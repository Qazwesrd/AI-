from datetime import datetime
from typing import Literal, Optional
from pathlib import Path
import hashlib
import json
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.novel import Novel, Chapter, AnalysisItem
from app.models.visual import VisualAsset
from app.models.story import StoryProject, EpisodePlan, StoryboardShot, StoryAssetLink
from app.models.ai_story_collaboration import AIStoryProposal, AIResultAttachment
from app.models.media import MediaFile
from app.services.media_service import STORAGE, safe_storage_file

router = APIRouter(prefix="/api/ai-collaboration", tags=["网页版AI协同创作"])


class ProposalInput(BaseModel):
    chapter_number: int = Field(ge=1)
    asset_name: str = Field(min_length=1, max_length=200)
    asset_category: Literal["characters", "scenes", "props", "weapons", "costumes", "buildings", "special_items"] = "characters"
    change_type: str = Field(default="状态变化", max_length=80)
    event: str = Field(min_length=2, max_length=2000)
    appearance_after: str = Field(default="", max_length=4000)
    clothing_after: str = Field(default="", max_length=4000)
    age_after: str = Field(default="", max_length=80)
    hairstyle_after: str = Field(default="", max_length=300)
    injury_after: str = Field(default="", max_length=1000)
    equipment_after: str = Field(default="", max_length=2000)
    condition_after: str = Field(default="", max_length=1000)
    holder: str = Field(default="", max_length=300)
    evidence: str = Field(min_length=4, max_length=2000)
    confidence: float = Field(default=0.5, ge=0, le=1)


class ImportPayload(BaseModel):
    novel_id: int
    source_text: str = Field(min_length=1, max_length=150000)
    source_task_key: str = Field(default="", max_length=120)
    proposals: list[ProposalInput] = Field(min_length=1, max_length=500)


class ReviewPayload(BaseModel):
    action: Literal["approve", "reject"]
    note: str = Field(default="", max_length=2000)


class MatchPayload(BaseModel):
    shot_id: int
    chapter_number: int | None = Field(default=None, ge=1)
    names: list[str] = Field(default_factory=list, max_length=30)


class AttachmentReviewPayload(BaseModel):
    action: Literal["approve", "reject"]
    note: str = Field(default="", max_length=2000)


RESULT_FILE_TYPES = {
    ".png": ("image", "image/png", 100 * 1024 * 1024),
    ".jpg": ("image", "image/jpeg", 100 * 1024 * 1024),
    ".jpeg": ("image", "image/jpeg", 100 * 1024 * 1024),
    ".webp": ("image", "image/webp", 100 * 1024 * 1024),
    ".gif": ("image", "image/gif", 100 * 1024 * 1024),
    ".mp4": ("video", "video/mp4", 500 * 1024 * 1024),
    ".mov": ("video", "video/quicktime", 500 * 1024 * 1024),
    ".mkv": ("video", "video/x-matroska", 500 * 1024 * 1024),
    ".webm": ("video", "video/webm", 500 * 1024 * 1024),
    ".mp3": ("audio", "audio/mpeg", 200 * 1024 * 1024),
    ".wav": ("audio", "audio/wav", 200 * 1024 * 1024),
    ".m4a": ("audio", "audio/mp4", 200 * 1024 * 1024),
    ".aac": ("audio", "audio/aac", 200 * 1024 * 1024),
    ".ogg": ("audio", "audio/ogg", 200 * 1024 * 1024),
    ".srt": ("subtitle", "application/x-subrip", 20 * 1024 * 1024),
    ".vtt": ("subtitle", "text/vtt", 20 * 1024 * 1024),
    ".txt": ("document", "text/plain", 20 * 1024 * 1024),
    ".md": ("document", "text/markdown", 20 * 1024 * 1024),
    ".json": ("document", "application/json", 20 * 1024 * 1024),
    ".pdf": ("document", "application/pdf", 100 * 1024 * 1024),
}


