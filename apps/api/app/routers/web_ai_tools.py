from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, HttpUrl
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.web_ai_tool import WebAITool, WebAITask

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
