from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel, Field, HttpUrl
from sqlalchemy.orm import Session
from pathlib import Path
from uuid import uuid4
from fastapi.responses import FileResponse

from app.database import STORAGE_DIR

from app.database import get_db
from app.models.web_ai_tool import WebAITool, WebAITask, WebAIResultFile
from app.models.visual import VisualAsset
from app.models.novel import Novel, Chapter
from app.models.story import StoryProject, EpisodePlan, StoryboardShot, StoryAssetLink
from app.models.media import MediaFile, MediaTask, SubtitleTrack

router = APIRouter(prefix="/api/web-ai", tags=["AI网页工具协同中心"])

Category = Literal["text", "image", "storyboard", "video"]
TaskStatus = Literal["prepared", "opened", "completed", "failed", "needs_review"]


def iso(value):
    return value.isoformat() if value else None


def infer_result_classification(filename: str, content_type: str, task_category: str):
    name = (filename or "").lower()
    ext = Path(name).suffix.lower()
    mime = (content_type or "").lower()
    if ext in {".mp4", ".mov", ".webm", ".mkv"} or mime.startswith("video/"): return "video", "根据扩展名或 MIME 类型识别为视频"
    if ext in {".mp3", ".wav", ".m4a", ".aac"} or mime.startswith("audio/"): return "audio", "根据扩展名或 MIME 类型识别为音频"
    if ext in {".srt", ".vtt"}: return "subtitle", "根据字幕扩展名识别"
    if ext in {".txt", ".md"}: return ("script" if task_category == "text" else "document"), "根据文本扩展名和任务类型推测"
    if ext in {".json", ".csv", ".pdf", ".docx"}: return ("storyboard" if task_category == "storyboard" else "document"), "根据文档扩展名和任务类型推测"
    if ext in {".png", ".jpg", ".jpeg", ".webp", ".gif"} or mime.startswith("image/"):
        hints = [(("character", "人物", "角色"), "character"), (("scene", "场景", "环境"), "scene"), (("prop", "道具", "武器"), "prop"), (("storyboard", "分镜", "镜头"), "storyboard")]
        for words, kind in hints:
            if any(word in name for word in words): return kind, "仅根据文件名关键词给出候选，需人工确认"
        return "image", "识别为图片；无法仅凭元数据判断画面内容"
    return "other", "文件类型无法可靠判断"

def result_file_dict(item):
    return {"id": item.id, "taskId": item.task_key, "filename": item.original_name, "contentType": item.content_type, "size": item.file_size, "downloadUrl": "/api/web-ai/files/" + str(item.id), "createdAt": iso(item.created_at), "classification": item.classification or "other", "classificationStatus": item.classification_status or "pending", "classificationReason": item.classification_reason or "", "visualAssetId": item.visual_asset_id}

def tool_dict(item):
    return {
        "id": item.tool_key, "name": item.name, "category": item.category,
        "url": item.url, "note": item.note or "", "custom": item.is_custom,
        "enabled": item.enabled, "created_at": iso(item.created_at),
    }


def task_dict(item):
    return {
        "id": item.task_key, "title": item.title, "category": item.category,
        "toolId": item.tool_key, "status": item.status, "prompt": item.prompt,
        "sourceText": item.source_text or "", "resultText": item.result_text or "",
        "resultUrl": item.result_url or "", "projectId": item.project_id,
        "novelId": item.novel_id, "chapterId": item.chapter_id,
        "characterId": item.character_id, "sceneId": item.scene_id,
        "createdAt": iso(item.created_at), "updatedAt": iso(item.updated_at),
    }


class ToolInput(BaseModel):
    id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=160)
    category: Category
    url: HttpUrl
    note: str = Field(default="", max_length=4000)
    custom: bool = True
    enabled: bool = True


class TaskInput(BaseModel):
    id: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=240)
    category: Category
    toolId: str = Field(min_length=1, max_length=120)
    status: TaskStatus = "prepared"
    prompt: str = Field(min_length=1, max_length=200000)
    sourceText: str = Field(default="", max_length=150000)
    resultText: str = Field(default="", max_length=200000)
    resultUrl: str = Field(default="", max_length=2048)
    projectId: int | None = None
    novelId: int | None = None
    chapterId: int | None = None
    characterId: int | None = None
    sceneId: int | None = None


@router.get("/tools")
def list_tools(db: Session = Depends(get_db)):
    return {"items": [tool_dict(x) for x in db.query(WebAITool).order_by(WebAITool.id.asc()).all()]}


