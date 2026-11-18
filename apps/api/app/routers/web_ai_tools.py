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

router = APIRouter(prefix="/api/web-ai", tags=["AI网页工具协同中心"])

Category = Literal["text", "image", "storyboard", "video"]
TaskStatus = Literal["prepared", "opened", "completed", "failed", "needs_review"]


def iso(value):
    return value.isoformat() if value else None


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
    item = WebAIResultFile(
        task_key=task_key,
        original_name=original_name,
        stored_name=stored_name,
        content_type=file.content_type or "application/octet-stream",
        file_size=total,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return {
        "id": item.id, "taskId": item.task_key, "filename": item.original_name,
        "contentType": item.content_type, "size": item.file_size,
        "downloadUrl": f"/api/web-ai/files/{item.id}", "createdAt": iso(item.created_at),
    }


@router.get("/tasks/{task_key}/files")
def list_task_result_files(task_key: str, db: Session = Depends(get_db)):
    if db.query(WebAITask).filter(WebAITask.task_key == task_key).first() is None:
        raise HTTPException(404, "任务不存在")
    items = db.query(WebAIResultFile).filter(
        WebAIResultFile.task_key == task_key
    ).order_by(WebAIResultFile.id.desc()).all()
    return {"items": [{
        "id": item.id, "taskId": item.task_key, "filename": item.original_name,
        "contentType": item.content_type, "size": item.file_size,
        "downloadUrl": f"/api/web-ai/files/{item.id}", "createdAt": iso(item.created_at),
    } for item in items]}


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
        return {
            "id": existing.id, "taskId": existing.task_key, "filename": existing.original_name,
            "contentType": existing.content_type, "size": existing.file_size,
            "downloadUrl": f"/api/web-ai/files/{existing.id}", "createdAt": iso(existing.created_at),
        }
    item = WebAIResultFile(
        task_key=payload.taskId,
        original_name=Path(payload.filename).name[:255],
        stored_name=payload.storedName,
        content_type=payload.contentType,
        file_size=actual_size,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return {
        "id": item.id, "taskId": item.task_key, "filename": item.original_name,
        "contentType": item.content_type, "size": item.file_size,
        "downloadUrl": f"/api/web-ai/files/{item.id}", "createdAt": iso(item.created_at),
    }
