import json
import os
import shutil
import stat
import uuid
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.database import DATABASE_PATH, STORAGE_DIR, DATA_ROOT, engine, get_db
from app.models.task_runtime import TaskExecutionLease
from app.routers.backups import _backup_sqlite, _validate_sqlite_backup

router = APIRouter(prefix="/api/system/workspace-backups", tags=["完整工作空间归档"])

WORKSPACE_BACKUP_DIR = DATA_ROOT / "workspace_backups"
MAX_ARCHIVE_UPLOAD_BYTES = 100 * 1024 * 1024 * 1024
MAX_ARCHIVE_UNPACKED_BYTES = 200 * 1024 * 1024 * 1024
MAX_ARCHIVE_FILES = 200000


def _is_inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _create_workspace_archive(prefix="workspace"):
    if not DATABASE_PATH.is_file():
        raise FileNotFoundError("当前数据库文件不存在")
    WORKSPACE_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    destination = WORKSPACE_BACKUP_DIR / f"{prefix}-{stamp}-{uuid.uuid4().hex[:8]}.zip"
    snapshot = WORKSPACE_BACKUP_DIR / f".database-{uuid.uuid4().hex}.db"
    try:
        _backup_sqlite(DATABASE_PATH, snapshot)
        file_count = 0
        storage_bytes = 0
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.write(snapshot, "database/current.db")
            if STORAGE_DIR.exists():
                for path in STORAGE_DIR.rglob("*"):
                    if not path.is_file():
                        continue
                    resolved = path.resolve()
                    if _is_inside(resolved, STORAGE_DIR / "backups"):
                        continue
                    if resolved == DATABASE_PATH.resolve():
                        continue
                    relative = path.relative_to(STORAGE_DIR).as_posix()
                    archive.write(path, "storage/" + relative)
                    file_count += 1
                    storage_bytes += path.stat().st_size
            manifest = {
                "application": "AI漫剧制作大师",
                "format_version": 1,
                "created_at": datetime.utcnow().isoformat() + "Z",
                "database_snapshot": "database/current.db",
                "storage_file_count": file_count,
                "storage_bytes": storage_bytes,
                "includes_local_encryption_key": (STORAGE_DIR / "secrets" / "local.key").is_file(),
                "notice": "该归档包含本地素材文件和可能存在的 API 密钥加密密钥，请妥善保管。",
            }
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        with zipfile.ZipFile(destination, "r") as archive:
            bad_file = archive.testzip()
            if bad_file:
                raise RuntimeError("归档完整性检查失败：" + bad_file)
        return destination, manifest
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        snapshot.unlink(missing_ok=True)


def _validate_workspace_archive(path: Path):
    if not path.is_file() or path.stat().st_size < 100:
        raise ValueError("归档文件不存在或文件过小")
    try:
        with zipfile.ZipFile(path, "r") as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ARCHIVE_FILES:
                raise ValueError("归档文件数量超过允许上限")
            total = 0
            names = set()
            for info in infos:
                name = info.filename
                parsed = PurePosixPath(name)
                mode = (info.external_attr >> 16) & 0xFFFF
                if (not name or name.startswith("/") or "\\" in name
                        or ":" in name or ".." in parsed.parts
                        or stat.S_ISLNK(mode)):
                    raise ValueError("归档包含不安全的文件路径或符号链接")
                total += info.file_size
                if total > MAX_ARCHIVE_UNPACKED_BYTES:
                    raise ValueError("归档解压后超过 200 GB 限制")
                names.add(name)
            if "database/current.db" not in names:
                raise ValueError("归档缺少数据库快照")
            if "manifest.json" not in names:
                raise ValueError("归档缺少清单文件")
            bad_file = archive.testzip()
            if bad_file:
                raise ValueError("归档文件损坏：" + bad_file)
            return {"file_count": len(infos), "unpacked_bytes": total}
    except zipfile.BadZipFile as exc:
        raise ValueError("文件不是有效的 ZIP 工作空间归档") from exc


