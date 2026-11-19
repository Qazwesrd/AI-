import os
import re
import shutil
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.database import DATABASE_PATH, STORAGE_DIR, engine, get_db
from app.models.task_runtime import TaskExecutionLease

router = APIRouter(prefix="/api/system/backups", tags=["数据备份与恢复"])

BACKUP_DIR = STORAGE_DIR / "backups" / "manual"
MAX_BACKUP_BYTES = 2 * 1024 * 1024 * 1024
SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,180}\.db$")


def _backup_sqlite(source_path: Path, destination: Path) -> None:
    if not source_path.is_file():
        raise FileNotFoundError("当前数据库文件不存在")
    destination.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(str(source_path), timeout=60)
    target = sqlite3.connect(str(destination), timeout=60)
    try:
        source.execute("PRAGMA wal_checkpoint(FULL)")
        source.backup(target)
        target.commit()
        result = target.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise RuntimeError("备份文件完整性检查失败")
    finally:
        target.close()
        source.close()


def _validate_sqlite_backup(path: Path) -> dict:
    if not path.is_file() or path.stat().st_size < 100:
        raise ValueError("备份文件不存在或文件过小")
    connection = None
    try:
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=30)
        integrity = connection.execute("PRAGMA quick_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise ValueError("SQLite 完整性检查未通过")
        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        known = {"novels", "projects", "ai_provider_configs", "media_files"}
        if not tables.intersection(known):
            raise ValueError("文件中没有识别到 AI 漫剧制作大师的数据表，已拒绝恢复")
        return {"tables": len(tables), "has_novels": "novels" in tables}
    except sqlite3.DatabaseError as exc:
        raise ValueError("文件不是有效的 SQLite 数据库") from exc
    finally:
        if connection is not None:
            connection.close()


def _create_manual_backup(prefix: str = "backup") -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    destination = BACKUP_DIR / f"{prefix}-{stamp}-{uuid.uuid4().hex[:8]}.db"
    _backup_sqlite(DATABASE_PATH, destination)
    return destination


@router.post("")
def create_backup(db: Session = Depends(get_db)):
    try:
        destination = _create_manual_backup()
        info = _validate_sqlite_backup(destination)
        return {
            "success": True,
            "name": destination.name,
            "size_bytes": destination.stat().st_size,
            "tables": info["tables"],
            "created_at": datetime.utcfromtimestamp(destination.stat().st_mtime).isoformat() + "Z",
            "message": "数据库备份已创建并通过完整性检查。"
        }
    except Exception as exc:
        raise HTTPException(500, "创建数据库备份失败：" + str(exc)[:1000]) from exc


@router.get("")
def list_backups():
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    for path in sorted(BACKUP_DIR.glob("*.db"), key=lambda item: item.stat().st_mtime, reverse=True):
        if not SAFE_NAME.fullmatch(path.name) or not path.is_file():
            continue
        rows.append({
            "name": path.name,
            "size_bytes": path.stat().st_size,
            "modified_at": datetime.utcfromtimestamp(path.stat().st_mtime).isoformat() + "Z",
        })
    return {"items": rows, "total": len(rows)}


@router.get("/{filename}/download")
def download_backup(filename: str):
    if not SAFE_NAME.fullmatch(filename):
        raise HTTPException(400, "备份文件名无效")
    path = (BACKUP_DIR / filename).resolve()
    if path.parent != BACKUP_DIR.resolve() or not path.is_file():
        raise HTTPException(404, "备份文件不存在")
    return FileResponse(path, filename=filename, media_type="application/vnd.sqlite3")


@router.post("/restore")
def restore_backup(
    backup_file: UploadFile = File(...),
    confirmation: str = Form(...),
    db: Session = Depends(get_db),
):
    if confirmation != "RESTORE":
        raise HTTPException(400, "恢复前必须在确认框中输入 RESTORE")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    staged = DATABASE_PATH.parent / f".restore-{uuid.uuid4().hex}.tmp"
    replacement = DATABASE_PATH.parent / f".database-{uuid.uuid4().hex}.tmp"
    total = 0
    try:
        active = db.query(TaskExecutionLease).filter(
            TaskExecutionLease.status == "active"
        ).count()
        if active:
            raise HTTPException(409, f"当前仍有 {active} 个任务正在执行，请等待任务结束后再恢复数据库")
        with staged.open("wb") as output:
            while True:
                chunk = backup_file.file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_BACKUP_BYTES:
                    raise HTTPException(413, "备份文件超过 2 GB 限制")
                output.write(chunk)
        _validate_sqlite_backup(staged)

        # 保留当前数据库的完整快照，作为恢复失败时的回滚点。
        safety_backup = _create_manual_backup("before-restore") if DATABASE_PATH.exists() else None
        db.close()
        if DATABASE_PATH.exists():
            try:
                current = sqlite3.connect(str(DATABASE_PATH), timeout=30)
                try:
                    current.execute("PRAGMA wal_checkpoint(FULL)")
                finally:
                    current.close()
            except sqlite3.DatabaseError:
                pass
        engine.dispose()
        for suffix in ("-wal", "-shm"):
            Path(str(DATABASE_PATH) + suffix).unlink(missing_ok=True)
        shutil.copyfile(staged, replacement)
        os.replace(replacement, DATABASE_PATH)
        engine.dispose()
        return {
            "success": True,
            "restored_bytes": total,
            "safety_backup": safety_backup.name if safety_backup else None,
            "message": "数据库恢复完成。当前数据已在恢复前自动备份；建议重新启动软件后继续使用。"
        }
    except HTTPException:
        raise
    except Exception as exc:
        try:
            if 'safety_backup' in locals() and safety_backup and safety_backup.is_file():
                engine.dispose()
                shutil.copyfile(safety_backup, replacement)
                os.replace(replacement, DATABASE_PATH)
                engine.dispose()
        except Exception:
            pass
        raise HTTPException(500, "数据库恢复失败，系统已尝试使用恢复前备份回滚：" + str(exc)[:1200]) from exc
    finally:
        staged.unlink(missing_ok=True)
        replacement.unlink(missing_ok=True)
        try:
            backup_file.file.close()
        except Exception:
            pass