def validate_result_file_signature(extension: str, prefix: bytes):
    valid = {
        ".png": prefix.startswith(b"\x89PNG\r\n\x1a\n"),
        ".jpg": prefix.startswith(b"\xff\xd8\xff"),
        ".jpeg": prefix.startswith(b"\xff\xd8\xff"),
        ".gif": prefix.startswith((b"GIF87a", b"GIF89a")),
        ".webp": len(prefix) >= 12 and prefix[:4] == b"RIFF" and prefix[8:12] == b"WEBP",
        ".mp4": len(prefix) >= 8 and prefix[4:8] == b"ftyp",
        ".mov": len(prefix) >= 8 and prefix[4:8] == b"ftyp",
        ".mkv": prefix.startswith(b"\x1a\x45\xdf\xa3"),
        ".webm": prefix.startswith(b"\x1a\x45\xdf\xa3"),
        ".wav": len(prefix) >= 12 and prefix[:4] == b"RIFF" and prefix[8:12] == b"WAVE",
        ".ogg": prefix.startswith(b"OggS"),
        ".mp3": prefix.startswith(b"ID3") or (len(prefix) >= 2 and prefix[0] == 0xFF and (prefix[1] & 0xE0) == 0xE0),
        ".aac": len(prefix) >= 2 and prefix[0] == 0xFF and (prefix[1] & 0xF6) == 0xF0,
        ".m4a": len(prefix) >= 8 and prefix[4:8] == b"ftyp",
        ".srt": bool(prefix) and b"\x00" not in prefix,
        ".vtt": prefix.lstrip(b"\xef\xbb\xbf").startswith(b"WEBVTT"),
        ".txt": bool(prefix) and b"\x00" not in prefix,
        ".md": bool(prefix) and b"\x00" not in prefix,
        ".json": bool(prefix) and b"\x00" not in prefix,
        ".pdf": prefix.startswith(b"%PDF-"),
    }
    if not valid.get(extension, False):
        raise HTTPException(415, "文件内容与扩展名不匹配，已拒绝导入")

def attachment_dict(item: AIResultAttachment):
    return {
        "id": item.id, "novel_id": item.novel_id, "chapter_number": item.chapter_number,
        "original_filename": item.original_filename, "media_type": item.media_type,
        "mime_type": item.mime_type, "file_size": item.file_size,
        "sha256": item.sha256, "description": item.description,
        "source_task_key": item.source_task_key, "status": item.status,
        "review_note": item.review_note, "media_file_id": item.media_file_id,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }



class TextResultPayload(BaseModel):
    novel_id: int
    chapter_number: int = Field(ge=1)
    content: str = Field(min_length=1, max_length=15000000)
    source_task_key: str = Field(default="", max_length=120)


def classify_ai_text_result(content: str):
    """Classify structured AI output without treating model text as trusted instructions."""
    try:
        parsed = json.loads(content)
    except (TypeError, ValueError):
        parsed = None
    if isinstance(parsed, dict):
        keys = {str(key).lower() for key in parsed.keys()}
        if "proposals" in keys:
            return "剧情变化提议"
        if keys.intersection({"storyboard", "shots", "镜头", "分镜"}):
            return "分镜设计"
        if keys.intersection({"screenplay", "script", "episode_screenplay", "剧本"}):
            return "剧本"
        if keys.intersection({"characters", "character_profiles", "角色", "人物设定"}):
            return "角色设定"
        if keys.intersection({"scenes", "scene_design", "场景"}):
            return "场景设定"
        if keys.intersection({"dialogues", "dialogue", "对白"}):
            return "对白"
        if keys.intersection({"events", "relationships", "timeline"}):
            return "剧情分析"
    sample = content[:12000].lower()
    if any(word in sample for word in ("分镜", "镜头号", "景别", "运镜")):
        return "分镜设计"
    if any(word in sample for word in ("场景设定", "场景描述", "环境设计")):
        return "场景设定"
    if any(word in sample for word in ("角色设定", "人物设定", "角色档案")):
        return "角色设定"
    if any(word in sample for word in ("对白", "台词", "角色台词")):
        return "对白"
    if any(word in sample for word in ("剧本", "场次", "内景", "外景")):
        return "剧本"
    return "综合分析"


