import importlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import init_database

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ai_comic_studio")

ROUTER_MODULES = [
    "app.routers.novels",
    "app.routers.studio",
    "app.routers.visual",
    "app.routers.story",
    "app.routers.media",
    "app.routers.video_editor",
]
_loaded_routers: list[str] = []
_router_errors: dict[str, str] = {}


def register_routers(application: FastAPI) -> None:
    """Register every required API router; never report a partial API as healthy."""
    for module_name in ROUTER_MODULES:
        try:
            module = importlib.import_module(module_name)
            router = getattr(module, "router", None) or getattr(module, "api_router", None)
            if router is None:
                raise RuntimeError(f"模块 {module_name} 没有导出 router")
            application.include_router(router)
            _loaded_routers.append(module_name)
            logger.info("路由注册成功：%s", module_name)
        except Exception as exc:
            _router_errors[module_name] = str(exc)
            logger.exception("必需路由加载失败：%s", module_name)


@asynccontextmanager
async def lifespan(application: FastAPI):
    missing = sorted(set(ROUTER_MODULES) - set(_loaded_routers))
    if missing:
        raise RuntimeError(
            "API 启动中止：以下必需路由未能加载：" +
            ", ".join(f"{name} ({_router_errors.get(name, 'unknown error')})" for name in missing)
        )
    init_database()
    logger.info("AI漫剧制作大师 API 已启动，路由数：%s", len(_loaded_routers))
    yield
    logger.info("AI漫剧制作大师 API 已停止")


app = FastAPI(
    title="AI漫剧制作大师 API",
    description="小说改编、AI创作、视觉素材、分镜、视频生产与剪辑",
    version="1.0.2",
    lifespan=lifespan,
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

register_routers(app)


@app.get("/")
def root():
    return {
        "application": "AI漫剧制作大师",
        "status": "running",
        "version": app.version,
    }


@app.get("/health")
def health():
    missing = sorted(set(ROUTER_MODULES) - set(_loaded_routers))
    healthy = not missing
    return {
        "status": "ok" if healthy else "degraded",
        "database": "initialized" if healthy else "not_ready",
        "loaded_routers": list(_loaded_routers),
        "missing_routers": missing,
        "router_errors": _router_errors,
    }


@app.get("/api/system/modules")
def system_modules():
    return {
        "loaded_routers": list(_loaded_routers),
        "available_routers": ROUTER_MODULES,
        "missing_routers": sorted(set(ROUTER_MODULES) - set(_loaded_routers)),
        "router_errors": _router_errors,
    }
