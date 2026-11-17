from app.routers.publishing import router as publishing_router
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
    "app.routers.ai_novel_analysis",
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
                raise RuntimeError(f"妯″潡 {module_name} 娌℃湁瀵煎嚭 router")
            application.include_router(router)
            _loaded_routers.append(module_name)
            logger.info("璺敱娉ㄥ唽鎴愬姛锛?s", module_name)
        except Exception as exc:
            _router_errors[module_name] = str(exc)
            logger.exception("蹇呴渶璺敱鍔犺浇澶辫触锛?s", module_name)


@asynccontextmanager
async def lifespan(application: FastAPI):
    missing = sorted(set(ROUTER_MODULES) - set(_loaded_routers))
    if missing:
        raise RuntimeError(
            "API 鍚姩涓锛氫互涓嬪繀闇€璺敱鏈兘鍔犺浇锛? +
            ", ".join(f"{name} ({_router_errors.get(name, 'unknown error')})" for name in missing)
        )
    init_database()
    logger.info("AI婕墽鍒朵綔澶у笀 API 宸插惎鍔紝璺敱鏁帮細%s", len(_loaded_routers))
    yield
    logger.info("AI婕墽鍒朵綔澶у笀 API 宸插仠姝?)


app = FastAPI(
    title="AI婕墽鍒朵綔澶у笀 API",
    description="灏忚鏀圭紪銆丄I鍒涗綔銆佽瑙夌礌鏉愩€佸垎闀溿€佽棰戠敓浜т笌鍓緫",
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
        "application": "AI婕墽鍒朵綔澶у笀",
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


app.include_router(publishing_router)

