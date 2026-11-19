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
