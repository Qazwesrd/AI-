import shutil
import sqlite3
import platform
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import DATABASE_PATH, STORAGE_DIR, get_db, get_schema_version
from app.models.studio import AIRuntimeSetting
from app.models.task_runtime import TaskExecutionLease, TaskQueueEntry
from app.services.media_service import _bundled_media_path

router = APIRouter(prefix="/api/system", tags=["系统诊断"])


def _check_database():
    if not DATABASE_PATH.is_file():
        return {"status": "error", "exists": False, "message": "数据库文件不存在"}
    try:
        connection = sqlite3.connect(str(DATABASE_PATH), timeout=10)
        try:
            result = connection.execute("PRAGMA quick_check").fetchone()
            tables = connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
            ).fetchone()[0]
        finally:
            connection.close()
        if not result or result[0] != "ok":
            return {"status": "error", "exists": True, "tables": tables,
                    "message": "SQLite 完整性检查未通过"}
        return {"status": "ok", "exists": True, "tables": tables,
                "size_bytes": DATABASE_PATH.stat().st_size,
                "schema_version": get_schema_version(),
                "message": "数据库可读取，完整性检查通过"}
    except Exception as exc:
        return {"status": "error", "exists": True, "message": str(exc)[:500]}


def _check_binary(name):
    try:
        path = _bundled_media_path(name)
        return {"status": "ok", "path": path, "message": "已找到"}
    except Exception as exc:
        return {"status": "error", "path": None, "message": str(exc)[:500]}



def _check_local_ai_tools():
    """Detect optional local AI runtimes without starting processes or using the network."""
    candidates = [
        {
            "id": "ollama",
            "name": "Ollama",
            "purpose": "本地文本模型运行环境",
            "commands": ["ollama"],
            "install_hint": "可从 Ollama 官方网站下载安装；安装和模型许可请自行核实。",
        },
        {
            "id": "llama_cpp",
            "name": "llama.cpp",
            "purpose": "本地 GGUF 文本模型运行环境",
            "commands": ["llama-cli", "main"],
            "install_hint": "可选择官方开源项目的预编译版本；模型文件需单独准备。",
        },
        {
            "id": "whisper_cpp",
            "name": "whisper.cpp",
            "purpose": "本地离线语音识别环境",
            "commands": ["whisper-cli", "main"],
            "install_hint": "可选择官方开源项目的预编译版本；语音模型文件需单独准备。",
        },
    ]
    results = []
    for item in candidates:
        executable = next((shutil.which(command) for command in item["commands"] if shutil.which(command)), None)
        results.append({
            "id": item["id"],
            "name": item["name"],
            "purpose": item["purpose"],
            "status": "installed" if executable else "not_detected",
            "executable": executable,
            "network_used": False,
            "auto_downloaded": False,
            "install_hint": item["install_hint"],
        })
    return {
        "mode": "local_only_detection",
        "network_used": False,
        "auto_downloaded": False,
        "tools": results,
        "note": "仅检查电脑 PATH 中是否存在本地命令，不启动程序、不连接网络、不下载模型。检测到软件不代表已安装模型或可以立即生成内容。",
    }

def _health_status(checks):
    values = [item.get("status") for item in checks]
    if "error" in values:
        return "error"
    if "warning" in values:
        return "warning"
    return "ok"


@router.get("/diagnostics")
def system_diagnostics(db: Session = Depends(get_db)):
    database = _check_database()
    ffmpeg = _check_binary("ffmpeg.exe")
    ffprobe = _check_binary("ffprobe.exe")
    try:
        usage = shutil.disk_usage(STORAGE_DIR)
        disk = {
            "status": "warning" if usage.free < 5 * 1024 ** 3 else "ok",
            "free_bytes": usage.free,
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "message": "剩余空间低于 5 GB，请清理磁盘或更换素材存储位置"
            if usage.free < 5 * 1024 ** 3 else "磁盘空间充足",
        }
    except Exception as exc:
        disk = {"status": "error", "message": str(exc)[:500]}

    runtime = db.query(AIRuntimeSetting).first()
    active_leases = db.query(TaskExecutionLease).filter(
        TaskExecutionLease.status == "active"
    ).count()
    queued_tasks = db.query(TaskQueueEntry).filter(
        TaskQueueEntry.status.in_(["waiting", "ready_to_run"])
    ).count()

    checks = [database, ffmpeg, ffprobe, disk]
    warnings = [
        item["message"] for item in checks
        if item.get("status") in ("warning", "error")
    ]
    return {
        "status": _health_status(checks),
        "application": "AI漫剧制作大师",
        "platform": platform.platform(),
        "python": platform.python_version(),
        "database": {**database, "path": str(DATABASE_PATH)},
        "storage": {"path": str(STORAGE_DIR), "disk": disk},
        "media_tools": {"ffmpeg": ffmpeg, "ffprobe": ffprobe},
        "ai_permissions": {
            "network_enabled": bool(runtime and runtime.network_enabled),
            "paid_api_allowed": bool(runtime and runtime.paid_api_allowed),
            "note": "这里只显示开关状态，不读取或返回 API Key。",
        },
        "tasks": {"active_leases": active_leases, "queued_tasks": queued_tasks},
        "warnings": warnings,
        "checked_components": len(checks),
    }