@router.put("/tools")
def upsert_tool(payload: ToolInput, db: Session = Depends(get_db)):
    item = db.query(WebAITool).filter(WebAITool.tool_key == payload.id).first()
    if item is None:
        item = WebAITool(tool_key=payload.id, name=payload.name, category=payload.category, url=str(payload.url))
        db.add(item)
    item.name, item.category, item.url = payload.name, payload.category, str(payload.url)
    item.note, item.is_custom, item.enabled = payload.note, payload.custom, payload.enabled
    item.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(item)
    return tool_dict(item)


@router.delete("/tools/{tool_key}")
def delete_tool(tool_key: str, db: Session = Depends(get_db)):
    item = db.query(WebAITool).filter(WebAITool.tool_key == tool_key).first()
    if item is None:
        raise HTTPException(404, "AI工具不存在")
    db.delete(item)
    db.commit()
    return {"success": True}


@router.get("/tasks")
def list_tasks(limit: int = 200, db: Session = Depends(get_db)):
    if limit < 1 or limit > 500:
        raise HTTPException(400, "limit 必须在 1 到 500 之间")
    items = db.query(WebAITask).order_by(WebAITask.id.desc()).limit(limit).all()
    return {"items": [task_dict(x) for x in items]}


@router.post("/tasks")
def create_task(payload: TaskInput, db: Session = Depends(get_db)):
    if db.query(WebAITask).filter(WebAITask.task_key == payload.id).first():
        raise HTTPException(409, "任务编号已存在")
    item = WebAITask(
        task_key=payload.id, title=payload.title, category=payload.category,
        tool_key=payload.toolId, status=payload.status, prompt=payload.prompt,
        source_text=payload.sourceText, result_text=payload.resultText,
        result_url=payload.resultUrl, project_id=payload.projectId,
        novel_id=payload.novelId, chapter_id=payload.chapterId,
        character_id=payload.characterId, scene_id=payload.sceneId,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return task_dict(item)


@router.patch("/tasks/{task_key}")
def update_task(task_key: str, payload: TaskInput, db: Session = Depends(get_db)):
    item = db.query(WebAITask).filter(WebAITask.task_key == task_key).first()
    if item is None:
        raise HTTPException(404, "任务不存在")
    if payload.id != task_key:
        raise HTTPException(400, "任务编号不能修改")
    item.title, item.category, item.tool_key = payload.title, payload.category, payload.toolId
    item.status, item.prompt, item.source_text = payload.status, payload.prompt, payload.sourceText
    item.result_text, item.result_url = payload.resultText, payload.resultUrl
    item.project_id, item.novel_id, item.chapter_id = payload.projectId, payload.novelId, payload.chapterId
    item.character_id, item.scene_id = payload.characterId, payload.sceneId
    item.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(item)
    return task_dict(item)


@router.delete("/tasks/{task_key}")
def delete_task(task_key: str, db: Session = Depends(get_db)):
    item = db.query(WebAITask).filter(WebAITask.task_key == task_key).first()
    if item is None:
        raise HTTPException(404, "任务不存在")
    db.delete(item)
    db.commit()
    return {"success": True}


ALLOWED_RESULT_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".webp", ".gif",
    ".mp4", ".mov", ".webm", ".mkv",
    ".mp3", ".wav", ".m4a", ".aac",
    ".txt", ".md", ".json", ".srt", ".vtt",
    ".pdf", ".docx", ".csv",
}
MAX_RESULT_FILE_BYTES = 2 * 1024 * 1024 * 1024

MEDIA_LIBRARY_TYPES = {
    ".mp4": ("video", "video/mp4"),
    ".mov": ("video", "video/quicktime"),
    ".webm": ("video", "video/webm"),
    ".mkv": ("video", "video/x-matroska"),
    ".mp3": ("audio", "audio/mpeg"),
    ".wav": ("audio", "audio/wav"),
    ".m4a": ("audio", "audio/mp4"),
    ".aac": ("audio", "audio/aac"),
    ".srt": ("other", "application/x-subrip"),
    ".vtt": ("other", "text/vtt"),
}


def validate_downloaded_media_signature(path: Path, extension: str) -> bool:
    """Check downloaded media magic bytes before adding it to the editor library."""
    try:
        with path.open("rb") as source:
            prefix = source.read(64)
    except OSError:
        return False
    if extension in {".mp4", ".mov", ".m4a"}:
        return len(prefix) >= 8 and prefix[4:8] == b"ftyp"
    if extension in {".mkv", ".webm"}:
        return prefix.startswith(b"\x1a\x45\xdf\xa3")
    if extension == ".wav":
        return len(prefix) >= 12 and prefix[:4] == b"RIFF" and prefix[8:12] == b"WAVE"
    if extension == ".mp3":
        return prefix.startswith(b"ID3") or (len(prefix) >= 2 and prefix[0] == 0xFF and (prefix[1] & 0xE0) == 0xE0)
    if extension == ".aac":
        return len(prefix) >= 2 and prefix[0] == 0xFF and (prefix[1] & 0xF6) == 0xF0
    if extension == ".vtt":
        return prefix.lstrip(b"\xef\xbb\xbf").startswith(b"WEBVTT")
    if extension == ".srt":
        return bool(prefix) and b"\x00" not in prefix
    return False


