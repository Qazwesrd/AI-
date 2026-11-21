"""Explicit, resumable download of the approved local Qwen model through Ollama.

The application never downloads a model until the user confirms the exact model and
the free-space check passes. Only the fixed local model catalog is accepted.
"""
import json
import os
import shutil
import threading
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Literal

router = APIRouter(prefix="/api/local-ai/model-installer", tags=["本地模型安装"])
OLLAMA_URL = "http://127.0.0.1:11434"
MODEL_NAME = "qwen3:4b"
MODEL_SIZE_BYTES = 2_500_000_000
REQUIRED_FREE_BYTES = 6 * 1024 ** 3
MODEL_PAGE = "https://ollama.com/library/qwen3:4b"
LICENSE_URL = "https://ollama.com/library/qwen3:4b"
_OPENER = build_opener(ProxyHandler({}))
_LOCK = threading.RLock()
_CANCEL = threading.Event()
_JOB = {
    "status": "idle", "model": MODEL_NAME, "stage": "尚未开始",
    "completed_bytes": 0, "total_bytes": MODEL_SIZE_BYTES,
    "percent": 0, "error": None, "updated": False,
}
_RESPONSE = None


class StartModelInstallInput(BaseModel):
    model: Literal["qwen3:4b"] = MODEL_NAME
    confirm_download: bool = False


def _ollama_models():
    request = Request(OLLAMA_URL + "/api/tags", headers={"Accept": "application/json"})
    try:
        with _OPENER.open(request, timeout=4) as response:
            payload = json.loads(response.read(2 * 1024 * 1024).decode("utf-8"))
        return payload.get("models", []) if isinstance(payload, dict) else []
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        return None


def _model_directory():
    configured = os.environ.get("OLLAMA_MODELS")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".ollama" / "models"


def _model_installed():
    models = _ollama_models()
    if models is None:
        return None
    return any(
        isinstance(item, dict)
        and (item.get("name") == MODEL_NAME or item.get("model") == MODEL_NAME)
        for item in models
    )


@router.get("/info")
def model_install_info():
    models = _ollama_models()
    model_dir = _model_directory()
    disk_root = model_dir
    while not disk_root.exists() and disk_root != disk_root.parent:
        disk_root = disk_root.parent
    try:
        free_bytes = shutil.disk_usage(disk_root).free
    except OSError:
        free_bytes = None
    installed = None if models is None else any(
        isinstance(item, dict) and (item.get("name") == MODEL_NAME or item.get("model") == MODEL_NAME)
        for item in models
    )
    with _LOCK:
        job = dict(_JOB)
    return {
        "engine_status": "ready" if models is not None else "not_running",
        "model": MODEL_NAME,
        "model_size_bytes": MODEL_SIZE_BYTES,
        "required_free_bytes": REQUIRED_FREE_BYTES,
        "free_bytes": free_bytes,
        "enough_space": free_bytes is not None and free_bytes >= REQUIRED_FREE_BYTES,
        "model_directory": str(model_dir),
        "installed": installed,
        "download_source": MODEL_PAGE,
        "license": "Apache-2.0 (请在下载前自行阅读模型条款)",
        "license_url": LICENSE_URL,
        "job": job,
        "cloud_fallback": False,
        "automatic_download": False,
    }


def _pull_worker():
    global _RESPONSE
    request = Request(
        OLLAMA_URL + "/api/pull",
        data=json.dumps({"model": MODEL_NAME, "stream": True}).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/x-ndjson"},
        method="POST",
    )
    try:
        response = _OPENER.open(request, timeout=30)
        with _LOCK:
            _RESPONSE = response
        while True:
            if _CANCEL.is_set():
                response.close()
                with _LOCK:
                    _JOB.update(status="cancelled", stage="已取消；Ollama 支持下次继续下载", error=None)
                return
            line = response.readline()
            if not line:
                break
            try:
                update = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                continue
            with _LOCK:
                _JOB["stage"] = str(update.get("status") or "正在下载")[:160]
                total = update.get("total")
                completed = update.get("completed")
                if isinstance(total, int) and total > 0:
                    _JOB["total_bytes"] = total
                if isinstance(completed, int) and completed >= 0:
                    _JOB["completed_bytes"] = completed
                total_now = _JOB["total_bytes"]
                _JOB["percent"] = min(99, int(_JOB["completed_bytes"] * 100 / total_now)) if total_now else 0
                if update.get("error"):
                    raise RuntimeError(str(update["error"])[:500])
                if update.get("status") == "success":
                    _JOB.update(status="verifying", stage="模型下载完成，正在核验本机模型清单")
        response.close()
        installed = _model_installed()
        with _LOCK:
            if _CANCEL.is_set():
                _JOB.update(status="cancelled", stage="已取消；可再次开始并续传")
            elif installed:
                _JOB.update(status="success", stage="模型已安装并通过本机清单核验", percent=100, error=None)
            else:
                _JOB.update(status="failed", stage="下载结束但未在本机模型清单中找到目标模型", error="请检查 Ollama 状态后重试")
    except Exception as exc:
        with _LOCK:
            if _CANCEL.is_set():
                _JOB.update(status="cancelled", stage="已取消；可再次开始并续传", error=None)
            else:
                _JOB.update(status="failed", stage="模型下载失败", error=str(exc)[:500])
    finally:
        with _LOCK:
            _RESPONSE = None


@router.post("/start")
def start_model_install(payload: StartModelInstallInput):
    global _RESPONSE
    if not payload.confirm_download:
        raise HTTPException(400, "必须先明确确认模型下载、网络流量和许可证信息。")
    models = _ollama_models()
    if models is None:
        raise HTTPException(503, "本机尚未运行 Ollama。请先安装并启动官方 Ollama，再回来安装模型。")
    if any(isinstance(item, dict) and (item.get("name") == MODEL_NAME or item.get("model") == MODEL_NAME) for item in models):
        return {"status": "already_installed", "model": MODEL_NAME, "network_used": False, "cloud_fallback": False}
    info = model_install_info()
    if not info["enough_space"]:
        raise HTTPException(507, "模型安装需要至少 6 GiB 可用磁盘空间；请清理磁盘或更改 Ollama 模型存储位置。")
    with _LOCK:
        if _JOB["status"] in {"starting", "downloading", "verifying"}:
            raise HTTPException(409, "已有模型下载任务正在运行。")
        _CANCEL.clear()
        _JOB.update(
            status="downloading", model=MODEL_NAME, stage="正在连接 Ollama 官方模型库",
            completed_bytes=0, total_bytes=MODEL_SIZE_BYTES, percent=0, error=None,
            updated=True,
        )
        worker = threading.Thread(target=_pull_worker, name="local-qwen-model-pull", daemon=True)
        worker.start()
    return {
        "status": "downloading", "model": MODEL_NAME,
        "network_used": True, "cloud_fallback": False,
        "message": "已按你的确认开始下载；下载可续传，不会调用云端 AI API。",
    }


@router.get("/status")
def model_install_status():
    with _LOCK:
        result = dict(_JOB)
    result.update({"network_used": result["status"] in {"downloading", "verifying", "success"}, "cloud_fallback": False})
    return result


@router.post("/cancel")
def cancel_model_install():
    with _LOCK:
        if _JOB["status"] not in {"starting", "downloading", "verifying"}:
            return {"status": _JOB["status"], "cancelled": False}
        _CANCEL.set()
        response = _RESPONSE
    if response is not None:
        try:
            response.close()
        except OSError:
            pass
    return {"status": "cancelling", "cancelled": True, "message": "正在停止下载；Ollama 可在下次安装时续传。"}
