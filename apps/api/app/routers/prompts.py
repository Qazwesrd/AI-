from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db, SessionLocal
from app.models.prompts import (
    PromptCategory, PromptTemplate, PromptVersion, PromptTestRun,
    PromptRankingSnapshot, PublicResearchSource, PublicResearchCollectionRun,
)
from app.services.public_source_collector import (
    FeedCollectionError, fetch_public_feed, parse_observed_date, validate_public_feed_url,
)

router = APIRouter(prefix="/api/prompts", tags=["提示词中心"])

DEFAULT_PROMPTS = [
    ("全书结构与爆点拆解", "小说分析", "你是一名资深长篇小说改编策划。只依据原文拆解世界观、主线支线、人物关系与动机、关键转折、伏笔、冲突升级、情绪曲线和视觉化场面。结论标注章节依据；不得凭空补充原著事实。"),
    ("长篇动态分集规划", "全书大纲", "按剧情自然节奏规划卷、季、集，不设固定集数上限。每集输出集名、核心事件、人物目标、冲突、情绪峰值、反转、结尾悬念和来源章节。不得凑集数、重复剧情或提前消耗伏笔。"),
    ("强开篇与高留存剧本", "分集剧本", "将剧情改编为可拍摄剧本：尽快呈现人物目标或危机；每场戏有明确冲突和信息推进；反转有前置线索；对白符合人物身份；结尾留下自然悬念。输出场景、时间、人物、动作、对白、情绪、音效和时长。"),
    ("角色形象一致性设定卡", "人物设定", "建立跨集角色设定卡，包含年龄段、脸型五官、发型发色、身材比例、服装配色、配饰、性格气质和声音特征。区分永久特征、可变化状态和当前剧情状态；变化注明触发事件和生效集数。"),
    ("场景与美术设定", "场景设定", "将原文场景转为复用美术设定：空间结构、时代地域、材质、主辅色、光线、天气、时间、关键道具、人物活动区域和镜头限制。保持同一地点跨镜头的空间连续性。"),
    ("镜头可执行分镜", "分镜生成", "拆为可执行镜头，每镜输出镜号、景别、机位、构图、镜头运动、画面主体、人物动作、表情、对白、音效、时长、转场和角色/场景引用。动作必须能在指定时长内完成，相邻镜头保持连续。"),
    ("图像生成提示词", "图片生成", "根据已确认角色卡和场景卡生成图像提示词，明确主体、外观、服装、动作、场景、构图、镜头、光线、色彩、画风和比例；约束多余人物、文字水印、畸形肢体和角色外观漂移。"),
    ("视频镜头运动提示词", "视频生成", "将分镜转为视频生成指令，明确首帧、主体动作、方向速度、镜头运动、环境动态、表情变化、结束状态和时长。动作连续、幅度可控，保持角色与场景参考一致。"),
    ("多语言平台发行文案", "多语言发行", "根据作品真实内容生成标题、简介、标签、内容分级提示和封面短句，提供中文、英语、西班牙语版本。专有名词译法一致，不夸大剧情，不虚构获奖或播放成绩。"),
]

DEFAULT_CATEGORIES = [
    "小说分析", "全书大纲", "分集剧本", "人物设定", "场景设定",
    "图片生成", "分镜生成", "视频生成", "配音字幕", "剪辑合成",
    "封面标题", "多语言发行", "自定义",
]


class CategoryInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)


class PromptInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    category_id: Optional[int] = None
    content: str = Field(min_length=1, max_length=50000)
    notes: str = Field(default="", max_length=10000)
    tags: list[str] = Field(default_factory=list)
    change_reason: str = Field(default="创建提示词", max_length=2000)


class PromptUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    category_id: Optional[int] = None
    content: Optional[str] = Field(default=None, min_length=1, max_length=50000)
    notes: Optional[str] = Field(default=None, max_length=10000)
    tags: Optional[list[str]] = None
    change_reason: str = Field(default="编辑提示词", max_length=2000)


class TestInput(BaseModel):
    prompt_version: Optional[int] = None
    dataset_name: str = Field(default="手动实测", max_length=200)
    model_name: str = Field(default="未注明", max_length=200)
    score_overall: float = Field(ge=0, le=100)
    score_story: float = Field(default=0, ge=0, le=100)
    score_consistency: float = Field(default=0, ge=0, le=100)
    score_executability: float = Field(default=0, ge=0, le=100)
    score_visual: float = Field(default=0, ge=0, le=100)
    notes: str = Field(default="", max_length=10000)
    evaluator: str = Field(default="用户", max_length=100)


def category_dict(row):
    return {"id": row.id, "name": row.name, "description": row.description,
            "is_system": row.is_system, "created_at": row.created_at.isoformat() if row.created_at else None}


def prompt_dict(row, category_name=""):
    return {"id": row.id, "name": row.name, "category_id": row.category_id,
            "category_name": category_name, "content": row.content, "notes": row.notes,
            "tags": row.tags or [], "current_version": row.current_version,
            "is_active": bool(row.is_active), "is_system": bool(row.is_system),
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None}


def ensure_default_categories(db):
    existing = {x.name for x in db.query(PromptCategory).all()}
    changed = False
    for name in DEFAULT_CATEGORIES:
        if name not in existing:
            db.add(PromptCategory(name=name, is_system=True))
            changed = True
    if changed:
        db.commit()


def ensure_default_prompts(db):
    ensure_default_categories(db)
    category_map = {x.name: x.id for x in db.query(PromptCategory).all()}
    existing = {x.name for x in db.query(PromptTemplate).all()}
    for name, category_name, content in DEFAULT_PROMPTS:
        if name in existing:
            continue
        row = PromptTemplate(name=name, category_id=category_map.get(category_name),
                             content=content, notes="内置基础模板，可自行编辑；修改后自动保留版本。",
                             tags=["内置模板"], current_version=1, is_system=True)
        db.add(row)
        db.flush()
        db.add(PromptVersion(prompt_id=row.id, version=1, name=row.name,
                             content=row.content, notes=row.notes,
                             change_reason="内置初始模板"))
    db.commit()


