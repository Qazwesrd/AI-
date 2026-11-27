import os
import sqlite3
import shutil
from datetime import datetime
from pathlib import Path
from importlib import import_module

from sqlalchemy import create_engine, Table, Column, Integer, String, select
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()

SCHEMA_VERSION = 1
schema_migrations = Table(
    "schema_migrations",
    Base.metadata,
    Column("version", Integer, primary_key=True),
    Column("name", String(160), nullable=False),
    Column("applied_at", String(40), nullable=False),
)

APP_ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = Path(os.environ.get("APP_DATA_DIR", str(APP_ROOT))).expanduser().resolve()
ROOT_DIR = DATA_ROOT
STORAGE_DIR = DATA_ROOT / "storage"
STORAGE_DIR.mkdir(parents=True, exist_ok=True)


def resolve_database_path():
    """Resolve the database without abandoning databases created by older builds."""
    explicit = os.environ.get("AI_COMIC_DATABASE_PATH")
    if explicit:
        return Path(explicit).expanduser().resolve()

    current = STORAGE_DIR / "ai_comic_studio.db"
    if current.exists():
        return current

    legacy_candidates = [
        APP_ROOT / "apps" / "api" / "novel_studio.db",
        APP_ROOT / "apps" / "api" / "ai_novel_studio.db",
        APP_ROOT / "apps" / "api" / "novel_studio_ai.db",
        APP_ROOT / "novel_studio.db",
        APP_ROOT / "ai_novel_studio.db",
        APP_ROOT / "novel_studio_ai.db",
    ]

    for legacy in legacy_candidates:
        if legacy.exists():
            print(f"[数据库兼容] 使用现有旧数据库：{legacy}")
            return legacy

    return current


DATABASE_PATH = resolve_database_path()
DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    f"sqlite:///{DATABASE_PATH.as_posix()}",
    connect_args={"check_same_thread": False, "timeout": 30},
    future=True,
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


MODEL_MODULES = [
    "app.models.novel",
    "app.models.studio",
    "app.models.visual",
    "app.models.story",
    "app.models.media",
    "app.models.editing",
    "app.models.task_runtime",
    "app.models.web_ai_tool",
    "app.models.ai_story_collaboration",
    "app.models.prompts",
]


def _backup_database_before_migration():
    if not DATABASE_PATH.exists() or DATABASE_PATH.stat().st_size == 0:
        return None

    backup_dir = STORAGE_DIR / "backups" / "schema"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S-%f")
    target = backup_dir / f"{DATABASE_PATH.stem}-before-migration-{stamp}.db"

    source = sqlite3.connect(str(DATABASE_PATH), timeout=30)
    destination = sqlite3.connect(str(target), timeout=30)
    try:
        source.backup(destination)
        destination.commit()
    finally:
        destination.close()
        source.close()

    return target



