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
    "app.routers.subtitle_tools",
    "app.routers.backups",
    "app.routers.system",
    "app.routers.local_models",
    "app.routers.local_ai_runtime",
    "app.routers.local_model_installer",
    "app.routers.local_model_hardware",
    "app.routers.local_image_generation",
    "app.routers.local_image_runtime",
    "app.routers.comfyui_installer",
    "app.routers.image_model_catalog",
    "app.routers.workspace_backups",
    "app.routers.ai_novel_analysis",
    "app.routers.publishing",
    "app.routers.publishing_uploads",
    "app.routers.tasks",
    "app.routers.web_ai_tools",
    "app.routers.ai_collaboration",
]

_loaded_routers: list[str] = []
_router_errors: dict[str, str] = {}


def register_routers(application: FastAPI) -> None:
    _loaded_routers.clear()
    _router_errors.clear()
    for module_name in ROUTER_MODULES:
        try:
            module = importlib.import_module(module_name)
            router = getattr(module, "router", None) or getattr(module, "api_router", None)
            if router is None:
                raise RuntimeError(f"Module {module_name} does not export a router")
            application.include_router(router)
            _loaded_routers.append(module_name)
            logger.info("Router registered: %s", module_name)
        except Exception as exc:
            _router_errors[module_name] = str(exc)
            logger.exception("Router loading failed: %s", module_name)


@asynccontextmanager
async def lifespan(application: FastAPI):
    missing = sorted(set(ROUTER_MODULES) - set(_loaded_routers))
    if missing:
        details = "; ".join(
            f"{name}: {_router_errors.get(name, 'unknown error')}" for name in missing
        )
        raise RuntimeError("API startup aborted because required routers failed: " + details)
    init_database()
    from app.services.task_runtime import inspect_startup_leases
    lease_scan = inspect_startup_leases()
    if lease_scan["stale_count"]:
        logger.warning("Task runtime startup scan found %s stale leases (active=%s); none were auto-released.", lease_scan["stale_count"], lease_scan["active_count"])
    elif lease_scan["active_count"]:
        logger.warning("Task runtime startup scan found %s active lease(s) from a previous process; none were auto-released.", lease_scan["active_count"])
    logger.info("AI Comic Studio API started. Registered routers: %s", len(_loaded_routers))
    try:
        yield
    finally:
        # A ComfyUI process launched by this API must not be orphaned when the
        # desktop application shuts down. Cleanup uses only its owned Popen handle.
        try:
            from app.routers.local_image_runtime import shutdown_managed_comfyui
            shutdown_managed_comfyui()
        except Exception:
            logger.exception("Failed to stop the application-managed ComfyUI process.")
        logger.info("AI Comic Studio API stopped.")


app = FastAPI(
    title="AI漫剧制作大师 API",
    description="小说分析、AI创作、视觉素材、分镜、视频制作与发布",
    version="1.0.5",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000", "http://127.0.0.1:3000",
        "http://localhost:3001", "http://127.0.0.1:3001",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_routers(app)


@app.get("/")
def root():
    return {"application": "AI漫剧制作大师", "status": "running", "version": app.version}


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
