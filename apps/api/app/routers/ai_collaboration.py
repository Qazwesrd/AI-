from datetime import datetime
from typing import Literal
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.novel import Novel, Chapter
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


def chapter_exists(db, novel_id, number):
    rows = db.query(Chapter).filter(Chapter.novel_id == novel_id).all()
    return any(int(getattr(row, "chapter_number", 0) or 0) == number for row in rows)


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
        if not chapter_exists(db, novel_id, proposal.chapter_number):
            raise HTTPException(422, f"第 {proposal.chapter_number} 章不存在")
        if proposal.evidence.strip() not in payload.source_text:
            raise HTTPException(422, f"证据未在提交的原文中找到：{proposal.asset_name}")
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
    asset = next((x for x in candidates if isinstance(x.data, dict) and x.data.get("source_novel_id") in (None, item.novel_id)), None)
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
    assets = db.query(VisualAsset).filter(VisualAsset.confirmed.is_(True)).all()
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
            aliases = [str(x).lower() for x in data.get("aliases", [])] if isinstance(data.get("aliases"), list) else []
            if key in aliases:
                score = max(score, 0.95)
            if score:
                ranked.append((score, asset))
        ranked.sort(key=lambda x: (-x[0], x[1].id))
        suggestions.append({"requested_name": name, "matches": [{
            "asset_id": asset.id, "name": asset.name, "category": asset.category,
            "confidence": score, "chapter_number": payload.chapter_number,
            "reason": "名称或别名匹配；请核对剧情时间点和素材版本",
        } for score, asset in ranked[:5]], "missing": not bool(ranked)})
    return {"shot_id": shot.id, "suggestions": suggestions, "requires_user_confirmation": True}
