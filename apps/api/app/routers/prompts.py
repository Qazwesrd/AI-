from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.prompts import (
    PromptCategory, PromptTemplate, PromptVersion, PromptTestRun,
    PromptRankingSnapshot,
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
    return {"success": True, "test_id": run.id, "message": "实测评分已记录；该评分为人工录入，不代表系统已自动生成或验证作品。"}


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
