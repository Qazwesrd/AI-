from datetime import datetime
from typing import Literal
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.novel import Novel, Chapter, AnalysisItem
from app.models.visual import VisualAsset
from app.models.story import StoryProject, EpisodePlan, StoryboardShot, StoryAssetLink
from app.models.ai_story_collaboration import AIStoryProposal

router = APIRouter(prefix="/api/ai-collaboration", tags=["网页版AI协同创作"])


class ProposalInput(BaseModel):
    chapter_number: int = Field(ge=1)
    asset_name: str = Field(min_length=1, max_length=200)
    asset_category: Literal["characters", "scenes", "props", "weapons", "costumes", "buildings", "special_items"] = "characters"
    change_type: str = Field(default="状态变化", max_length=80)
    event: str = Field(min_length=2, max_length=2000)
    appearance_after: str = Field(default="", max_length=4000)
    clothing_after: str = Field(default="", max_length=4000)
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


def proposal_dict(item):
    return {
        "id": item.id, "novel_id": item.novel_id, "chapter_number": item.chapter_number,
        "proposal_type": item.proposal_type, "asset_name": item.asset_name,
        "asset_category": item.asset_category, "change_type": item.change_type,
        "event": item.event, "appearance_after": item.appearance_after,
        "clothing_after": item.clothing_after, "holder": item.holder,
        "evidence": item.evidence, "confidence": item.confidence,
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
    history.sort(key=lambda x: (int(x.get("chapter_number") or 0), str(x.get("recorded_at") or "")))
    applicable = [x for x in history if chapter_number is None or int(x.get("chapter_number") or 0) <= chapter_number]
    latest = applicable[-1] if applicable else {}
    return {
        "chapter_number": latest.get("chapter_number"),
        "event": latest.get("event", ""),
        "change_type": latest.get("change_type", "基础状态"),
        "appearance": latest.get("appearance_after") or asset.appearance or "",
        "clothing": latest.get("clothing_after") or asset.clothing or "",
        "holder": latest.get("holder", ""),
        "confirmed": bool(latest.get("confirmed", False)),
        "is_evolved": bool(latest),
    }


class HandoffPackageRequest(BaseModel):
    # Explicit acknowledgement: this endpoint returns selected novel text to the
    # caller so the user can review it before copying it to a third-party AI site.
    confirm_selected_chapter: bool = False
    include_confirmed_analysis: bool = True
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
            "proposals": {"type": "array", "items": {"type": "object", "required": ["chapter_number", "asset_name", "asset_category", "event", "evidence", "confidence"]}},
            "uncertainties": {"type": "array", "items": {"type": "string"}},
        },
    }
    instruction = (
        "你是小说与影视改编分析专家。请只分析本次交接包中的指定章节，并结合已确认资料识别变化。"
        "严格区分原文事实、推断和未知；不得把推断当作事实。"
        "每一项人物/场景/道具/事件及剧情变化都必须附带本章原文中的逐字证据。"
        "没有证据的变化不得提交；置信度使用0到1。"
        "只返回符合给定JSON Schema的JSON，不要使用Markdown代码围栏。"
        "proposals仅用于提交待审核的变化提议，不代表正式素材已经更新。"
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
    }
    return {
        "success": True,
        "handoff_version": "1.0",
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
        "rules": ["只输出JSON对象，proposals为数组", "evidence必须逐字引用输入原文", "没有明确变化时不要推测", "confidence为0到1之间数字"],
        "example": {"proposals": [{"chapter_number": 3, "asset_name": "主角", "asset_category": "characters", "change_type": "服装变化", "event": "战斗后更换衣服", "appearance_after": "", "clothing_after": "黑色破损长袍", "holder": "", "evidence": "原文中逐字引用的证据", "confidence": 0.92}]},
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
            clothing_after=proposal.clothing_after.strip(), holder=proposal.holder.strip(),
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
            "clothing_after": item.clothing_after, "holder": item.holder,
            "evidence": item.evidence, "confirmed": True,
            "source": "ai_web_proposal", "proposal_id": item.id,
            "recorded_at": datetime.utcnow().isoformat(),
        })
        history.sort(key=lambda x: (int(x.get("chapter_number") or 0), str(x.get("recorded_at") or "")))
        data["evolution_history"] = history
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
    data = asset.data if isinstance(asset.data, dict) else {}
    history = [x for x in (data.get("evolution_history") or []) if isinstance(x, dict)]
    history.sort(key=lambda x: (int(x.get("chapter_number") or 0), str(x.get("recorded_at") or "")))
    applicable = [x for x in history if payload.chapter_number is None or int(x.get("chapter_number") or 0) <= payload.chapter_number]
    latest = applicable[-1] if applicable else {}
    snapshot = {
        "chapter_number": latest.get("chapter_number"),
        "event": latest.get("event", ""),
        "change_type": latest.get("change_type", "基础状态"),
        "appearance": latest.get("appearance_after") or asset.appearance or "",
        "clothing": latest.get("clothing_after") or asset.clothing or "",
        "holder": latest.get("holder", ""),
        "confirmed": bool(latest.get("confirmed", False)),
        "is_evolved": bool(latest),
    }
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