@router.get("/categories")
def list_categories(db: Session = Depends(get_db)):
    ensure_default_categories(db)
    rows = db.query(PromptCategory).order_by(PromptCategory.id).all()
    return {"items": [category_dict(x) for x in rows]}


@router.post("/categories")
def create_category(data: CategoryInput, db: Session = Depends(get_db)):
    name = data.name.strip()
    if not name:
        raise HTTPException(400, "分类名称不能为空")
    if db.query(PromptCategory).filter(PromptCategory.name == name).first():
        raise HTTPException(409, "该分类已经存在")
    row = PromptCategory(name=name, description=data.description.strip(), is_system=False)
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"success": True, "item": category_dict(row)}


@router.get("")
def list_prompts(q: str = "", category_id: Optional[int] = None, db: Session = Depends(get_db)):
    ensure_default_prompts(db)
    query = db.query(PromptTemplate).filter(PromptTemplate.is_active.is_(True))
    if q.strip():
        needle = f"%{q.strip()}%"
        query = query.filter((PromptTemplate.name.like(needle)) | (PromptTemplate.content.like(needle)) | (PromptTemplate.notes.like(needle)))
    if category_id is not None:
        query = query.filter(PromptTemplate.category_id == category_id)
    rows = query.order_by(PromptTemplate.updated_at.desc(), PromptTemplate.id.desc()).all()
    categories = {x.id: x.name for x in db.query(PromptCategory).all()}
    return {"items": [prompt_dict(x, categories.get(x.category_id, "")) for x in rows]}


@router.post("")
def create_prompt(data: PromptInput, db: Session = Depends(get_db)):
    if data.category_id is not None and not db.query(PromptCategory).filter(PromptCategory.id == data.category_id).first():
        raise HTTPException(404, "提示词分类不存在")
    row = PromptTemplate(name=data.name.strip(), category_id=data.category_id,
                         content=data.content, notes=data.notes, tags=data.tags or [],
                         current_version=1, updated_at=datetime.utcnow())
    db.add(row)
    db.flush()
    db.add(PromptVersion(prompt_id=row.id, version=1, name=row.name, content=row.content,
                         notes=row.notes, change_reason=data.change_reason))
    db.commit()
    db.refresh(row)
    category = db.query(PromptCategory).filter(PromptCategory.id == row.category_id).first() if row.category_id else None
    return {"success": True, "item": prompt_dict(row, category.name if category else "")}


@router.put("/{prompt_id}")
def update_prompt(prompt_id: int, data: PromptUpdate, db: Session = Depends(get_db)):
    row = db.query(PromptTemplate).filter(PromptTemplate.id == prompt_id, PromptTemplate.is_active.is_(True)).first()
    if not row:
        raise HTTPException(404, "提示词不存在")
    if data.category_id is not None and not db.query(PromptCategory).filter(PromptCategory.id == data.category_id).first():
        raise HTTPException(404, "提示词分类不存在")
    changes = data.model_dump(exclude_unset=True, exclude={"change_reason"})
    changed = any(getattr(row, key) != value for key, value in changes.items())
    for key, value in changes.items():
        setattr(row, key, value)
    if changed:
        row.current_version += 1
        row.updated_at = datetime.utcnow()
        db.add(PromptVersion(prompt_id=row.id, version=row.current_version, name=row.name,
                             content=row.content, notes=row.notes,
                             change_reason=data.change_reason))
    db.commit()
    category = db.query(PromptCategory).filter(PromptCategory.id == row.category_id).first() if row.category_id else None
    return {"success": True, "item": prompt_dict(row, category.name if category else "")}


@router.delete("/{prompt_id}")
def archive_prompt(prompt_id: int, db: Session = Depends(get_db)):
    row = db.query(PromptTemplate).filter(PromptTemplate.id == prompt_id).first()
    if not row:
        raise HTTPException(404, "提示词不存在")
    row.is_active = False
    row.updated_at = datetime.utcnow()
    db.commit()
    return {"success": True, "message": "提示词已归档，历史版本和实测记录保留"}


@router.get("/{prompt_id}/versions")
def prompt_versions(prompt_id: int, db: Session = Depends(get_db)):
    if not db.query(PromptTemplate).filter(PromptTemplate.id == prompt_id).first():
        raise HTTPException(404, "提示词不存在")
    rows = db.query(PromptVersion).filter(PromptVersion.prompt_id == prompt_id).order_by(PromptVersion.version.desc()).all()
    return {"items": [{"id": x.id, "version": x.version, "name": x.name, "content": x.content,
                       "notes": x.notes, "change_reason": x.change_reason,
                       "created_at": x.created_at.isoformat() if x.created_at else None} for x in rows]}


@router.post("/{prompt_id}/tests")
def add_test_run(prompt_id: int, data: TestInput, db: Session = Depends(get_db)):
    row = db.query(PromptTemplate).filter(PromptTemplate.id == prompt_id).first()
    if not row:
        raise HTTPException(404, "提示词不存在")
    version = data.prompt_version or row.current_version
    if not db.query(PromptVersion).filter(PromptVersion.prompt_id == prompt_id, PromptVersion.version == version).first():
        raise HTTPException(404, "提示词版本不存在")
    run = PromptTestRun(prompt_id=prompt_id, prompt_version=version, **data.model_dump(exclude={"prompt_version"}))
    db.add(run)
    db.commit()
    db.refresh(run)
    snapshot_result = save_category_ranking_snapshots(db, only_if_changed=True)
    return {"success": True, "test_id": run.id, "ranking_snapshots_updated": snapshot_result["created_count"], "message": "实测评分已记录；提示词榜已按新评分自动重算，历史快照仅在排名数据变化时新增。该评分为人工录入，不代表系统已自动生成或验证作品。"}