def add_download_to_media_library(db: Session, task: WebAITask, result_file: WebAIResultFile, path: Path):
    extension = path.suffix.lower()
    media_type = MEDIA_LIBRARY_TYPES.get(extension)
    if media_type is None:
        return None
    category, fallback_mime = media_type
    if not validate_downloaded_media_signature(path, extension):
        raise HTTPException(415, "下载文件内容与扩展名不匹配，已阻止加入媒体库")
    metadata = {
        "source": "ai_web_browser",
        "web_ai_task_key": task.task_key,
        "web_ai_result_file_id": result_file.id,
        "task_title": task.title,
        "task_category": task.category,
        "novel_id": task.novel_id,
        "chapter_id": task.chapter_id,
        "project_id": task.project_id,
        "classification_status": result_file.classification_status,
    }
    media = MediaFile(
        name=result_file.original_name,
        category=category,
        file_path=str(path),
        mime_type=fallback_mime,
        file_size=result_file.file_size,
        metadata_json=metadata,
    )
    db.add(media)
    if extension == ".srt":
        db.add(SubtitleTrack(
            name=result_file.original_name,
            file_path=str(path),
            format="srt",
            language="und",
        ))
    return media



@router.post("/tasks/{task_key}/files")
async def upload_task_result_file(
    task_key: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    task = db.query(WebAITask).filter(WebAITask.task_key == task_key).first()
    if task is None:
        raise HTTPException(404, "任务不存在")
    original_name = Path(file.filename or "result.bin").name[:255]
    extension = Path(original_name).suffix.lower()
    if extension not in ALLOWED_RESULT_EXTENSIONS:
        raise HTTPException(415, "不支持该文件类型；请先导出为图片、视频、音频或常见文档格式")
    result_dir = STORAGE_DIR / "web_ai_results"
    result_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid4().hex}{extension}"
    target = result_dir / stored_name
    total = 0
    try:
        with target.open("wb") as output:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_RESULT_FILE_BYTES:
                    raise HTTPException(413, "单个结果文件不能超过 2 GB")
                output.write(chunk)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    finally:
        await file.close()
    content_type = file.content_type or "application/octet-stream"
    classification, reason = infer_result_classification(original_name, content_type, task.category)
    item = WebAIResultFile(
        task_key=task_key, original_name=original_name, stored_name=stored_name,
        content_type=content_type, file_size=total, classification=classification,
        classification_status="pending", classification_reason=reason,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return result_file_dict(item)


@router.get("/tasks/{task_key}/files")
def list_task_result_files(task_key: str, db: Session = Depends(get_db)):
    if db.query(WebAITask).filter(WebAITask.task_key == task_key).first() is None:
        raise HTTPException(404, "任务不存在")
    items = db.query(WebAIResultFile).filter(
        WebAIResultFile.task_key == task_key
    ).order_by(WebAIResultFile.id.desc()).all()
    return {"items": [result_file_dict(item) for item in items]}


@router.get("/tasks/{task_key}/reference-suggestions")
def list_reference_suggestions(task_key: str, db: Session = Depends(get_db)):
    task = db.query(WebAITask).filter(WebAITask.task_key == task_key).first()
    if task is None:
        raise HTTPException(404, "目标任务不存在")
    if task.category != "video":
        raise HTTPException(409, "只有视频分镜任务可以自动匹配参考图")
    if not task.novel_id or not task.chapter_id:
        raise HTTPException(409, "该视频任务没有关联小说章节，无法自动匹配素材")
    chapter = db.query(Chapter).filter(
        Chapter.id == task.chapter_id, Chapter.novel_id == task.novel_id
    ).first()
    if chapter is None:
        raise HTTPException(404, "视频任务关联的章节不存在")

    project_query = db.query(StoryProject).filter(StoryProject.novel_id == task.novel_id)
    project = (
        project_query.filter(StoryProject.id == task.project_id).first()
        if task.project_id
        else project_query.order_by(StoryProject.updated_at.desc()).first()
    )
    links = []
    episode_by_id = {}
    if project is not None:
        episodes = db.query(EpisodePlan).filter(EpisodePlan.project_id == project.id).all()
        episode_by_id = {
            episode.id: episode for episode in episodes
            if chapter.id in (episode.source_chapter_ids or [])
        }
        links = db.query(StoryAssetLink).filter(
            StoryAssetLink.project_id == project.id,
            StoryAssetLink.chapter_number == chapter.chapter_number,
        ).all()
        links = [
            link for link in links
            if link.episode_id is None or link.episode_id in episode_by_id
        ]

    linked_asset_ids = {link.asset_id for link in links}
    files_by_asset = {}
    if linked_asset_ids:
        linked_files = db.query(WebAIResultFile).filter(
            WebAIResultFile.visual_asset_id.in_(linked_asset_ids),
            WebAIResultFile.classification_status == "confirmed",
            WebAIResultFile.content_type.like("image/%"),
        ).all()
        allowed_reference_extensions = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
        for result_file in linked_files:
            if Path(result_file.stored_name).suffix.lower() not in allowed_reference_extensions:
                continue
            files_by_asset.setdefault(result_file.visual_asset_id, []).append(result_file)

    suggestions = {}
    for link in links:
        shot = db.query(StoryboardShot).filter(StoryboardShot.id == link.shot_id).first() if link.shot_id else None
        episode = episode_by_id.get(link.episode_id)
        for result_file in files_by_asset.get(link.asset_id, []):
            entry = suggestions.setdefault(result_file.id, {
                "fileId": result_file.id,
                "filename": result_file.original_name,
                "classification": result_file.classification,
                "role": link.role,
                "matches": [],
            })
            if shot is not None:
                entry["matches"].append({
                    "episodeNumber": episode.episode_number if episode else None,
                    "shotNumber": shot.shot_number,
                    "shotType": shot.shot_type,
                    "sceneName": shot.scene_name,
                    "visualDescription": shot.visual_description,
                })

    if not suggestions:
        fallback_files = db.query(WebAIResultFile).join(
            WebAITask, WebAITask.task_key == WebAIResultFile.task_key
        ).filter(
            WebAITask.novel_id == task.novel_id,
            WebAITask.chapter_id == task.chapter_id,
            WebAIResultFile.classification_status == "confirmed",
            WebAIResultFile.visual_asset_id.isnot(None),
            WebAIResultFile.content_type.like("image/%"),
        ).order_by(WebAIResultFile.id.desc()).all()
        allowed_reference_extensions = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
        for result_file in fallback_files:
            if Path(result_file.stored_name).suffix.lower() not in allowed_reference_extensions:
                continue
            suggestions[result_file.id] = {
                "fileId": result_file.id,
                "filename": result_file.original_name,
                "classification": result_file.classification,
                "role": "chapter_asset",
                "matches": [],
            }

    items = sorted(
        suggestions.values(),
        key=lambda item: (
            min((match["shotNumber"] for match in item["matches"]), default=10**9),
            item["fileId"],
        ),
    )
    return {
        "taskId": task_key,
        "chapterNumber": chapter.chapter_number,
        "items": items,
        "matchedShotCount": len({(match["episodeNumber"], match["shotNumber"]) for item in items for match in item["matches"]}),
    }


@router.get("/files")
def list_all_result_files(
    novel_id: int | None = None,
    chapter_id: int | None = None,
    limit: int = 200,
    db: Session = Depends(get_db),
):
    if limit < 1 or limit > 500:
        raise HTTPException(400, "limit 必须在 1 到 500 之间")
    query = db.query(WebAIResultFile, WebAITask).join(
        WebAITask, WebAITask.task_key == WebAIResultFile.task_key
    )
    if novel_id is not None:
        query = query.filter(WebAITask.novel_id == novel_id)
    if chapter_id is not None:
        query = query.filter(WebAITask.chapter_id == chapter_id)
    rows = query.order_by(WebAIResultFile.id.desc()).limit(limit).all()
    items = []
    for result_file, task in rows:
        item = result_file_dict(result_file)
        item.update({
            "taskTitle": task.title,
            "taskCategory": task.category,
            "toolId": task.tool_key,
            "novelId": task.novel_id,
            "chapterId": task.chapter_id,
        })
        items.append(item)
    return {"items": items}


@router.get("/files/{file_id}/upload-info")
def get_reference_upload_info(file_id: int, db: Session = Depends(get_db)):
    item = db.query(WebAIResultFile).filter(WebAIResultFile.id == file_id).first()
    if item is None:
        raise HTTPException(404, "结果文件不存在")
    if item.classification_status != "confirmed" or item.visual_asset_id is None:
        raise HTTPException(409, "请先确认图片分类并归档到视觉素材库")
    allowed_types = {
        ".png": "image/png", ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif",
    }
    extension = Path(item.stored_name).suffix.lower()
    expected_type = allowed_types.get(extension)
    if expected_type is None or not (item.content_type or "").lower().startswith("image/"):
        raise HTTPException(415, "仅允许上传已归档的 PNG、JPG、WebP 或 GIF 图片")
    result_dir = (STORAGE_DIR / "web_ai_results").resolve()
    path = (result_dir / item.stored_name).resolve()
    if path.parent != result_dir or not path.is_file():
        raise HTTPException(410, "参考图片文件已不存在")
    actual_size = path.stat().st_size
    if actual_size > 100 * 1024 * 1024:
        raise HTTPException(413, "单张参考图片不能超过 100 MB")
    source_task = db.query(WebAITask).filter(WebAITask.task_key == item.task_key).first()
    if source_task is None:
        raise HTTPException(410, "参考图片所属任务不存在")
    return {
        "id": item.id,
        "storedName": item.stored_name,
        "filename": item.original_name,
        "contentType": expected_type,
        "size": actual_size,
        "novelId": source_task.novel_id,
        "chapterId": source_task.chapter_id,
    }


@router.get("/files/{file_id}/preview")
def preview_task_result_file(file_id: int, db: Session = Depends(get_db)):
    item = db.query(WebAIResultFile).filter(WebAIResultFile.id == file_id).first()
    if item is None:
        raise HTTPException(404, "结果文件不存在")
    preview_types = {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".webp": "image/webp", ".gif": "image/gif",
    }
    extension = Path(item.stored_name).suffix.lower()
    media_type = preview_types.get(extension)
    if media_type is None:
        raise HTTPException(415, "目前仅支持 PNG、JPG、WebP 和 GIF 图片在线预览")
    path = (STORAGE_DIR / "web_ai_results" / item.stored_name).resolve()
    root = (STORAGE_DIR / "web_ai_results").resolve()
    if path.parent != root or not path.is_file():
        raise HTTPException(410, "结果文件已不存在")
    return FileResponse(
        path, media_type=media_type, filename=item.original_name,
        content_disposition_type="inline",
        headers={"X-Content-Type-Options": "nosniff"},
    )


@router.get("/files/{file_id}")
def download_task_result_file(file_id: int, db: Session = Depends(get_db)):
    item = db.query(WebAIResultFile).filter(WebAIResultFile.id == file_id).first()
    if item is None:
        raise HTTPException(404, "结果文件不存在")
    path = STORAGE_DIR / "web_ai_results" / item.stored_name
    if not path.is_file():
        raise HTTPException(410, "结果文件已不存在")
    return FileResponse(
        path,
        media_type=item.content_type,
        filename=item.original_name,
        content_disposition_type="attachment",
    )


class RegisterDownloadedFile(BaseModel):
    taskId: str = Field(min_length=1, max_length=120)
    filename: str = Field(min_length=1, max_length=255)
    storedName: str = Field(pattern=r"^[a-f0-9]{32}\.(png|jpg|jpeg|webp|gif|mp4|mov|webm|mkv|mp3|wav|m4a|aac|txt|md|json|srt|vtt|pdf|docx|csv)$")
    contentType: str = Field(default="application/octet-stream", max_length=160)
    size: int = Field(ge=0, le=2 * 1024 * 1024 * 1024)


@router.post("/files/register")
def register_downloaded_file(payload: RegisterDownloadedFile, db: Session = Depends(get_db)):
    task = db.query(WebAITask).filter(WebAITask.task_key == payload.taskId).first()
    if task is None:
        raise HTTPException(404, "任务不存在")
    result_dir = (STORAGE_DIR / "web_ai_results").resolve()
    target = (result_dir / payload.storedName).resolve()
    if target.parent != result_dir or not target.is_file():
        raise HTTPException(400, "下载文件不存在或路径无效")
    actual_size = target.stat().st_size
    if actual_size != payload.size:
        raise HTTPException(409, "下载文件大小校验失败")
    existing = db.query(WebAIResultFile).filter(WebAIResultFile.stored_name == payload.storedName).first()
    if existing is not None:
        return result_file_dict(existing)
    original_name = Path(payload.filename).name[:255]
    classification, reason = infer_result_classification(original_name, payload.contentType, task.category)
    item = WebAIResultFile(
        task_key=payload.taskId, original_name=original_name, stored_name=payload.storedName,
        content_type=payload.contentType, file_size=actual_size, classification=classification,
        classification_status="pending", classification_reason=reason,
    )
    db.add(item)
    db.flush()
    add_download_to_media_library(db, task, item, target)
    task.status = "completed"
    task.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(item)
    return result_file_dict(item)


class ConfirmResultClassification(BaseModel):
    classification: Literal["character", "scene", "prop", "weapon", "costume", "building", "special_item", "storyboard", "script", "subtitle", "video", "audio", "document", "image", "other"]
    assetName: str = Field(default="", max_length=200)
    description: str = Field(default="", max_length=4000)
    novelId: int | None = Field(default=None, ge=1)
    chapterId: int | None = Field(default=None, ge=1)

def _downloaded_video_shot_context(file_id: int, db: Session):
    result_file = db.query(WebAIResultFile).filter(WebAIResultFile.id == file_id).first()
    if result_file is None:
        raise HTTPException(404, "AI 网页结果文件不存在")
    if Path(result_file.stored_name).suffix.lower() not in {".mp4", ".mov", ".webm", ".mkv"}:
        raise HTTPException(415, "只有视频文件可以关联到分镜")
    source_task = db.query(WebAITask).filter(
        WebAITask.task_key == result_file.task_key
    ).first()
    if source_task is None:
        raise HTTPException(410, "视频所属的 AI 网页任务不存在")
    if source_task.category != "video":
        raise HTTPException(409, "只有视频类 AI 网页任务的结果可以关联分镜")
    if not source_task.novel_id or not source_task.chapter_id:
        raise HTTPException(409, "原始 AI 任务没有关联小说章节")
    chapter = db.query(Chapter).filter(
        Chapter.id == source_task.chapter_id,
        Chapter.novel_id == source_task.novel_id,
    ).first()
    if chapter is None:
        raise HTTPException(404, "原始任务关联的章节不存在或已变更")
    projects_query = db.query(StoryProject).filter(
        StoryProject.novel_id == source_task.novel_id
    )
    if source_task.project_id is not None:
        projects_query = projects_query.filter(StoryProject.id == source_task.project_id)
    projects = projects_query.order_by(StoryProject.updated_at.desc()).all()
    if not projects:
        raise HTTPException(404, "没有找到该小说对应的漫剧项目")
    project_ids = [project.id for project in projects]
    episodes = db.query(EpisodePlan).filter(
        EpisodePlan.project_id.in_(project_ids)
    ).order_by(EpisodePlan.episode_number, EpisodePlan.sort_order).all()
    eligible_episodes = [
        episode for episode in episodes
        if chapter.id in (episode.source_chapter_ids or [])
    ]
    return result_file, source_task, chapter, eligible_episodes


@router.get("/files/{file_id}/shot-options")
def list_downloaded_video_shot_options(file_id: int, db: Session = Depends(get_db)):
    result_file, source_task, chapter, episodes = _downloaded_video_shot_context(file_id, db)
    items = []
    for episode in episodes:
        shots = db.query(StoryboardShot).filter(
            StoryboardShot.episode_id == episode.id
        ).order_by(StoryboardShot.sort_order, StoryboardShot.shot_number).all()
        for shot in shots:
            items.append({
                "shotId": shot.id,
                "shotNumber": shot.shot_number,
                "episodeId": episode.id,
                "episodeNumber": episode.episode_number,
                "episodeTitle": episode.title,
                "sceneName": shot.scene_name,
                "shotType": shot.shot_type,
                "visualDescription": shot.visual_description,
                "durationSeconds": shot.duration_seconds,
                "status": shot.status,
            })
    return {
        "fileId": result_file.id,
        "taskId": source_task.task_key,
        "chapterId": source_task.chapter_id,
        "chapterNumber": chapter.chapter_number,
        "items": items,
    }


class AttachDownloadedVideoInput(BaseModel):
    shotId: int = Field(ge=1)


@router.post("/files/{file_id}/attach-shot")
def attach_downloaded_video_to_shot(
    file_id: int,
    payload: AttachDownloadedVideoInput,
    db: Session = Depends(get_db),
):
    """Attach a downloaded browser-generated video to an explicitly selected storyboard shot."""
    result_file = db.query(WebAIResultFile).filter(WebAIResultFile.id == file_id).first()
    if result_file is None:
        raise HTTPException(404, "AI 网页结果文件不存在")
    extension = Path(result_file.stored_name).suffix.lower()
    if extension not in {".mp4", ".mov", ".webm", ".mkv"}:
        raise HTTPException(415, "只有 MP4、MOV、WebM 或 MKV 视频可以关联到分镜")
    source_task = db.query(WebAITask).filter(
        WebAITask.task_key == result_file.task_key
    ).first()
    if source_task is None:
        raise HTTPException(410, "视频所属的 AI 网页任务不存在")
    if source_task.category != "video":
        raise HTTPException(409, "只有视频类 AI 网页任务的结果可以关联分镜")
    if not source_task.novel_id or not source_task.chapter_id:
        raise HTTPException(409, "原始 AI 任务没有关联小说章节，无法安全确定分集范围")

    chapter = db.query(Chapter).filter(
        Chapter.id == source_task.chapter_id,
        Chapter.novel_id == source_task.novel_id,
    ).first()
    if chapter is None:
        raise HTTPException(404, "原始任务关联的章节不存在或已变更")

    project_query = db.query(StoryProject).filter(
        StoryProject.novel_id == source_task.novel_id
    )
    if source_task.project_id is not None:
        project_query = project_query.filter(StoryProject.id == source_task.project_id)
    projects = project_query.order_by(StoryProject.updated_at.desc()).all()
    if not projects:
        raise HTTPException(404, "没有找到该小说对应的漫剧项目")
    project_ids = {project.id for project in projects}

    shot = db.query(StoryboardShot).filter(
        StoryboardShot.id == payload.shotId
    ).first()
    if shot is None:
        raise HTTPException(404, "指定分镜不存在")
    episode = db.query(EpisodePlan).filter(
        EpisodePlan.id == shot.episode_id,
        EpisodePlan.project_id == shot.project_id,
    ).first()
    if episode is None or episode.project_id not in project_ids:
        raise HTTPException(409, "指定分镜不属于该 AI 任务对应的漫剧项目")
    if source_task.chapter_id not in (episode.source_chapter_ids or []):
        raise HTTPException(409, "指定分镜所属分集没有包含该 AI 任务的原始章节")

    result_dir = (STORAGE_DIR / "web_ai_results").resolve()
    source_path = (result_dir / result_file.stored_name).resolve()
    if source_path.parent != result_dir or not source_path.is_file():
        raise HTTPException(410, "下载的视频文件已不存在")
    if not validate_downloaded_media_signature(source_path, extension):
        raise HTTPException(415, "视频文件签名校验失败，不能关联到分镜")

    media_file = db.query(MediaFile).filter(
        MediaFile.file_path == str(source_path),
        MediaFile.category == "video",
    ).first()
    if media_file is None:
        raise HTTPException(409, "视频尚未登记到本地视频素材库，请重新下载或刷新素材库")

    for existing_task in db.query(MediaTask).filter(
        MediaTask.task_type == "storyboard_video"
    ).all():
        metadata = existing_task.request_data if isinstance(existing_task.request_data, dict) else {}
        if metadata.get("web_ai_result_file_id") == result_file.id:
            if metadata.get("shot_id") != shot.id or metadata.get("episode_id") != episode.id:
                raise HTTPException(409, "该视频已经关联到其他分镜；请先在素材管理中处理原关联")
            return {
                "success": True,
                "reused": True,
                "task_id": existing_task.id,
                "episode_id": episode.id,
                "shot_id": shot.id,
                "message": "该视频已经关联到此分镜，无需重复登记。",
            }

    try:
        from app.services.media_service import probe_media
        info = probe_media(source_path)
        streams = info.get("streams") or []
        video_stream = next(
            (stream for stream in streams if stream.get("codec_type") == "video"),
            None,
        )
        duration = float((info.get("format") or {}).get("duration") or 0)
        if video_stream is None or duration <= 0 or duration > 600:
            raise HTTPException(422, "视频流无效或单镜头视频超过 10 分钟")
        media_file.duration = duration
        metadata = dict(media_file.metadata_json or {})
        metadata.update({
            "episode_id": episode.id,
            "shot_id": shot.id,
            "shot_number": shot.shot_number,
            "web_ai_task_key": source_task.task_key,
            "web_ai_result_file_id": result_file.id,
            "novel_id": source_task.novel_id,
            "chapter_id": source_task.chapter_id,
            "project_id": episode.project_id,
            "attached_by_user": True,
        })
        media_file.metadata_json = metadata
        media_task = MediaTask(
            task_type="storyboard_video",
            provider="web_ai",
            model="web-browser-import",
            status="completed",
            prompt=source_task.title,
            request_data={
                "episode_id": episode.id,
                "shot_id": shot.id,
                "shot_number": shot.shot_number,
                "web_ai_task_key": source_task.task_key,
                "web_ai_result_file_id": result_file.id,
                "novel_id": source_task.novel_id,
                "chapter_id": source_task.chapter_id,
                "project_id": episode.project_id,
                "local_import": True,
            },
            result_path=str(source_path),
        )
        db.add(media_task)
        shot.status = "video_ready"
        db.commit()
        db.refresh(media_task)
        return {
            "success": True,
            "reused": False,
            "task_id": media_task.id,
            "episode_id": episode.id,
            "shot_id": shot.id,
            "shot_number": shot.shot_number,
            "duration_seconds": round(duration, 3),
            "message": "视频已关联到指定分镜，可进入分集时间轴并参与 FFmpeg 合成。",
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(422, "视频关联失败：" + str(exc)[:1000]) from exc


@router.patch("/files/{file_id}/classification")
def confirm_result_classification(file_id: int, payload: ConfirmResultClassification, db: Session = Depends(get_db)):
    item = db.query(WebAIResultFile).filter(WebAIResultFile.id == file_id).first()
    if item is None: raise HTTPException(404, "结果文件不存在")
    target = (STORAGE_DIR / "web_ai_results" / item.stored_name).resolve()
    root = (STORAGE_DIR / "web_ai_results").resolve()
    if target.parent != root or not target.is_file(): raise HTTPException(410, "结果文件已不存在")
    if item.classification_status == "confirmed": raise HTTPException(409, "该文件已经确认归档，不能重复创建素材")
    asset_id = None
    task = db.query(WebAITask).filter(WebAITask.task_key == item.task_key).first()
    resolved_novel_id = payload.novelId or (task.novel_id if task else None)
    resolved_chapter_id = payload.chapterId or (task.chapter_id if task else None)
    chapter = None
    if resolved_novel_id is not None and db.query(Novel).filter(Novel.id == resolved_novel_id).first() is None:
        raise HTTPException(404, "指定的小说不存在")
    if resolved_chapter_id is not None:
        chapter = db.query(Chapter).filter(
            Chapter.id == resolved_chapter_id,
            Chapter.novel_id == resolved_novel_id,
        ).first()
        if chapter is None:
            raise HTTPException(404, "指定章节不存在或不属于该小说")
    if payload.classification in {"character", "scene", "prop", "weapon", "costume", "building", "special_item"}:
        if target.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}: raise HTTPException(400, "人物、场景和道具素材必须是图片文件")
        if not payload.assetName.strip(): raise HTTPException(400, "请填写素材名称后再确认归档")
        category_map = {"character": "characters", "scene": "scenes", "prop": "props", "weapon": "weapons", "costume": "costumes", "building": "buildings", "special_item": "special_items"}
        asset_data = {"source": "web_ai_result", "result_file_id": item.id, "task_key": item.task_key, "original_filename": item.original_name}
        if resolved_novel_id is not None:
            asset_data["source_novel_id"] = resolved_novel_id
        if chapter is not None:
            asset_data["source_chapter_id"] = chapter.id
            asset_data["source_chapter_number"] = chapter.chapter_number
        asset = VisualAsset(category=category_map[payload.classification], name=payload.assetName.strip(), description=payload.description.strip() or ("从 AI 网页任务 " + item.task_key + " 导入"), image_path=str(target), style="待确认", data=asset_data, confirmed=True)
        db.add(asset)
        db.flush()
        asset_id = asset.id

        # 将已确认的图片资产挂到本小说对应的分集，并尽可能关联到名称明确匹配的镜头。
        if resolved_novel_id is not None:
            projects_query = db.query(StoryProject).filter(StoryProject.novel_id == resolved_novel_id)
            if task and task.project_id:
                projects_query = projects_query.filter(StoryProject.id == task.project_id)
            else:
                projects_query = projects_query.order_by(StoryProject.updated_at.desc()).limit(1)
            projects = projects_query.all()
            for project in projects:
                episodes_query = db.query(EpisodePlan).filter(EpisodePlan.project_id == project.id)
                episodes = episodes_query.all()
                for episode in episodes:
                    source_ids = episode.source_chapter_ids if isinstance(episode.source_chapter_ids, list) else []
                    if chapter is not None and chapter.id not in source_ids:
                        continue
                    if chapter is None and not source_ids:
                        continue
                    episode_link = StoryAssetLink(
                        project_id=project.id, episode_id=episode.id, shot_id=None,
                        asset_id=asset.id, role=category_map[payload.classification],
                        chapter_number=chapter.chapter_number if chapter else None,
                    )
                    db.add(episode_link)
                    shots = db.query(StoryboardShot).filter(
                        StoryboardShot.episode_id == episode.id
                    ).all()
                    for shot in shots:
                        searchable = " ".join([
                            shot.scene_name or "", shot.visual_description or "",
                            shot.character_actions or "", shot.dialogue or "",
                            shot.image_prompt or "",
                        ]).casefold()
                        asset_name = payload.assetName.strip().casefold()
                        if not asset_name or asset_name not in searchable:
                            continue
                        if category_map[payload.classification] == "scenes" and (shot.scene_name or "").strip() and (
                            asset_name in shot.scene_name.casefold()
                            or shot.scene_name.casefold() in asset_name
                        ):
                            shot.scene_asset_id = asset.id
                        exists = db.query(StoryAssetLink).filter(
                            StoryAssetLink.project_id == project.id,
                            StoryAssetLink.episode_id == episode.id,
                            StoryAssetLink.shot_id == shot.id,
                            StoryAssetLink.asset_id == asset.id,
                        ).first()
                        if exists is None:
                            db.add(StoryAssetLink(
                                project_id=project.id, episode_id=episode.id,
                                shot_id=shot.id, asset_id=asset.id,
                                role=category_map[payload.classification],
                                chapter_number=chapter.chapter_number if chapter else None,
                            ))
    item.classification = payload.classification
    item.classification_status = "confirmed"
    item.classification_reason = "用户已确认分类"
    item.visual_asset_id = asset_id
    db.commit()
    db.refresh(item)
    return result_file_dict(item)
