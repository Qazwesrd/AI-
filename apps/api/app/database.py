import os
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

DATABASE_PATH = STORAGE_DIR / "ai_comic_studio.db"

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
]


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
    print(f"[数据库初始化完成] {DATABASE_PATH}")

    return True

# Publishing models registration
import app.models.publishing