@router.get("/{prompt_id}/tests")
def list_test_runs(prompt_id: int, db: Session = Depends(get_db)):
    rows = db.query(PromptTestRun).filter(PromptTestRun.prompt_id == prompt_id).order_by(PromptTestRun.created_at.desc()).all()
    return {"items": [{"id": x.id, "prompt_version": x.prompt_version, "dataset_name": x.dataset_name,
                       "model_name": x.model_name, "score_overall": x.score_overall,
                       "score_story": x.score_story, "score_consistency": x.score_consistency,
                       "score_executability": x.score_executability, "score_visual": x.score_visual,
                       "notes": x.notes, "evaluator": x.evaluator,
                       "created_at": x.created_at.isoformat() if x.created_at else None} for x in rows]}


@router.post("/ranking/refresh")
def refresh_ranking(dimension: str = "overall", db: Session = Depends(get_db)):
    allowed = {"overall", "story", "consistency", "executability", "visual"}
    if dimension not in allowed:
        raise HTTPException(400, "不支持的排行榜维度")
    score_field = {"overall": "score_overall", "story": "score_story",
                   "consistency": "score_consistency", "executability": "score_executability",
                   "visual": "score_visual"}[dimension]
    rows = db.query(PromptTestRun).all()
    groups = {}
    for run in rows:
        key = (run.prompt_id, run.prompt_version)
        groups.setdefault(key, []).append(run)
    prompts = {x.id: x for x in db.query(PromptTemplate).all()}
    ranking = []
    for (prompt_id, version), runs in groups.items():
        prompt = prompts.get(prompt_id)
        if not prompt:
            continue
        scores = [float(getattr(run, score_field) or 0) for run in runs]
        ranking.append({"prompt_id": prompt_id, "prompt_name": prompt.name,
                        "version": version, "category_id": prompt.category_id,
                        "test_count": len(runs), "average_score": round(sum(scores) / len(scores), 2),
                        "last_test_at": max((run.created_at for run in runs if run.created_at), default=None).isoformat() if any(run.created_at for run in runs) else None,
                        "score_type": "人工录入实测评分"})
    ranking.sort(key=lambda x: (x["average_score"], x["test_count"]), reverse=True)
    for index, item in enumerate(ranking, start=1):
        item["rank"] = index
    snapshot = PromptRankingSnapshot(dimension=dimension, ranking_data=ranking)
    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)
    return {"success": True, "snapshot_id": snapshot.id, "dimension": dimension,
            "created_at": snapshot.created_at.isoformat() if snapshot.created_at else None,
            "items": ranking,
            "notice": "排行榜根据当前已录入的实测评分即时计算；没有实测记录的提示词不会上榜，排名不自动修改正式提示词库。"}


@router.get("/ranking/history")
def ranking_history(dimension: str = "overall", db: Session = Depends(get_db)):
    rows = db.query(PromptRankingSnapshot).filter(PromptRankingSnapshot.dimension == dimension).order_by(PromptRankingSnapshot.created_at.desc()).limit(50).all()
    return {"items": [{"id": x.id, "dimension": x.dimension,
                       "created_at": x.created_at.isoformat() if x.created_at else None,
                       "ranking": x.ranking_data or []} for x in rows]}


@router.post("/export")
def export_prompts(db: Session = Depends(get_db)):
    rows = db.query(PromptTemplate).filter(PromptTemplate.is_active.is_(True)).all()
    categories = {x.id: x.name for x in db.query(PromptCategory).all()}
    return {"format": "ai-comic-prompts-v1", "exported_at": datetime.utcnow().isoformat() + "Z",
            "categories": [category_dict(x) for x in db.query(PromptCategory).all()],
            "prompts": [prompt_dict(x, categories.get(x.category_id, "")) for x in rows]}


@router.post("/import")
def import_prompts(payload: dict, db: Session = Depends(get_db)):
    if payload.get("format") != "ai-comic-prompts-v1" or not isinstance(payload.get("prompts"), list):
        raise HTTPException(400, "导入文件格式不正确")
    category_map = {x.name: x.id for x in db.query(PromptCategory).all()}
    added = 0
    for item in payload["prompts"]:
        if not isinstance(item, dict) or not str(item.get("name", "")).strip() or not str(item.get("content", "")).strip():
            continue
        category_name = str(item.get("category_name", "")).strip()
        category_id = category_map.get(category_name)
        if category_name and not category_id:
            category = PromptCategory(name=category_name, is_system=False)
            db.add(category)
            db.flush()
            category_id = category.id
            category_map[category_name] = category_id
        row = PromptTemplate(name=str(item["name"])[:200], category_id=category_id,
                             content=str(item["content"]), notes=str(item.get("notes", "")),
                             tags=item.get("tags", []) if isinstance(item.get("tags", []), list) else [],
                             current_version=1)
        db.add(row)
        db.flush()
        db.add(PromptVersion(prompt_id=row.id, version=1, name=row.name, content=row.content,
                             notes=row.notes, change_reason="从导入文件创建"))
        added += 1
    db.commit()
    return {"success": True, "imported_count": added, "message": "已导入为新记录，未覆盖现有提示词。"}


# 爆款研究证据与提示词升级审批：研究记录可以离线手工维护；只有人工核验的多来源证据
# 才能进入升级候选。候选审批与正式提示词版本更新分离，刷新排行榜不会触碰正式库。
from app.models.prompts import TrendEvidence, PromptUpgradeCandidate