@router.post("/results/text")
def save_text_result(payload: TextResultPayload, db: Session = Depends(get_db)):
    if db.query(Novel).filter(Novel.id == payload.novel_id).first() is None:
        raise HTTPException(404, "小说不存在")
    if not chapter_exists(db, payload.novel_id, payload.chapter_number):
        raise HTTPException(404, "指定章节不存在")
    content = payload.content.strip()
    if not content:
        raise HTTPException(400, "AI 返回内容为空")
    result_type = classify_ai_text_result(content)
    extension = ".json"
    mime_type = "application/json"
    try:
        json.loads(content)
    except (TypeError, ValueError):
        extension, mime_type = ".md", "text/markdown"
    folder = STORAGE / "ai_results" / str(payload.novel_id)
    folder.mkdir(parents=True, exist_ok=True)
    stored_name = uuid.uuid4().hex + extension
    destination = folder / stored_name
    encoded = content.encode("utf-8")
    if len(encoded) > 20 * 1024 * 1024:
        raise HTTPException(413, "文本结果超过 20 MB")
    checksum = hashlib.sha256(encoded).hexdigest()
    duplicate = db.query(AIResultAttachment).filter(
        AIResultAttachment.novel_id == payload.novel_id,
        AIResultAttachment.sha256 == checksum,
    ).first()
    if duplicate:
        return {"success": True, "duplicate": True, "detected_type": result_type,
                "attachment": attachment_dict(duplicate), "message": "检测到相同结果，未重复保存。"}
    try:
        destination.write_bytes(encoded)
        item = AIResultAttachment(
            novel_id=payload.novel_id, chapter_number=payload.chapter_number,
            original_filename=f"第{payload.chapter_number}章-{result_type}{extension}",
            stored_filename=stored_name, media_type="document", mime_type=mime_type,
            file_path=str(destination), file_size=len(encoded), sha256=checksum,
            description=f"网页版 AI 文本结果｜自动识别类型：{result_type}",
            source_task_key=payload.source_task_key[:120], status="pending_review",
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        return {"success": True, "duplicate": False, "detected_type": result_type,
                "attachment": attachment_dict(item),
                "message": f"已识别为「{result_type}」，并保存到第 {payload.chapter_number} 章的待审核结果区。"}
    except Exception:
        destination.unlink(missing_ok=True)
        db.rollback()
        raise


@router.post("/results/attachments")
async def upload_result_attachment(
    novel_id: int = Form(...),
    chapter_number: Optional[int] = Form(None),
    confirm_import: bool = Form(False),
    description: str = Form(""),
    source_task_key: str = Form(""),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    if not confirm_import:
        raise HTTPException(400, "请先确认导入这份网页版 AI 结果文件")
    if db.query(Novel).filter(Novel.id == novel_id).first() is None:
        raise HTTPException(404, "小说不存在")
    if chapter_number is not None and not chapter_exists(db, novel_id, chapter_number):
        raise HTTPException(404, "指定章节不存在")
    original_name = (file.filename or "ai-result").replace("\\", "/").split("/")[-1][:300]
    extension = Path(original_name).suffix.lower()
    file_type = RESULT_FILE_TYPES.get(extension)
    if not file_type:
        raise HTTPException(415, "文件格式不支持。支持图片、视频、音频、字幕、TXT、MD、JSON 和 PDF")
    media_type, mime_type, max_size = file_type
    folder = STORAGE / "ai_results" / str(novel_id)
    folder.mkdir(parents=True, exist_ok=True)
    stored_name = uuid.uuid4().hex + extension
    destination = folder / stored_name
    digest = hashlib.sha256()
    total = 0
    prefix = b""
    try:
        with destination.open("wb") as output:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                if len(prefix) < 64:
                    prefix += chunk[:64 - len(prefix)]
                total += len(chunk)
                if total > max_size:
                    raise HTTPException(413, f"文件超过该类型允许的大小上限（{max_size // (1024 * 1024)} MB）")
                digest.update(chunk)
                output.write(chunk)
        if total <= 0:
            raise HTTPException(400, "不能导入空文件")
        validate_result_file_signature(extension, prefix)
        checksum = digest.hexdigest()
        duplicate = db.query(AIResultAttachment).filter(
            AIResultAttachment.novel_id == novel_id,
            AIResultAttachment.sha256 == checksum,
        ).first()
        if duplicate:
            destination.unlink(missing_ok=True)
            return {"success": True, "duplicate": True, "attachment": attachment_dict(duplicate),
                    "message": "检测到相同文件，未重复保存。"}
        item = AIResultAttachment(
            novel_id=novel_id, chapter_number=chapter_number,
            original_filename=original_name, stored_filename=stored_name,
            media_type=media_type, mime_type=mime_type, file_path=str(destination),
            file_size=total, sha256=checksum, description=description[:2000],
            source_task_key=source_task_key[:120], status="pending_review",
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        return {"success": True, "duplicate": False, "attachment": attachment_dict(item),
                "message": "文件已安全保存到待审核区，尚未加入正式素材库。"}
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        await file.close()


@router.get("/results/attachments")
def list_result_attachments(
    novel_id: int,
    status: str = "all",
    db: Session = Depends(get_db),
):
    query = db.query(AIResultAttachment).filter(AIResultAttachment.novel_id == novel_id)
    if status != "all":
        query = query.filter(AIResultAttachment.status == status)
    items = query.order_by(AIResultAttachment.id.desc()).limit(500).all()
    return {"items": [attachment_dict(item) for item in items]}


@router.get("/results/attachments/{attachment_id}/file")
def get_result_attachment_file(attachment_id: int, db: Session = Depends(get_db)):
    item = db.query(AIResultAttachment).filter(AIResultAttachment.id == attachment_id).first()
    if item is None:
        raise HTTPException(404, "AI 结果文件不存在")
    try:
        path = safe_storage_file(item.file_path)
    except Exception as exc:
        raise HTTPException(410, f"AI 结果文件不存在或路径无效：{exc}")
    return FileResponse(
        path, media_type=item.mime_type,
        headers={"Content-Disposition": f'inline; filename="{item.stored_filename}"'},
    )


@router.post("/results/attachments/{attachment_id}/review")
def review_result_attachment(
    attachment_id: int,
    payload: AttachmentReviewPayload,
    db: Session = Depends(get_db),
):
    item = db.query(AIResultAttachment).filter(AIResultAttachment.id == attachment_id).first()
    if item is None:
        raise HTTPException(404, "AI 结果文件不存在")
    if item.status != "pending_review":
        raise HTTPException(409, "该文件已经审核，不能重复处理")
    if payload.action == "reject":
        item.status = "rejected"
        item.review_note = payload.note
        item.reviewed_at = datetime.utcnow()
        db.commit()
        return {"success": True, "attachment": attachment_dict(item), "message": "已拒绝该文件，未加入正式素材库。"}
    try:
        path = safe_storage_file(item.file_path)
    except Exception as exc:
        raise HTTPException(410, f"待审核文件已不存在或路径无效：{exc}")
    media = MediaFile(
        name=item.original_filename,
        category=item.media_type,
        file_path=str(path),
        mime_type=item.mime_type,
        file_size=item.file_size,
        metadata_json={
            "source": "web_ai_result",
            "novel_id": item.novel_id,
            "chapter_number": item.chapter_number,
            "attachment_id": item.id,
            "sha256": item.sha256,
            "description": item.description,
            "source_task_key": item.source_task_key,
        },
    )
    db.add(media)
    db.flush()
    item.status = "approved"
    item.media_file_id = media.id
    item.review_note = payload.note
    item.reviewed_at = datetime.utcnow()
    db.commit()
    return {"success": True, "attachment": attachment_dict(item), "media_file_id": media.id,
            "message": "已审核通过并加入素材媒体库。"}

def proposal_dict(item):
    return {
        "id": item.id, "novel_id": item.novel_id, "chapter_number": item.chapter_number,
        "proposal_type": item.proposal_type, "asset_name": item.asset_name,
        "asset_category": item.asset_category, "change_type": item.change_type,
        "event": item.event, "appearance_after": item.appearance_after,
        "clothing_after": item.clothing_after, "age_after": item.age_after,
        "hairstyle_after": item.hairstyle_after, "injury_after": item.injury_after,
        "equipment_after": item.equipment_after, "condition_after": item.condition_after,
        "holder": item.holder, "evidence": item.evidence, "confidence": item.confidence,
        "status": item.status, "review_note": item.review_note,
        "applied_asset_id": item.applied_asset_id,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


def get_novel_chapter(db, novel_id, number):
    return db.query(Chapter).filter(
        Chapter.novel_id == novel_id,
        Chapter.chapter_number == number,
    ).first()


def evidence_matches_chapter(chapter, evidence):
    content = str(getattr(chapter, "content", "") or "")
    quote = str(evidence or "").strip()
    return bool(quote) and quote in content


def chapter_exists(db, novel_id, number):
    return get_novel_chapter(db, novel_id, number) is not None



def asset_belongs_to_novel(asset, novel_id):
    data = asset.data if isinstance(asset.data, dict) else {}
    owner = data.get("source_novel_id")
    if owner in (None, ""):
        return True
    try:
        return int(owner) == int(novel_id)
    except (TypeError, ValueError):
        return False


def resolve_asset_state(asset, chapter_number=None):
    data = asset.data if isinstance(asset.data, dict) else {}
    history = [x for x in (data.get("evolution_history") or []) if isinstance(x, dict)]
    history = [x for x in history if x.get("confirmed", True) is True]
    history.sort(key=lambda x: (int(x.get("chapter_number") or 0), str(x.get("recorded_at") or "")))
    applicable = [x for x in history if chapter_number is None or int(x.get("chapter_number") or 0) <= chapter_number]
    state = {
        "chapter_number": None, "event": "", "change_type": "基础状态",
        "appearance": asset.appearance or "", "clothing": asset.clothing or "",
        "age": "", "hairstyle": "", "injury": "", "equipment": "",
        "condition": "", "holder": "", "confirmed": True, "is_evolved": False,
    }
    fields = {
        "appearance_after": "appearance", "clothing_after": "clothing",
        "age_after": "age", "hairstyle_after": "hairstyle",
        "injury_after": "injury", "equipment_after": "equipment",
        "condition_after": "condition", "holder": "holder",
    }
    for item in applicable:
        for source, target in fields.items():
            value = item.get(source)
            if isinstance(value, str) and value.strip():
                state[target] = value.strip()
        state.update({
            "chapter_number": item.get("chapter_number"),
            "event": item.get("event", ""),
            "change_type": item.get("change_type", "状态变化"),
            "confirmed": bool(item.get("confirmed", True)),
            "is_evolved": True,
        })
    return state


class HandoffPackageRequest(BaseModel):
    # Explicit acknowledgement: this endpoint returns selected novel text to the
    # caller so the user can review it before copying it to a third-party AI site.
    confirm_selected_chapter: bool = False
    include_confirmed_analysis: bool = True
    task_type: Literal["剧情分析", "剧本改编", "分镜设计", "角色设定", "场景设定", "道具设定", "对白整理"] = "剧情分析"
    max_context_chars: int = Field(default=120000, ge=1000, le=150000)


@router.post("/novels/{novel_id}/chapters/{chapter_number}/handoff-package")
def build_chapter_handoff_package(
    novel_id: int,
    chapter_number: int,
    payload: HandoffPackageRequest,
    db: Session = Depends(get_db),
):
    if not payload.confirm_selected_chapter:
        raise HTTPException(400, "请先在软件界面确认本次要交接的章节内容")
    novel = db.query(Novel).filter(Novel.id == novel_id).first()
    if novel is None:
        raise HTTPException(404, "小说不存在")
    chapter = get_novel_chapter(db, novel_id, chapter_number)
    if chapter is None:
        raise HTTPException(404, "章节不存在")
    if not (chapter.content or "").strip():
        raise HTTPException(422, "章节正文为空，无法生成交接包")

    analysis = []
    if payload.include_confirmed_analysis:
        records = db.query(AnalysisItem).filter(
            AnalysisItem.novel_id == novel_id,
            AnalysisItem.confirmed.is_(True),
        ).order_by(AnalysisItem.category, AnalysisItem.id).limit(300).all()
        analysis = [{
            "category": item.category,
            "name": item.name,
            "description": item.description or "",
            "data": item.data if isinstance(item.data, dict) else {},
        } for item in records]

    chapter_text = chapter.content[:payload.max_context_chars]
    truncated = len(chapter.content) > len(chapter_text)
    output_schema = {
        "type": "object",
        "required": ["chapter_number", "events", "characters", "scenes", "props", "relationships", "timeline", "proposals", "uncertainties"],
        "properties": {
            "chapter_number": {"type": "integer"},
            "events": {"type": "array", "items": {"type": "object", "required": ["event", "evidence"]}},
            "characters": {"type": "array", "items": {"type": "object", "required": ["name", "evidence"]}},
            "scenes": {"type": "array", "items": {"type": "object", "required": ["name", "evidence"]}},
            "props": {"type": "array", "items": {"type": "object", "required": ["name", "evidence"]}},
            "relationships": {"type": "array"},
            "timeline": {"type": "array"},
            "proposals": {"type": "array", "items": {
                "type": "object",
                "required": ["chapter_number", "asset_name", "asset_category", "event", "evidence", "confidence"],
                "properties": {
                    "chapter_number": {"type": "integer"},
                    "asset_name": {"type": "string"},
                    "asset_category": {"type": "string"},
                    "change_type": {"type": "string"},
                    "event": {"type": "string"},
                    "appearance_after": {"type": "string"},
                    "clothing_after": {"type": "string"},
                    "age_after": {"type": "string"},
                    "hairstyle_after": {"type": "string"},
                    "injury_after": {"type": "string"},
                    "equipment_after": {"type": "string"},
                    "condition_after": {"type": "string"},
                    "holder": {"type": "string"},
                    "evidence": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1}
                }
            }},
            "uncertainties": {"type": "array", "items": {"type": "string"}},
        },
    }
    task_schemas = {
        "剧本改编": {
            "type": "object",
            "required": ["chapter_number", "title", "synopsis", "screenplay"],
            "properties": {
                "chapter_number": {"type": "integer"}, "title": {"type": "string"},
                "synopsis": {"type": "string"}, "screenplay": {"type": "string"},
                "characters": {"type": "array"}, "scenes": {"type": "array"}, "dialogues": {"type": "array"},
            },
        },
        "分镜设计": {
            "type": "object",
            "required": ["chapter_number", "shots"],
            "properties": {
                "chapter_number": {"type": "integer"}, "shots": {"type": "array"},
            },
        },
        "角色设定": {
            "type": "object",
            "required": ["chapter_number", "characters"],
            "properties": {
                "chapter_number": {"type": "integer"}, "characters": {"type": "array"},
            },
        },
        "场景设定": {
            "type": "object",
            "required": ["chapter_number", "scenes"],
            "properties": {
                "chapter_number": {"type": "integer"}, "scenes": {"type": "array"},
            },
        },
        "道具设定": {
            "type": "object",
            "required": ["chapter_number", "props"],
            "properties": {
                "chapter_number": {"type": "integer"},
                "props": {"type": "array", "items": {"type": "object", "required": ["name", "description", "image_prompt"]}},
            },
        },
        "对白整理": {
            "type": "object",
            "required": ["chapter_number", "dialogues"],
            "properties": {
                "chapter_number": {"type": "integer"}, "dialogues": {"type": "array"},
            },
        },
    }
    if payload.task_type in task_schemas:
        output_schema = task_schemas[payload.task_type]
    task_instructions = {
        "剧情分析": "分析剧情事件、人物、场景、道具、关系、时间线及有证据的变化提议。",
        "剧本改编": "将本章改编为可拍摄的漫剧剧本，保留核心剧情，输出场次、动作、对白和场景说明。",
        "分镜设计": "将本章拆解为连续镜头，明确景别、运镜、时长、画面、人物动作、对白、音效和绘图提示词。",
        "角色设定": "提取本章出现的角色，整理外貌、服装、性格、能力及原文证据；为每个主要角色输出正面、侧面、全身和表情设定图提示词；未知内容标注未知。",
        "场景设定": "提取本章场景，整理空间结构、环境、氛围、时间、光线及原文证据，并为每个重要场景输出独立环境概念图提示词。",
        "道具设定": "提取本章出现的重要道具、武器、服装和特殊物品，整理名称、持有人、外观、材质、尺寸、用途、剧情状态及原文证据；为每件道具输出独立、可直接用于图片模型的设定图提示词和负面提示词。",
        "对白整理": "按角色整理本章对白，保留原意，标注情绪和场景，并提供原文证据。",
    }
    instruction = (
        f"你是专业的小说漫剧改编与制作助手。本次任务类型：{payload.task_type}。"
        f"{task_instructions[payload.task_type]}"
        "只处理本次交接包中的指定章节，并结合已确认资料。严格区分原文事实、推断和未知；"
        "所有从原文提取的事实必须附带逐字证据，不得把推断当作事实。"
        "只返回符合给定JSON Schema的JSON，不要使用Markdown代码围栏。"
        "如内容无法从原文确定，请标注未知或写入uncertainties。"
    )
    context = {
        "novel": {"id": novel.id, "title": novel.title, "genre": novel.genre},
        "chapter": {
            "id": chapter.id,
            "number": chapter.chapter_number,
            "title": chapter.title,
            "word_count": chapter.word_count,
            "content": chapter_text,
            "truncated": truncated,
        },
        "confirmed_analysis": analysis,
        "handoff_scope": "仅本次选定章节；不包含整本小说其他章节原文",
        "task_type": payload.task_type,
    }
    return {
        "success": True,
        "handoff_version": "1.1",
        "task_type": payload.task_type,
        "novel_id": novel.id,
        "chapter_id": chapter.id,
        "chapter_number": chapter.chapter_number,
        "content_chars": len(chapter_text),
        "truncated": truncated,
        "user_review_required": True,
        "third_party_transfer_started": False,
        "instruction": instruction,
        "output_schema": output_schema,
        "context": context,
        "notice": "这是交接预览数据包；本接口不会打开网页、发送内容给第三方或自动产生费用。",
    }


@router.get("/prompt-schema")
def prompt_schema():
    return {
        "version": "1.0",
        "required_fields": ["chapter_number", "asset_name", "asset_category", "event", "evidence", "confidence"],
        "asset_categories": ["characters", "scenes", "props", "weapons", "costumes", "buildings", "special_items"],
        "optional_state_fields": ["age_after", "hairstyle_after", "injury_after", "equipment_after", "condition_after"],
        "rules": ["只输出JSON对象，proposals为数组", "evidence必须逐字引用输入原文", "没有明确变化时不要推测", "confidence为0到1之间数字", "人物年龄/发型/伤势和道具装备状态应分别填写对应字段，未知字段留空"],
        "example": {"proposals": [{"chapter_number": 3, "asset_name": "主角", "asset_category": "characters", "change_type": "装备变化", "event": "战斗后获得护腕", "appearance_after": "", "clothing_after": "", "age_after": "", "hairstyle_after": "", "injury_after": "", "equipment_after": "银色护腕", "condition_after": "", "holder": "主角", "evidence": "原文中逐字引用的证据", "confidence": 0.92}]},
    }


@router.post("/novels/{novel_id}/proposals/import")
def import_proposals(novel_id: int, payload: ImportPayload, db: Session = Depends(get_db)):
    if payload.novel_id != novel_id:
        raise HTTPException(400, "小说编号不一致")
    if db.query(Novel).filter(Novel.id == novel_id).first() is None:
        raise HTTPException(404, "小说不存在")
    created, duplicates = [], 0
    for proposal in payload.proposals:
        chapter = get_novel_chapter(db, novel_id, proposal.chapter_number)
        if chapter is None:
            raise HTTPException(422, f"第 {proposal.chapter_number} 章不存在")
        # Validate against the persisted chapter, never trust client-supplied source_text
        # as proof: a client could otherwise submit fabricated text and matching quotes.
        if not evidence_matches_chapter(chapter, proposal.evidence):
            raise HTTPException(422, f"证据未在小说数据库第 {proposal.chapter_number} 章原文中找到：{proposal.asset_name}")
        duplicate = db.query(AIStoryProposal).filter(
            AIStoryProposal.novel_id == novel_id,
            AIStoryProposal.chapter_number == proposal.chapter_number,
            AIStoryProposal.asset_name == proposal.asset_name,
            AIStoryProposal.event == proposal.event,
            AIStoryProposal.evidence == proposal.evidence,
        ).first()
        if duplicate:
            duplicates += 1
            continue
        item = AIStoryProposal(
            novel_id=novel_id, chapter_number=proposal.chapter_number,
            asset_name=proposal.asset_name.strip(), asset_category=proposal.asset_category,
            change_type=proposal.change_type.strip(), event=proposal.event.strip(),
            appearance_after=proposal.appearance_after.strip(),
            clothing_after=proposal.clothing_after.strip(),
            age_after=proposal.age_after.strip(), hairstyle_after=proposal.hairstyle_after.strip(),
            injury_after=proposal.injury_after.strip(), equipment_after=proposal.equipment_after.strip(),
            condition_after=proposal.condition_after.strip(), holder=proposal.holder.strip(),
            evidence=proposal.evidence.strip(), confidence=proposal.confidence,
            status="pending_review", source_task_key=payload.source_task_key,
        )
        db.add(item)
        db.flush()
        created.append(proposal_dict(item))
    db.commit()
    return {"success": True, "created": created, "created_count": len(created), "duplicates_skipped": duplicates, "message": "AI提议已进入待审核列表，尚未写入正式素材设定。"}


@router.get("/novels/{novel_id}/proposals")
def list_proposals(novel_id: int, status: str = "pending_review", db: Session = Depends(get_db)):
    if db.query(Novel).filter(Novel.id == novel_id).first() is None:
        raise HTTPException(404, "小说不存在")
    query = db.query(AIStoryProposal).filter(AIStoryProposal.novel_id == novel_id)
    if status != "all":
        query = query.filter(AIStoryProposal.status == status)
    items = query.order_by(AIStoryProposal.chapter_number, AIStoryProposal.id).limit(1000).all()
    return {"items": [proposal_dict(item) for item in items]}


@router.post("/proposals/{proposal_id}/review")
def review_proposal(proposal_id: int, payload: ReviewPayload, db: Session = Depends(get_db)):
    item = db.query(AIStoryProposal).filter(AIStoryProposal.id == proposal_id).first()
    if item is None:
        raise HTTPException(404, "剧情变化提议不存在")
    if item.status != "pending_review":
        raise HTTPException(409, "该提议已经审核，不能重复处理")
    if payload.action == "reject":
        item.status, item.review_note, item.reviewed_at = "rejected", payload.note, datetime.utcnow()
        db.commit()
        return {"success": True, "proposal": proposal_dict(item)}
    candidates = db.query(VisualAsset).filter(
        VisualAsset.name == item.asset_name,
        VisualAsset.category == item.asset_category,
    ).all()
    # Never attach an AI-proposed change to a same-named asset from another
    # novel or to an unscoped manually-created asset.
    asset = next((
        x for x in candidates
        if isinstance(x.data, dict) and x.data.get("source_novel_id") == item.novel_id
    ), None)
    if asset is None:
        raise HTTPException(404, f"素材库中找不到该小说的{item.asset_name}（{item.asset_category}），请先建立或导入素材")
    data = dict(asset.data) if isinstance(asset.data, dict) else {}
    history = list(data.get("evolution_history") or [])
    same = any(
        isinstance(entry, dict)
        and int(entry.get("chapter_number") or 0) == item.chapter_number
        and entry.get("event") == item.event
        and entry.get("evidence") == item.evidence
        for entry in history
    )
    if not same:
        history.append({
            "chapter_number": item.chapter_number, "event": item.event,
            "change_type": item.change_type, "appearance_after": item.appearance_after,
            "clothing_after": item.clothing_after, "age_after": item.age_after,
            "hairstyle_after": item.hairstyle_after, "injury_after": item.injury_after,
            "equipment_after": item.equipment_after, "condition_after": item.condition_after,
            "holder": item.holder, "evidence": item.evidence, "confirmed": True,
            "source": "ai_web_proposal", "proposal_id": item.id,
            "recorded_at": datetime.utcnow().isoformat(),
        })
        history.sort(key=lambda x: (int(x.get("chapter_number") or 0), str(x.get("recorded_at") or "")))
        data["evolution_history"] = history
        asset.data = data
        data["current_state"] = resolve_asset_state(asset)
        asset.data = data
        asset.updated_at = datetime.utcnow()
    item.status, item.review_note, item.reviewed_at = "approved", payload.note, datetime.utcnow()
    item.applied_asset_id = asset.id
    db.commit()
    return {"success": True, "proposal": proposal_dict(item), "asset_id": asset.id, "message": "已确认并写入素材剧情版本历史。"}


@router.post("/storyboard/match-suggestions")
def storyboard_match_suggestions(payload: MatchPayload, db: Session = Depends(get_db)):
    shot = db.query(StoryboardShot).filter(StoryboardShot.id == payload.shot_id).first()
    if shot is None:
        raise HTTPException(404, "分镜镜头不存在")
    names = [x.strip() for x in payload.names if x and x.strip()]
    if not names:
        names = [x for x in [shot.scene_name] if x]
    project = db.query(StoryProject).filter(StoryProject.id == shot.project_id).first()
    if project is None:
        raise HTTPException(404, "漫剧项目不存在")
    # Assets from another novel must never leak into this project's suggestions.
    # Unscoped manually-created assets remain selectable for backward compatibility.
    all_assets = db.query(VisualAsset).filter(VisualAsset.confirmed.is_(True)).all()
    assets = [asset for asset in all_assets if asset_belongs_to_novel(asset, project.novel_id)]
    suggestions = []
    for name in names:
        key = re.sub(r"\s+", "", name).lower()
        ranked = []
        for asset in assets:
            asset_name = re.sub(r"\s+", "", asset.name).lower()
            if not key:
                continue
            score = 1.0 if key == asset_name else (0.82 if key in asset_name or asset_name in key else 0.0)
            data = asset.data if isinstance(asset.data, dict) else {}
            aliases = [re.sub(r"\s+", "", str(x)).lower() for x in data.get("aliases", [])] if isinstance(data.get("aliases"), list) else []
            if key in aliases:
                score = max(score, 0.95)
            if score:
                state = resolve_asset_state(asset, payload.chapter_number)
                ranked.append((score, asset, state))
        ranked.sort(key=lambda x: (-x[0], x[1].id))
        suggestions.append({"requested_name": name, "matches": [{
            "asset_id": asset.id, "name": asset.name, "category": asset.category,
            "confidence": score, "chapter_number": payload.chapter_number,
            "effective_state": state,
            "reason": "同一小说或未绑定小说的素材名称/别名匹配；状态已按目标章节计算，请人工确认",
        } for score, asset, state in ranked[:5]], "missing": not bool(ranked)})
    return {"shot_id": shot.id, "suggestions": suggestions, "requires_user_confirmation": True}


class ConfirmMatchPayload(BaseModel):
    shot_id: int
    asset_id: int
    role: Literal["character", "scene", "prop", "weapon", "costume", "building", "special_item"]
    chapter_number: int | None = Field(default=None, ge=1)


@router.post("/storyboard/confirm-match")
def confirm_storyboard_match(payload: ConfirmMatchPayload, db: Session = Depends(get_db)):
    shot = db.query(StoryboardShot).filter(StoryboardShot.id == payload.shot_id).first()
    if shot is None:
        raise HTTPException(404, "分镜镜头不存在")
    project = db.query(StoryProject).filter(StoryProject.id == shot.project_id).first()
    if project is None:
        raise HTTPException(404, "漫剧项目不存在")
    asset = db.query(VisualAsset).filter(VisualAsset.id == payload.asset_id).first()
    if asset is None or not asset.confirmed:
        raise HTTPException(404, "素材不存在或尚未确认")
    if not asset_belongs_to_novel(asset, project.novel_id):
        raise HTTPException(403, "该素材属于其他小说，不能关联到当前漫剧项目")
    if payload.chapter_number is not None and not chapter_exists(db, project.novel_id, payload.chapter_number):
        raise HTTPException(422, "章节编号不属于当前小说")
    snapshot = resolve_asset_state(asset, payload.chapter_number)
    link = db.query(StoryAssetLink).filter(
        StoryAssetLink.shot_id == shot.id,
        StoryAssetLink.asset_id == asset.id,
        StoryAssetLink.role == payload.role,
    ).first()
    if link is None:
        link = StoryAssetLink(
            project_id=shot.project_id, episode_id=shot.episode_id,
            shot_id=shot.id, asset_id=asset.id, role=payload.role,
        )
        db.add(link)
    link.chapter_number = payload.chapter_number
    link.state_snapshot = snapshot
    db.commit()
    db.refresh(link)
    return {"success": True, "link_id": link.id, "shot_id": shot.id, "asset_id": asset.id, "chapter_number": payload.chapter_number, "state_snapshot": snapshot}