def _restore_database_backup(backup_path: Path):
    """Restore a pre-migration SQLite snapshot without replacing an open Windows file."""
    if not backup_path or not Path(backup_path).is_file():
        raise RuntimeError("找不到迁移前数据库备份，无法自动回滚")

    engine.dispose()
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Replacing an open SQLite file with os.replace is unreliable on Windows.
    # SQLite's backup API restores the snapshot into the existing database file
    # and handles the destination journal safely.
    source = sqlite3.connect(str(backup_path), timeout=30)
    destination = sqlite3.connect(str(DATABASE_PATH), timeout=30)
    try:
        destination.execute("PRAGMA journal_mode=DELETE")
        source.backup(destination)
        destination.commit()
        integrity = destination.execute("PRAGMA integrity_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise RuntimeError(f"恢复后的数据库完整性检查失败：{integrity}")
    finally:
        destination.close()
        source.close()

    for suffix in ("-wal", "-shm"):
        Path(str(DATABASE_PATH) + suffix).unlink(missing_ok=True)

    engine.dispose()


def _pending_schema_changes():
    """Return missing model tables and columns before any schema mutation."""
    from sqlalchemy import inspect

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    missing_tables = []
    missing_columns = []

    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            missing_tables.append(table.name)
            continue

        existing_columns = {
            column["name"] for column in inspector.get_columns(table.name)
        }
        for column in table.columns:
            if column.name not in existing_columns:
                missing_columns.append(f"{table.name}.{column.name}")

    return missing_tables, missing_columns


def _migrate_missing_columns(backup_path=None):
    """Safely add compatible ORM columns to existing SQLite databases."""
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    missing = []

    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue

        existing_columns = {
            column["name"] for column in inspector.get_columns(table.name)
        }

        for column in table.columns:
            if column.name in existing_columns:
                continue

            if column.primary_key or column.foreign_keys:
                raise RuntimeError(
                    f"无法安全自动迁移表 {table.name} 的结构列 {column.name}；"
                    "请使用正式迁移脚本。"
                )

            if not column.nullable and column.server_default is None:
                python_default = column.default.arg if column.default is not None else None
                is_datetime_default = (
                    python_default is not None
                    and callable(python_default)
                    and column.type.python_type.__name__ in {"datetime", "date"}
                )
                if (
                    python_default is None
                    or (
                        not isinstance(python_default, (str, int, float))
                        and not is_datetime_default
                    )
                ):
                    raise RuntimeError(
                        f"数据库迁移拒绝添加必填字段 {table.name}.{column.name}："
                        "该字段没有可安全转换为 SQLite 默认值的默认值。"
                    )

            missing.append((table, column))

    if not missing:
        return []

    backup = backup_path or _backup_database_before_migration()

    try:
        with engine.begin() as connection:
            for table, column in missing:
                column_type = column.type.compile(dialect=engine.dialect)
                clause = (
                    f'ALTER TABLE "{table.name}" '
                    f'ADD COLUMN "{column.name}" {column_type}'
                )

                default_value = column.server_default.arg if column.server_default is not None else (
                    column.default.arg if column.default is not None else None
                )
                if default_value is not None:
                    if isinstance(default_value, str):
                        clause += " DEFAULT '" + default_value.replace("'", "''") + "'"
                    elif isinstance(default_value, (int, float)):
                        clause += f" DEFAULT {default_value}"
                    elif callable(default_value) and column.type.python_type.__name__ in {"datetime", "date"}:
                        clause += " DEFAULT CURRENT_TIMESTAMP"

                connection.execute(text(clause))
    except Exception:
        print(f"[数据库迁移失败] 已保留迁移前备份：{backup}")
        raise

    print(
        f"[数据库迁移] 新增 {len(missing)} 个字段；"
        f"迁移前备份：{backup}"
    )
    return [f"{table.name}.{column.name}" for table, column in missing]



def get_schema_version():
    from sqlalchemy import inspect
    if not inspect(engine).has_table("schema_migrations"):
        return 0
    with engine.connect() as connection:
        value = connection.execute(
            select(schema_migrations.c.version).order_by(schema_migrations.c.version.desc()).limit(1)
        ).scalar()
    return int(value or 0)


def _record_schema_version():
    with engine.begin() as connection:
        current = connection.execute(
            select(schema_migrations.c.version).order_by(schema_migrations.c.version.desc()).limit(1)
        ).scalar()
        current = int(current or 0)
        if current > SCHEMA_VERSION:
            raise RuntimeError(
                f"数据库结构版本 {current} 高于当前软件支持版本 {SCHEMA_VERSION}，"
                "为防止降级破坏数据，已停止启动。"
            )
        if current < 1:
            connection.execute(schema_migrations.insert().values(
                version=1,
                name="baseline_schema",
                applied_at=datetime.utcnow().isoformat(timespec="seconds") + "Z",
            ))

def init_database():
    print("正在注册数据库模型……")

    for module_name in MODEL_MODULES:
        try:
            import_module(module_name)
            print(f"[模型已注册] {module_name}")
        except ModuleNotFoundError as exc:
            if exc.name == module_name:
                print(f"[跳过不存在的模型] {module_name}")
            else:
                print(f"[模型依赖错误] {module_name}: {exc}")
                raise

    # Snapshot an existing database before create_all can add any tables.
    # The old order backed up only after create_all, which meant the backup
    # was not a faithful pre-migration copy when a release introduced tables.
    missing_tables, missing_columns = _pending_schema_changes()
    backup = None
    if missing_tables or missing_columns:
        backup = _backup_database_before_migration()
        if backup:
            print(
                f"[数据库迁移] 已在结构变更前创建完整备份：{backup} "
                f"(新增表 {len(missing_tables)}，新增字段 {len(missing_columns)})"
            )

    database_existed_before = DATABASE_PATH.exists() and DATABASE_PATH.stat().st_size > 0
    try:
        Base.metadata.create_all(bind=engine)
        _migrate_missing_columns(backup_path=backup)
        _record_schema_version()
    except Exception as migration_error:
        rollback_message = ""
        try:
            if backup and Path(backup).is_file():
                _restore_database_backup(backup)
                rollback_message = f"；已自动恢复迁移前数据库备份：{backup}"
            elif not database_existed_before:
                engine.dispose()
                DATABASE_PATH.unlink(missing_ok=True)
                for suffix in ("-wal", "-shm"):
                    Path(str(DATABASE_PATH) + suffix).unlink(missing_ok=True)
                rollback_message = "；本次新建的空数据库已清理"
        except Exception as rollback_error:
            rollback_message = f"；自动回滚也失败，请保留备份 {backup} 并联系维护：{rollback_error}"
        raise RuntimeError(
            f"数据库结构升级失败：{migration_error}{rollback_message}"
        ) from migration_error

    print(f"[数据库初始化完成] {DATABASE_PATH}")
    return True


# Publishing models registration
import app.models.publishing