class EvidenceInput(BaseModel):
    platform: str = Field(min_length=1, max_length=100)
    work_title: str = Field(min_length=1, max_length=300)
    genre: str = Field(default="未分类", max_length=100)
    work_url: str = Field(default="", max_length=2000)
    observed_at: str = Field(min_length=8, max_length=40)
    metric_summary: dict = Field(default_factory=dict)
    evidence_type: str = Field(default="作品趋势证据", max_length=100)
    reliability: str = Field(default="待核验", max_length=30)


class EvidenceVerifyInput(BaseModel):
    verification_note: str = Field(min_length=3, max_length=5000)
    evidence_type: Optional[str] = Field(default=None, max_length=100)
    genre: Optional[str] = Field(default=None, max_length=100)


class UpgradeCandidateInput(BaseModel):
    prompt_id: int
    base_version: int
    proposed_name: str = Field(min_length=1, max_length=200)
    proposed_content: str = Field(min_length=1, max_length=50000)
    change_reason: str = Field(min_length=3, max_length=5000)
    expected_impact: str = Field(default="", max_length=5000)
    evidence_ids: list[int] = Field(min_length=2, max_length=50)


class CandidateDecisionInput(BaseModel):
    decision_note: str = Field(default="", max_length=5000)


def evidence_dict(row):
    return {
        "id": row.id, "platform": row.platform, "work_title": row.work_title,
        "genre": row.genre or "未分类", "work_url": row.work_url, "observed_at": row.observed_at,
        "metric_summary": row.metric_summary or {}, "evidence_type": row.evidence_type,
        "reliability": row.reliability, "is_verified": bool(row.is_verified),
        "verification_note": row.verification_note,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def candidate_dict(row):
    return {
        "id": row.id, "prompt_id": row.prompt_id, "base_version": row.base_version,
        "proposed_name": row.proposed_name, "proposed_content": row.proposed_content,
        "change_reason": row.change_reason, "expected_impact": row.expected_impact,
        "evidence_ids": row.evidence_ids or [], "status": row.status,
        "decision_note": row.decision_note,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "decided_at": row.decided_at.isoformat() if row.decided_at else None,
    }


@router.get("/research/evidence")
def list_trend_evidence(verified: Optional[bool] = None, db: Session = Depends(get_db)):
    query = db.query(TrendEvidence)
    if verified is not None:
        query = query.filter(TrendEvidence.is_verified.is_(verified))
    rows = query.order_by(TrendEvidence.observed_at.desc(), TrendEvidence.id.desc()).limit(500).all()
    return {"items": [evidence_dict(x) for x in rows],
            "notice": "证据来自公开来源或人工录入；未核验记录不会用于创建升级候选。"}


@router.post("/research/evidence")
def create_trend_evidence(data: EvidenceInput, db: Session = Depends(get_db)):
    row = TrendEvidence(
        platform=data.platform.strip(), work_title=data.work_title.strip(), genre=data.genre.strip() or "未分类",
        work_url=data.work_url.strip(), observed_at=data.observed_at.strip(),
        metric_summary=data.metric_summary, evidence_type=data.evidence_type.strip(),
        reliability=data.reliability.strip(), is_verified=False,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"success": True, "item": evidence_dict(row),
            "message": "证据已保存为待核验状态，不会自动认定为爆款。"}


@router.post("/research/evidence/{evidence_id}/verify")
def verify_trend_evidence(evidence_id: int, data: EvidenceVerifyInput, db: Session = Depends(get_db)):
    row = db.query(TrendEvidence).filter(TrendEvidence.id == evidence_id).first()
    if not row:
        raise HTTPException(404, "研究证据不存在")
    allowed_types = {"漫剧作品趋势证据","作品趋势证据","剧本作品证据","公开提示词分享","创作案例证据","work_trend","screenplay","public_prompt","公开榜单/公开页面"}
    if data.evidence_type is not None:
        if data.evidence_type not in allowed_types:
            raise HTTPException(400,"不支持的证据分类")
        row.evidence_type = data.evidence_type
    if data.genre is not None:
        row.genre = data.genre.strip() or "未分类"
    row.is_verified = True
    row.verification_note = data.verification_note.strip()
    row.reliability = "人工已核验"
    db.commit()
    db.refresh(row)
    snapshot_result = save_category_ranking_snapshots(db, only_if_changed=True)
    return {"success": True, "item": evidence_dict(row), "ranking_snapshots_updated": snapshot_result["created_count"],
            "message": "证据已核验；漫剧/剧本/公开提示词榜已立即重算，变化的榜单已保存历史快照。"}


@router.post("/research/candidates")
def create_upgrade_candidate(data: UpgradeCandidateInput, db: Session = Depends(get_db)):
    prompt = db.query(PromptTemplate).filter(
        PromptTemplate.id == data.prompt_id, PromptTemplate.is_active.is_(True)
    ).first()
    if not prompt:
        raise HTTPException(404, "正式提示词不存在")
    if prompt.current_version != data.base_version:
        raise HTTPException(409, "候选基准版本已过期，请基于当前正式版本重新生成候选")
    evidence_rows = db.query(TrendEvidence).filter(
        TrendEvidence.id.in_(set(data.evidence_ids)),
        TrendEvidence.is_verified.is_(True),
    ).all()
    if len(evidence_rows) != len(set(data.evidence_ids)):
        raise HTTPException(400, "候选只能引用已人工核验的研究证据")
    platforms = {x.platform.strip().lower() for x in evidence_rows}
    dates = {x.observed_at[:10] for x in evidence_rows}
    if len(platforms) < 2 or len(dates) < 2:
        raise HTTPException(400, "爆款证据至少需要两个不同平台、两个不同日期的已核验样本")
    candidate = PromptUpgradeCandidate(
        prompt_id=prompt.id, base_version=data.base_version,
        proposed_name=data.proposed_name.strip(),
        proposed_content=data.proposed_content,
        change_reason=data.change_reason.strip(),
        expected_impact=data.expected_impact.strip(),
        evidence_ids=sorted(set(data.evidence_ids)), status="pending",
    )
    db.add(candidate)
    db.commit()
    db.refresh(candidate)
    return {"success": True, "item": candidate_dict(candidate),
            "message": "升级候选已建立，正式提示词未改变；等待用户明确批准。"}


@router.get("/research/candidates")
def list_upgrade_candidates(status: Optional[str] = None, db: Session = Depends(get_db)):
    query = db.query(PromptUpgradeCandidate)
    if status:
        if status not in {"pending", "approved", "rejected"}:
            raise HTTPException(400, "候选状态无效")
        query = query.filter(PromptUpgradeCandidate.status == status)
    rows = query.order_by(PromptUpgradeCandidate.created_at.desc()).limit(200).all()
    return {"items": [candidate_dict(x) for x in rows]}


@router.post("/research/candidates/{candidate_id}/approve")
def approve_upgrade_candidate(candidate_id: int, data: CandidateDecisionInput, db: Session = Depends(get_db)):
    candidate = db.query(PromptUpgradeCandidate).filter(
        PromptUpgradeCandidate.id == candidate_id
    ).with_for_update().first()
    if not candidate:
        raise HTTPException(404, "升级候选不存在")
    if candidate.status != "pending":
        raise HTTPException(409, "该候选已处理，不能重复审批")
    prompt = db.query(PromptTemplate).filter(
        PromptTemplate.id == candidate.prompt_id,
        PromptTemplate.is_active.is_(True),
    ).with_for_update().first()
    if not prompt:
        raise HTTPException(404, "正式提示词不存在或已归档")
    if prompt.current_version != candidate.base_version:
        raise HTTPException(409, "正式提示词已更新；该候选基准过期，不能覆盖新版本")
    next_version = prompt.current_version + 1
    prompt.name = candidate.proposed_name
    prompt.content = candidate.proposed_content
    prompt.current_version = next_version
    prompt.updated_at = datetime.utcnow()
    db.add(PromptVersion(
        prompt_id=prompt.id, version=next_version, name=prompt.name,
        content=prompt.content, notes=prompt.notes,
        change_reason="用户批准爆款研究升级候选：" + candidate.change_reason,
    ))
    candidate.status = "approved"
    candidate.decision_note = data.decision_note.strip()
    candidate.decided_at = datetime.utcnow()
    db.commit()
    db.refresh(prompt)
    return {"success": True, "candidate": candidate_dict(candidate),
            "prompt": prompt_dict(prompt),
            "message": f"已按用户明确批准创建正式提示词 V{next_version}；旧版本保留。"}


@router.post("/research/candidates/{candidate_id}/reject")
def reject_upgrade_candidate(candidate_id: int, data: CandidateDecisionInput, db: Session = Depends(get_db)):
    candidate = db.query(PromptUpgradeCandidate).filter(
        PromptUpgradeCandidate.id == candidate_id
    ).first()
    if not candidate:
        raise HTTPException(404, "升级候选不存在")
    if candidate.status != "pending":
        raise HTTPException(409, "该候选已处理，不能重复审批")
    candidate.status = "rejected"
    candidate.decision_note = data.decision_note.strip()
    candidate.decided_at = datetime.utcnow()
    db.commit()
    db.refresh(candidate)
    return {"success": True, "item": candidate_dict(candidate),
            "message": "候选已拒绝，正式提示词保持不变。"}


@router.get("/research/trends/ranking")
def research_trend_ranking(db: Session = Depends(get_db)):
    """Rank manually captured, human-verified public trend evidence without treating it as hidden prompts."""
    import math
    from collections import defaultdict
    from datetime import date

    rows = db.query(TrendEvidence).filter(
        TrendEvidence.is_verified.is_(True),
        TrendEvidence.evidence_type.in_({"作品趋势证据","漫剧作品趋势证据","work_trend","公开榜单/公开页面"}),
    ).all()
    groups = defaultdict(list)
    for row in rows:
        # A title is a conservative cross-platform key; URL is retained as evidence, not used to merge platforms.
        key = " ".join((row.work_title or "").casefold().split())
        if key:
            groups[key].append(row)

    today = date.today()
    ranking = []
    metric_weights = {"views": 1.0, "plays": 1.0, "likes": 1.4, "comments": 2.2, "shares": 2.6, "saves": 2.0}
    for key, evidence_rows in groups.items():
        platforms = sorted({x.platform.strip() for x in evidence_rows if x.platform.strip()})
        dates = sorted({(x.observed_at or "")[:10] for x in evidence_rows if len(x.observed_at or "") >= 10})
        # Cross-platform and repeated-day evidence reduces single-source noise.
        if len(platforms) < 2 or len(dates) < 2:
            continue
        weighted_samples = []
        for row in evidence_rows:
            metrics = row.metric_summary if isinstance(row.metric_summary, dict) else {}
            engagement = 0.0
            for metric, weight in metric_weights.items():
                try:
                    value = max(0.0, float(metrics.get(metric, 0) or 0))
                except (TypeError, ValueError):
                    value = 0.0
                engagement += weight * math.log1p(value)
            try:
                observed = date.fromisoformat((row.observed_at or "")[:10])
                age_days = max(0, (today - observed).days)
                recency = math.pow(0.5, age_days / 14.0)
            except ValueError:
                recency = 0.25
            weighted_samples.append(engagement * recency)
        average_signal = sum(weighted_samples) / max(1, len(weighted_samples))
        score = average_signal + min(len(platforms), 5) * 2.0 + min(len(dates), 7) * 1.25
        latest = max(dates) if dates else ""
        ranking.append({
            "work_title": evidence_rows[0].work_title,
            "platforms": platforms,
            "platform_count": len(platforms),
            "observed_days": dates,
            "sample_count": len(evidence_rows),
            "latest_observed_at": latest,
            "trend_score": round(score, 3),
            "evidence_types": sorted({x.evidence_type for x in evidence_rows}),
            "contains_public_prompt": False,
            "evidence_ids": [x.id for x in evidence_rows],
            "score_method": "公开互动指标对数加权 + 14天半衰期 + 跨平台/跨日期加权；不是平台官方热度分",
        })
    ranking.sort(key=lambda x: (x["trend_score"], x["latest_observed_at"]), reverse=True)
    for index, item in enumerate(ranking, start=1):
        item["rank"] = index
    return {
        "items": ranking,
        "verified_evidence_count": len(rows),
        "notice": "只统计人工核验的漫剧/作品趋势证据；提示词分享证据与剧本证据分别进入独立榜单，不混入漫剧热度排名。当前仍需人工录入，尚未接入各平台自动抓取。",
    }


def _verified_evidence_ranking(db, accepted_types):
    import math
    from collections import defaultdict
    from datetime import date
    rows = db.query(TrendEvidence).filter(
        TrendEvidence.is_verified.is_(True),
        TrendEvidence.evidence_type.in_(accepted_types),
    ).all()
    groups = defaultdict(list)
    for row in rows:
        key = " ".join((row.work_title or "").casefold().split())
        if key:
            groups[key].append(row)
    today = date.today()
    weights = {"views":1.0,"plays":1.0,"likes":1.4,"comments":2.2,"shares":2.6,"saves":2.0}
    ranking = []
    for key, samples in groups.items():
        platforms = sorted({item.platform.strip() for item in samples if item.platform.strip()})
        dates = sorted({(item.observed_at or "")[:10] for item in samples if len(item.observed_at or "") >= 10})
        if len(platforms) < 2 or len(dates) < 2:
            continue
        signals = []
        daily_signal = defaultdict(float)
        has_metrics = False
        for item in samples:
            metrics = item.metric_summary if isinstance(item.metric_summary,dict) else {}
            signal = 0.0
            for metric,weight in weights.items():
                try:
                    value=max(0.0,float(metrics.get(metric,0) or 0))
                    signal += weight * math.log1p(value)
                    has_metrics = has_metrics or value > 0
                except (TypeError,ValueError): pass
            try:
                observed_day=date.fromisoformat(item.observed_at[:10])
                age=max(0,(today-observed_day).days)
                decay=math.pow(0.5,age/14.0)
                daily_signal[observed_day.isoformat()] += signal
            except (ValueError,TypeError):
                decay=0.25
            signals.append(signal*decay)
        if not has_metrics:
            continue
        ordered_daily=sorted(daily_signal.items())
        growth_rate=None
        if len(ordered_daily)>=2 and ordered_daily[-2][1]>0:
            growth_rate=round((ordered_daily[-1][1]/ordered_daily[-2][1]-1)*100,2)
        genres=[item.genre or "未分类" for item in samples]
        genre=max(set(genres),key=genres.count) if genres else "未分类"
        if len(set(genres))>1:
            genre="分类待复核"
        score=sum(signals)/max(1,len(signals))+min(len(platforms),5)*2+min(len(dates),7)*1.25
        ranking.append({
            "title":samples[0].work_title,"genre":genre,"growth_rate_pct":growth_rate,"platforms":platforms,"platform_count":len(platforms),
            "observed_days":dates,"sample_count":len(samples),"latest_observed_at":max(dates),
            "score":round(score,3),"evidence_ids":[item.id for item in samples],
            "evidence_types":sorted({item.evidence_type for item in samples}),
            "score_basis":"已核验公开互动数据；对数加权、14天半衰期、跨平台与跨日期加权",
        })
    ranking.sort(key=lambda item:(item["score"],item["latest_observed_at"]),reverse=True)
    for index,item in enumerate(ranking,1): item["rank"]=index
    return ranking, len(rows)


def _prompt_test_ranking(db, score_field="score_overall"):
    rows = db.query(PromptTestRun).all()
    grouped = {}
    for run in rows:
        grouped.setdefault((run.prompt_id,run.prompt_version),[]).append(run)
    prompts = {item.id:item for item in db.query(PromptTemplate).filter(PromptTemplate.is_active.is_(True)).all()}
    result=[]
    for (prompt_id,version),runs in grouped.items():
        prompt=prompts.get(prompt_id)
        if not prompt: continue
        values=[float(getattr(run,score_field) or 0) for run in runs]
        result.append({"title":prompt.name,"prompt_id":prompt_id,"version":version,
                       "test_count":len(runs),"score":round(sum(values)/len(values),2),
                       "last_test_at":max((run.created_at for run in runs if run.created_at),default=None).isoformat() if any(run.created_at for run in runs) else None,
                       "score_basis":"用户录入的提示词版本实测评分，不是作品播放热度"})
    result.sort(key=lambda item:(item["score"],item["test_count"]),reverse=True)
    for index,item in enumerate(result,1): item["rank"]=index
    return result


@router.get("/research/rankings")
def category_rankings(db: Session = Depends(get_db)):
    drama,drama_count = _verified_evidence_ranking(db,{"作品趋势证据","漫剧作品趋势证据","work_trend","公开榜单/公开页面"})
    genre_groups={}
    for item in drama:
        genre_groups.setdefault(item["genre"],[]).append(item)
    drama_genres=[]
    for genre,items in genre_groups.items():
        drama_genres.append({"title":genre,"work_count":len(items),"sample_count":sum(item["sample_count"] for item in items),
                             "score":round(sum(item["score"] for item in items)/len(items),3),
                             "platforms":sorted({platform for item in items for platform in item["platforms"]})})
    drama_genres.sort(key=lambda item:(item["score"],item["work_count"]),reverse=True)
    for index,item in enumerate(drama_genres,1): item["rank"]=index
    screenplay,screenplay_count = _verified_evidence_ranking(db,{"剧本作品证据","screenplay"})
    prompts = _prompt_test_ranking(db)
    public_prompts,prompt_evidence_count = _verified_evidence_ranking(db,{"公开提示词分享","public_prompt"})
    latest_verified = db.query(TrendEvidence).filter(TrendEvidence.is_verified.is_(True)).order_by(TrendEvidence.created_at.desc()).first()
    latest_source = db.query(PublicResearchSource).filter(PublicResearchSource.last_collected_at.isnot(None)).order_by(PublicResearchSource.last_collected_at.desc()).first()
    latest_snapshot = db.query(PromptRankingSnapshot).filter(PromptRankingSnapshot.dimension.in_(["drama","prompt","screenplay"])).order_by(PromptRankingSnapshot.created_at.desc()).first()
    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "freshness": {
            "mode": "recomputed_on_request",
            "ui_poll_seconds": 30,
            "collector_scheduler_seconds": 900,
            "latest_verified_evidence_at": latest_verified.created_at.isoformat() + "Z" if latest_verified and latest_verified.created_at else None,
            "latest_observed_at": latest_verified.observed_at if latest_verified else None,
            "latest_source_collection_at": latest_source.last_collected_at.isoformat() + "Z" if latest_source and latest_source.last_collected_at else None,
            "latest_snapshot_at": latest_snapshot.created_at.isoformat() + "Z" if latest_snapshot and latest_snapshot.created_at else None,
        },
        "categories":{
            "drama":{"title":"漫剧作品排名","items":drama,"genre_items":drama_genres,"verified_evidence_count":drama_count,"basis":"公开作品互动热度，跨平台与跨日期交叉核验；题材榜按已核验作品聚合"},
            "prompt":{"title":"提示词排名","items":prompts,"verified_evidence_count":prompt_evidence_count,"public_share_items":public_prompts,"basis":"正式提示词版本的人工实测评分；公开分享提示词热度单独列出，不混算质量分"},
            "screenplay":{"title":"剧本排名","items":screenplay,"verified_evidence_count":screenplay_count,"basis":"已核验的公开剧本/剧本案例互动数据，跨平台与跨日期交叉核验"},
        },
        "notice":"榜单请求时实时重算；新实测评分或新核验证据写入后立即重算并在数据变化时保存快照。公开来源按各自采集间隔更新，不代表平台提供实时 API。没有样本时显示空榜，不虚构排名或数据。",
    }