def _extract_workspace_archive(archive_path: Path, destination: Path):
    storage_stage = destination / "storage"
    database_stage = destination / "current.db"
    storage_stage.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path, "r") as archive:
        for info in archive.infolist():
            if info.is_dir() or info.filename == "manifest.json":
                continue
            if info.filename == "database/current.db":
                target = database_stage
            elif info.filename.startswith("storage/"):
                relative = PurePosixPath(info.filename).relative_to("storage")
                target = storage_stage.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
            else:
                continue
            if not _is_inside(target, destination):
                raise ValueError("归档目标路径超出临时目录")
            with archive.open(info, "r") as source, target.open("wb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
    _validate_sqlite_backup(database_stage)
    return storage_stage, database_stage


def _restore_database_from_workspace_archive(archive_path: Path, target_path: Path):
    temporary = target_path.with_name(target_path.name + f".rollback-{uuid.uuid4().hex}.tmp")
    with zipfile.ZipFile(archive_path, "r") as archive:
        with archive.open("database/current.db", "r") as source, temporary.open("wb") as output:
            shutil.copyfileobj(source, output, length=1024 * 1024)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    os.replace(temporary, target_path)


@router.post("")
def create_workspace_backup():
    try:
        destination, manifest = _create_workspace_archive()
        return {
            "success": True,
            "name": destination.name,
            "size_bytes": destination.stat().st_size,
            "storage_file_count": manifest["storage_file_count"],
            "includes_local_encryption_key": manifest["includes_local_encryption_key"],
            "message": "完整工作空间归档已创建并通过 ZIP 完整性检查。",
        }
    except Exception as exc:
        raise HTTPException(500, "创建完整工作空间归档失败：" + str(exc)[:1200]) from exc


@router.get("")
def list_workspace_backups():
    WORKSPACE_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    items = []
    for path in sorted(WORKSPACE_BACKUP_DIR.glob("*.zip"), key=lambda item: item.stat().st_mtime, reverse=True):
        items.append({
            "name": path.name,
            "size_bytes": path.stat().st_size,
            "modified_at": datetime.utcfromtimestamp(path.stat().st_mtime).isoformat() + "Z",
        })
    return {"items": items, "total": len(items)}


@router.get("/{filename}/download")
def download_workspace_backup(filename: str):
    if not filename.endswith(".zip") or Path(filename).name != filename:
        raise HTTPException(400, "归档文件名无效")
    path = (WORKSPACE_BACKUP_DIR / filename).resolve()
    if path.parent != WORKSPACE_BACKUP_DIR.resolve() or not path.is_file():
        raise HTTPException(404, "工作空间归档不存在")
    return FileResponse(path, filename=filename, media_type="application/zip")


@router.post("/restore")
def restore_workspace_backup(
    archive_file: UploadFile = File(...),
    confirmation: str = Form(...),
    db: Session = Depends(get_db),
):
    if confirmation != "RESTORE WORKSPACE":
        raise HTTPException(400, "恢复前必须输入 RESTORE WORKSPACE")
    WORKSPACE_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    uploaded = WORKSPACE_BACKUP_DIR / f".upload-{uuid.uuid4().hex}.zip"
    staging_root = WORKSPACE_BACKUP_DIR / f".restore-stage-{uuid.uuid4().hex}"
    old_storage = STORAGE_DIR.with_name(STORAGE_DIR.name + f".pre-restore-{uuid.uuid4().hex}")
    new_storage_installed = False
    old_storage_moved = False
    safety_archive = None
    temporary_database = None
    includes_local_key = False
    total = 0
    try:
        active = db.query(TaskExecutionLease).filter(TaskExecutionLease.status == "active").count()
        if active:
            raise HTTPException(409, f"当前仍有 {active} 个任务正在执行，请等待任务结束后再恢复")
        with uploaded.open("wb") as output:
            while True:
                chunk = archive_file.file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_ARCHIVE_UPLOAD_BYTES:
                    raise HTTPException(413, "工作空间归档超过 100 GB 上传限制")
                output.write(chunk)
        _validate_workspace_archive(uploaded)
        with zipfile.ZipFile(uploaded, "r") as archive:
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            includes_local_key = bool(manifest.get("includes_local_encryption_key"))
        staging_root.mkdir(parents=True, exist_ok=True)
        staged_storage, staged_database = _extract_workspace_archive(uploaded, staging_root)
        safety_archive, _ = _create_workspace_archive("before-restore")

        db.close()
        engine.dispose()
        if STORAGE_DIR.exists():
            os.replace(STORAGE_DIR, old_storage)
            old_storage_moved = True
        os.replace(staged_storage, STORAGE_DIR)
        new_storage_installed = True

        temporary_database = DATABASE_PATH.with_name(DATABASE_PATH.name + f".restore-{uuid.uuid4().hex}.tmp")
        DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(staged_database, temporary_database)
        os.replace(temporary_database, DATABASE_PATH)
        engine.dispose()
        _validate_sqlite_backup(DATABASE_PATH)

        if old_storage.exists():
            shutil.rmtree(old_storage, ignore_errors=True)
        return {
            "success": True,
            "restored_bytes": total,
            "safety_archive": safety_archive.name if safety_archive else None,
            "includes_local_encryption_key": includes_local_key,
            "message": (
                "完整工作空间恢复成功。数据库和素材文件已恢复。"
                + ("本地 AI 密钥加密文件也已包含。" if includes_local_key else "该归档未包含本地 AI 密钥加密文件，AI 服务商密钥需要重新保存。")
                + "建议重新启动软件。"
            ),
        }
    except HTTPException:
        raise
    except Exception as exc:
        rollback_errors = []
        try:
            engine.dispose()
            if new_storage_installed and STORAGE_DIR.exists():
                shutil.rmtree(STORAGE_DIR, ignore_errors=True)
            if old_storage_moved and old_storage.exists():
                os.replace(old_storage, STORAGE_DIR)
            if safety_archive and safety_archive.is_file():
                _restore_database_from_workspace_archive(safety_archive, DATABASE_PATH)
            engine.dispose()
        except Exception as rollback_error:
            rollback_errors.append(str(rollback_error))
        suffix = ("；回滚错误：" + "；".join(rollback_errors)) if rollback_errors else "；已尝试恢复操作前的工作空间"
        raise HTTPException(500, "工作空间恢复失败：" + str(exc)[:1000] + suffix) from exc
    finally:
        uploaded.unlink(missing_ok=True)
        if temporary_database:
            temporary_database.unlink(missing_ok=True)
        shutil.rmtree(staging_root, ignore_errors=True)
        try:
            archive_file.file.close()
        except Exception:
            pass
