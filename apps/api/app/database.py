import os
import sqlite3
from datetime import datetime
from pathlib import Path
from importlib import import_module

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()

APP_ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = Path(os.environ.get("APP_DATA_DIR", str(APP_ROOT)))
ROOT_DIR = DATA_ROOT
STORAGE_DIR = DATA_ROOT / "storage"
STORAGE_DIR.mkdir(parents=True, exist_ok=True)

def resolve_database_path():
    """Keep existing user databases when the application database filename changes."""
    explicit = os.environ.get("AI_COMIC_DATABASE_PATH")
    if explicit:
        return Path(explicit).expanduser().resolve()

    current = STORAGE_DIR / "ai_comic_studio.db"
    if current.exists():
        return current

    for legacy_name in ("novel_studio.db", "ai_novel_studio.db", "novel_studio_ai.db"):
        legacy = STORAGE_DIR / legacy_name
        if legacy.exists():
            print(f"[数据库兼容] 使用现有旧数据库：{legacy}")
            return legacy
    return current


DATABASE_PATH = resolve_database_path()

engine = create_engine(
    f"sqlite:///{DATABASE_PATH.as_posix()}",
    connect_args={"check_same_thread": False},
    future=True
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
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
]


def _backup_database_before_migration():
    if not DATABASE_PATH.exists() or DATABASE_PATH.stat().st_size == 0:
        return None
    backup_dir = STORAGE_DIR / "backups" / "schema"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S-%f")
    target = backup_dir / f"{DATABASE_PATH.stem}-before-migration-{stamp}.db"
    source = sqlite3.connect(str(DATABASE_PATH))
    destination = sqlite3.connect(str(target))
    try:
        source.backup(destination)
        destination.commit()
    finally:
        destination.close()
        source.close()
    return target


def _migrate_missing_columns():
    """Safely add columns introduced by newer ORM models to old SQLite DBs."""
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    missing = []
    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        existing_columns = {column["name"] for column in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in existing_columns:
                continue
            if column.primary_key or column.foreign_keys:
                raise RuntimeError(
                    f"无法安全自动迁移表 {table.name} 的结构列 {column.name}；请使用正式迁移脚本。"
                )
            missing.append((table, column))

    if not missing:
        return []

    backup = _backup_database_before_migration()
    with engine.begin() as connection:
        for table, column in missing:
            column_type = column.type.compile(dialect=engine.dialect)
            clause = f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {column_type}'
            if column.server_default is not None:
                clause += f" DEFAULT {column.server_default.arg}"
            connection.execute(text(clause))

    print(f"[数据库迁移] 新增 {len(missing)} 个字段；迁移前备份：{backup}")
    return [f"{table.name}.{column.name}" for table, column in missing]


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

    Base.metadata.create_all(bind=engine)
    _migrate_missing_columns()
    print(f"[数据库初始化完成] {DATABASE_PATH}")

    return True

# Publishing models registration
import app.models.publishing