def save_category_ranking_snapshots(db: Session, only_if_changed: bool = True):
    data = category_rankings(db)
    created = []
    for key in ("drama","prompt","screenplay"):
        ranking = data["categories"][key]["items"]
        latest = db.query(PromptRankingSnapshot).filter(
            PromptRankingSnapshot.dimension == key
        ).order_by(PromptRankingSnapshot.created_at.desc()).first()
        if only_if_changed and latest and latest.ranking_data == ranking:
            continue
        snapshot = PromptRankingSnapshot(dimension=key, ranking_data=ranking)
        db.add(snapshot)
        db.flush()
        created.append({"category":key,"snapshot_id":snapshot.id,"count":len(ranking)})
    db.commit()
    return {"created_count":len(created),"snapshots":created}


@router.post("/research/rankings/refresh")
def refresh_category_rankings(db: Session = Depends(get_db)):
    result = save_category_ranking_snapshots(db, only_if_changed=False)
    data = category_rankings(db)
    return {"success":True,"snapshots":result["snapshots"],"categories":data["categories"],"message":"三类排名已重新计算并保存历史快照；无证据或无实测数据的类别保持空榜。"}


@router.get("/research/rankings/history")
def category_ranking_history(category: str = "drama", db: Session = Depends(get_db)):
    if category not in {"drama","prompt","screenplay"}:
        raise HTTPException(400,"排名类别无效")
    rows=db.query(PromptRankingSnapshot).filter(PromptRankingSnapshot.dimension==category).order_by(PromptRankingSnapshot.created_at.desc()).limit(50).all()
    return {"items":[{"id":item.id,"category":item.dimension,"created_at":item.created_at.isoformat() if item.created_at else None,"ranking":item.ranking_data or []} for item in rows]}


