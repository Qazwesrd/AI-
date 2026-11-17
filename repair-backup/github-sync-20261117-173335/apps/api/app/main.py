import importlib
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import init_database

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ai_comic_studio")

app = FastAPI(
    title="AI漫剧制作大师 API",
    description="小说改编、AI创作、视觉素材、分镜、视频生产与剪辑",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3001",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ROUTER_MODULES = [
    "app.routers.novels",
    "app.routers.studio",
    "app.routers.visual",
    "app.routers.story",
    "app.routers.media",
    "app.routers.video_editor",
]

_loaded_routers = []


def register_routers():
    for module_name in ROUTER_MODULES:
        try:
            module = importlib.import_module(module_name)
            router = getattr(module, "router", None)

            if router is None:
                router = getattr(module, "api_router", None)

            if router is None:
                logger.warning("模块没有找到 router：%s", module_name)
                continue

            app.include_router(router)
            _loaded_routers.append(module_name)
            logger.info("路由注册成功：%s", module_name)

        except ModuleNotFoundError as exc:
            if exc.name == module_name:
                logger.warning("路由模块不存在，跳过：%s", module_name)
            else:
                logger.exception("路由依赖导入失败：%s", module_name)

        except Exception:
            logger.exception("路由加载失败：%s", module_name)


register_routers()


@app.on_event("startup")
def startup_event():
    init_database()
    logger.info("AI漫剧制作大师 API 已启动")


@app.get("/")
def root():
    return {
        "application": "AI漫剧制作大师",
        "status": "running",
        "version": "1.0.0"
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "database": "initialized",
        "loaded_routers": _loaded_routers
    }


@app.get("/api/system/modules")
def system_modules():
    return {
        "loaded_routers": _loaded_routers,
        "available_routers": ROUTER_MODULES
    }
