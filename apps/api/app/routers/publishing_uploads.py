import os
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

router = APIRouter(prefix="/api/publishing", tags=["Publishing Uploads"])

PROJECT_ROOT = Path(__file__).resolve().parents[4]
UPLOAD_ROOT = PROJECT_ROOT / "storage" / "publishing"
UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)

MAX_VIDEO_SIZE = 2 * 1024 * 1024 * 1024
MAX_COVER_SIZE = 20 * 1024 * 1024
CHUNK_SIZE = 1024 * 1024

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


@router.post("/uploads")
async def upload_publishing_file(
    file: UploadFile = File(...),
    kind: str = Form("video"),
):
    if kind not in {"video", "cover"}:
        raise HTTPException(status_code=400, detail="文件类型不正确")

    original_name = Path(file.filename or "").name
    extension = Path(original_name).suffix.lower()

    allowed = VIDEO_EXTENSIONS if kind == "video" else IMAGE_EXTENSIONS
    max_size = MAX_VIDEO_SIZE if kind == "video" else MAX_COVER_SIZE

    if extension not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式：{extension}",
        )

    upload_id = uuid.uuid4().hex
    destination_dir = UPLOAD_ROOT / kind
    destination_dir.mkdir(parents=True, exist_ok=True)

    destination = destination_dir / f"{upload_id}{extension}"
    temporary = destination.with_suffix(destination.suffix + ".uploading")

    total_size = 0

    try:
        with temporary.open("wb") as output:
            while True:
                chunk = await file.read(CHUNK_SIZE)
                if not chunk:
                    break

                total_size += len(chunk)

                if total_size > max_size:
                    raise HTTPException(
                        status_code=413,
                        detail="文件超过允许大小",
                    )

                output.write(chunk)

        if total_size == 0:
            raise HTTPException(status_code=400, detail="不能上传空文件")

        os.replace(temporary, destination)

        return {
            "success": True,
            "kind": kind,
            "filename": original_name,
            "size": total_size,
            "path": str(destination.resolve()),
            "file_id": upload_id,
        }

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"文件保存失败：{str(exc)}",
        )
    finally:
        await file.close()
        if temporary.exists():
            temporary.unlink(missing_ok=True)