# User-configured public RSS/Atom sources. No platform login, private API, or hidden-content scraping.
class PublicSourceInput(BaseModel):
    platform: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    feed_url: str = Field(min_length=12, max_length=2000)
    refresh_interval_minutes: int = Field(default=360, ge=60, le=10080)


class PublicSourceUpdate(BaseModel):
    enabled: Optional[bool] = None
    refresh_interval_minutes: Optional[int] = Field(default=None, ge=60, le=10080)


def public_source_dict(row):
    return {
        "id": row.id, "platform": row.platform, "name": row.name,
        "feed_url": row.feed_url, "source_type": row.source_type,
        "enabled": bool(row.enabled), "refresh_interval_minutes": row.refresh_interval_minutes,
        "last_collected_at": row.last_collected_at.isoformat() if row.last_collected_at else None,
        "last_status": row.last_status, "last_error": row.last_error or "",
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def collection_run_dict(row):
    return {
        "id": row.id, "source_id": row.source_id, "status": row.status,
        "discovered_count": row.discovered_count, "inserted_count": row.inserted_count,
        "duplicate_count": row.duplicate_count, "error_message": row.error_message or "",
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
    }


def collect_public_source(source_id: int, db: Session):
    source = db.query(PublicResearchSource).filter(PublicResearchSource.id == source_id).first()
    if not source:
        raise HTTPException(404, "公开来源不存在")
    if not source.enabled:
        raise HTTPException(409, "该来源已暂停")
    run = PublicResearchCollectionRun(source_id=source.id, status="running")
    db.add(run)
    db.flush()
    try:
        items = fetch_public_feed(source.feed_url)
        inserted = 0
        duplicates = 0
        for item in items:
            exists = db.query(TrendEvidence.id).filter(
                TrendEvidence.platform == source.platform,
                TrendEvidence.work_url == item["url"],
            ).first()
            if exists:
                duplicates += 1
                continue
            db.add(TrendEvidence(
                platform=source.platform,
                work_title=item["title"],
                work_url=item["url"],
                observed_at=parse_observed_date(item["published"]),
                metric_summary={
                    "source_name": source.name,
                    "source_type": "rss_atom",
                    "published": item["published"],
                    "summary_excerpt": item["summary_excerpt"],
                },
                evidence_type="公开订阅源发现（作品/创作内容，待人工核验）",
                reliability="自动采集待核验",
                is_verified=False,
            ))
            inserted += 1
        run.status = "success"
        run.discovered_count = len(items)
        run.inserted_count = inserted
        run.duplicate_count = duplicates
        run.finished_at = datetime.utcnow()
        source.last_status = "成功"
        source.last_error = ""
        source.last_collected_at = datetime.utcnow()
        db.commit()
        db.refresh(run)
        snapshot_result = save_category_ranking_snapshots(db, only_if_changed=True)
        return {"success": True, "run": collection_run_dict(run), "ranking_snapshots_updated": snapshot_result["created_count"],
                "message": "采集完成；新增内容均为待人工核验证据，不会自动进入正式提示词库。"}
    except (FeedCollectionError, OSError, ValueError) as exc:
        db.rollback()
        source = db.query(PublicResearchSource).filter(PublicResearchSource.id == source_id).first()
        run = PublicResearchCollectionRun(
            source_id=source_id, status="failed", error_message=str(exc)[:1000],
            finished_at=datetime.utcnow(),
        )
        db.add(run)
        if source:
            source.last_status = "失败"
            source.last_error = str(exc)[:1000]
            source.last_collected_at = datetime.utcnow()
        db.commit()
        return {"success": False, "run": collection_run_dict(run), "message": str(exc)}


@router.get("/research/sources")
def list_public_research_sources(db: Session = Depends(get_db)):
    rows = db.query(PublicResearchSource).order_by(PublicResearchSource.platform, PublicResearchSource.id).all()
    return {"items": [public_source_dict(row) for row in rows],
            "supported_platform_domains": [
                "youtube.com", "bilibili.com", "douyin.com", "kuaishou.com",
                "tiktok.com", "facebook.com", "hongguo.com"
            ],
            "notice": "仅支持用户明确配置的 HTTPS RSS/Atom 公开订阅源；平台域名须在允许范围内。当前不代表这些平台均提供官方 RSS 接口。采集内容全部待人工核验。"
    }


@router.post("/research/sources")
def create_public_research_source(data: PublicSourceInput, db: Session = Depends(get_db)):
    try:
        safe_url = validate_public_feed_url(data.feed_url, data.platform.strip())
    except FeedCollectionError as exc:
        raise HTTPException(400, str(exc))
    duplicate = db.query(PublicResearchSource).filter(PublicResearchSource.feed_url == safe_url).first()
    if duplicate:
        raise HTTPException(409, "该公开订阅源已经添加")
    row = PublicResearchSource(
        platform=data.platform.strip(), name=data.name.strip(), feed_url=safe_url,
        source_type="rss_atom", enabled=True,
        refresh_interval_minutes=data.refresh_interval_minutes,
        last_status="待采集",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"success": True, "item": public_source_dict(row),
            "message": "来源已添加；首次采集将在后台周期任务到期时运行，也可立即手动采集。请确保该地址是平台允许访问的公开 RSS/Atom 源。"
    }


@router.patch("/research/sources/{source_id}")
def update_public_research_source(source_id: int, data: PublicSourceUpdate, db: Session = Depends(get_db)):
    row = db.query(PublicResearchSource).filter(PublicResearchSource.id == source_id).first()
    if not row:
        raise HTTPException(404, "公开来源不存在")
    changes = data.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return {"success": True, "item": public_source_dict(row)}


@router.post("/research/sources/{source_id}/collect")
def collect_public_research_source(source_id: int, db: Session = Depends(get_db)):
    return collect_public_source(source_id, db)


@router.post("/research/sources/collect-due")
def collect_due_public_research_sources(db: Session = Depends(get_db)):
    now = datetime.utcnow()
    rows = db.query(PublicResearchSource).filter(PublicResearchSource.enabled.is_(True)).all()
    due = [
        row.id for row in rows
        if row.last_collected_at is None
        or row.last_collected_at <= now - timedelta(minutes=row.refresh_interval_minutes)
    ]
    results = [collect_public_source(source_id, db) for source_id in due[:10]]
    return {"success": True, "checked": len(rows), "due_count": len(due),
            "processed_count": len(results), "results": results}


@router.get("/research/sources/runs")
def list_public_research_collection_runs(source_id: Optional[int] = None, db: Session = Depends(get_db)):
    query = db.query(PublicResearchCollectionRun)
    if source_id is not None:
        query = query.filter(PublicResearchCollectionRun.source_id == source_id)
    rows = query.order_by(PublicResearchCollectionRun.started_at.desc()).limit(100).all()
    return {"items": [collection_run_dict(row) for row in rows]}


def collect_due_public_sources_background():
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        rows = db.query(PublicResearchSource).filter(PublicResearchSource.enabled.is_(True)).all()
        due_ids = [
            row.id for row in rows
            if row.last_collected_at is None
            or row.last_collected_at <= now - timedelta(minutes=row.refresh_interval_minutes)
        ][:10]
        for source_id in due_ids:
            collect_public_source(source_id, db)
    finally:
        db.close()
