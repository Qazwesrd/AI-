from datetime import datetime
from typing import Optional
import json
import re
import urllib.request
import urllib.error

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy.inspection import inspect as sa_inspect

from app.database import get_db
from app.models.novel import Novel, Chapter
from app.models.story import StoryProject, EpisodePlan, StoryboardShot, StoryAssetLink
from app.models.visual import VisualAsset
from app.models.studio import AIProviderConfig, AIRuntimeSetting, AIUsageLog
from app.routers.studio import decrypt_key

router = APIRouter(prefix="/api/story", tags=["剧本改编与智能分镜"])


def serialize_model(obj):
    result = {}
    for column in sa_inspect(obj.__class__).mapper.column_attrs:
        value = getattr(obj, column.key)
        if isinstance(value, datetime):
            value = value.isoformat()
        result[column.key] = value
    return result


def chapter_value(chapter, candidates, default=""):
    for name in candidates:
        if hasattr(chapter, name):
            value = getattr(chapter, name)
            if value is not None and value != "":
                return value
    return default


def get_chapter_content(chapter):
    return str(chapter_value(
        chapter,
        ["content", "body", "text", "chapter_content", "original_text"],
        ""
    ))


def get_chapter_title(chapter):
    return str(chapter_value(
        chapter,
        ["title", "name", "chapter_title"],
        f"第{chapter.id}章"
    ))


def get_novel_chapters(db, novel_id):
    chapters = db.query(Chapter).all()
    result = []
    for chapter in chapters:
        chapter_novel_id = chapter_value(
            chapter, ["novel_id", "book_id"], None
        )
        if chapter_novel_id is not None and int(chapter_novel_id) == int(novel_id):
            result.append(chapter)

    result.sort(key=lambda x: chapter_value(
        x, ["chapter_number", "chapter_index", "order", "sort_order", "id"], x.id
    ))
    return result


class ProjectInput(BaseModel):
    novel_id: int
    name: str
    adaptation_style: str = "3D动漫电影风格"
    target_duration: int = 120
    min_duration: int = 90
    max_duration: int = 150
    settings: dict = Field(default_factory=dict)


class EpisodeUpdate(BaseModel):
    title: Optional[str] = None
    synopsis: Optional[str] = None
    screenplay: Optional[str] = None
    duration_seconds: Optional[int] = None
    sort_order: Optional[int] = None
    status: Optional[str] = None


class EpisodeReorder(BaseModel):
    episode_ids: list[int]


class EpisodeMerge(BaseModel):
    episode_ids: list[int]
    title: str = ""


class ShotInput(BaseModel):
    shot_number: int = 1
    scene_name: str = ""
    shot_type: str = "中景"
    camera_movement: str = "固定镜头"
    duration_seconds: float = 5
    visual_description: str = ""
    character_actions: str = ""
    dialogue: str = ""
    sound_effects: str = ""
    image_prompt: str = ""
    character_ids: list[int] = Field(default_factory=list)
    scene_asset_id: Optional[int] = None
    prop_ids: list[int] = Field(default_factory=list)
    status: str = "draft"
    sort_order: int = 0


class AssetLinkInput(BaseModel):
    asset_id: int
    role: str = "character"


@router.get("/novels")
def list_novels(db: Session = Depends(get_db)):
    novels = db.query(Novel).all()
    return [serialize_model(x) for x in novels]


@router.get("/novels/{novel_id}/chapters")
def list_novel_chapters(novel_id: int, db: Session = Depends(get_db)):
    novel = db.query(Novel).filter(Novel.id == novel_id).first()
    if not novel:
        raise HTTPException(404, "小说不存在")

    chapters = get_novel_chapters(db, novel_id)
    return [{
        "id": x.id,
        "title": get_chapter_title(x),
        "content": get_chapter_content(x),
        "word_count": len(get_chapter_content(x).strip()),
    } for x in chapters]


@router.post("/projects")
def create_project(payload: ProjectInput, db: Session = Depends(get_db)):
    novel = db.query(Novel).filter(Novel.id == payload.novel_id).first()
    if not novel:
        raise HTTPException(404, "小说不存在")
    if payload.min_duration > payload.max_duration:
        raise HTTPException(400, "单集最短时长不能大于最长时长")

    project = StoryProject(**payload.model_dump())
    db.add(project)
    db.commit()
    db.refresh(project)
    return {"success": True, "project": serialize_model(project)}


@router.get("/projects")
def list_projects(db: Session = Depends(get_db)):
    items = db.query(StoryProject).order_by(StoryProject.created_at.desc()).all()
    return [serialize_model(x) for x in items]


@router.get("/projects/{project_id}")
def get_project(project_id: int, db: Session = Depends(get_db)):
    project = db.query(StoryProject).filter(StoryProject.id == project_id).first()
    if not project:
        raise HTTPException(404, "剧本项目不存在")
    episodes = db.query(EpisodePlan).filter(
        EpisodePlan.project_id == project_id
    ).order_by(EpisodePlan.sort_order, EpisodePlan.episode_number).all()
    return {
        "project": serialize_model(project),
        "episodes": [serialize_model(x) for x in episodes],
    }


@router.post("/projects/{project_id}/plan")
def plan_episodes(project_id: int, db: Session = Depends(get_db)):
    project = db.query(StoryProject).filter(StoryProject.id == project_id).first()
    if not project:
        raise HTTPException(404, "剧本项目不存在")

    chapters = get_novel_chapters(db, project.novel_id)
    if not chapters:
        raise HTTPException(400, "该小说没有找到章节内容，无法规划分集")

    # 保留原章节顺序；超长章节允许拆成两集，不自动把短章节拆碎。
    old = db.query(EpisodePlan).filter(EpisodePlan.project_id == project_id).all()
    # Replanning invalidates dependent shots and asset links; remove them explicitly
    # rather than leaving records attached to deleted episode IDs.
    for item in old:
        db.query(StoryboardShot).filter(StoryboardShot.episode_id == item.id).delete(synchronize_session=False)
        db.query(StoryAssetLink).filter(StoryAssetLink.episode_id == item.id).delete(synchronize_session=False)
        db.delete(item)
    db.flush()

    episode_number = 1
    for chapter in chapters:
        content = get_chapter_content(chapter)
        title = get_chapter_title(chapter)
        if not content.strip():
            continue

        # 用章节内容长度估算分集；此处是初步规划规则，不冒充 AI 剧情分析。
        split = len(content) > 9000
        parts = [content[:len(content)//2], content[len(content)//2:]] if split else [content]

        for part_index, part in enumerate(parts):
            suffix = f"（下）" if split and part_index == 1 else (f"（上）" if split else "")
            episode = EpisodePlan(
                project_id=project_id,
                episode_number=episode_number,
                title=f"{title}{suffix}",
                source_chapter_ids=[chapter.id],
                source_text=part,
                synopsis="待 AI 根据原著内容提炼剧情摘要",
                screenplay="待 AI 改编为影视剧本",
                duration_seconds=project.target_duration,
                status="planned",
                sort_order=episode_number,
            )
            db.add(episode)
            episode_number += 1

    project.status = "planned"
    project.updated_at = datetime.utcnow()
    db.commit()

    episodes = db.query(EpisodePlan).filter(
        EpisodePlan.project_id == project_id
    ).order_by(EpisodePlan.sort_order).all()

    return {
        "success": True,
        "message": "初步分集计划已建立。剧情摘要和剧本正文仍需 AI 改编。",
        "episode_count": len(episodes),
        "episodes": [serialize_model(x) for x in episodes],
    }


class AIPlanInput(BaseModel):
    confirm_paid: bool = False


@router.post("/projects/{project_id}/plan-ai")
def plan_episodes_with_ai(project_id: int, payload: AIPlanInput, db: Session = Depends(get_db)):
    project = db.query(StoryProject).filter(StoryProject.id == project_id).first()
    if not project:
        raise HTTPException(404, "剧本项目不存在")
    if db.query(EpisodePlan).filter(EpisodePlan.project_id == project_id).count():
        raise HTTPException(409, "该项目已有分集。为避免覆盖剧本和分镜，请先创建新项目再执行 AI 规划。")
    runtime = db.query(AIRuntimeSetting).first()
    if not runtime or not runtime.network_enabled:
        raise HTTPException(403, "网络调用总开关尚未开启")
    provider = db.query(AIProviderConfig).filter(
        AIProviderConfig.enabled == True,
        AIProviderConfig.purpose == "text",
        AIProviderConfig.base_url != "",
        AIProviderConfig.model != "",
        AIProviderConfig.encrypted_api_key != "",
    ).first()
    if not provider:
        raise HTTPException(400, "没有已启用的文本 AI 模型，请先完成 AI 管理中心配置")
    paid = bool(getattr(provider, "paid", True))
    if paid and not runtime.paid_api_allowed:
        raise HTTPException(403, "当前模型属于付费服务，请先开启付费 API 总授权")
    if paid and not payload.confirm_paid:
        raise HTTPException(403, "本次 AI 分集规划可能产生费用，尚未确认")
    chapters = get_novel_chapters(db, project.novel_id)
    if not chapters:
        raise HTTPException(400, "小说没有可用章节")
    try:
        api_key = decrypt_key(provider.encrypted_api_key)
        episode_number = 1
        for chapter in chapters:
            source = get_chapter_content(chapter)
            if not source.strip():
                continue
            title = get_chapter_title(chapter)
            prompt = (
                "你是资深影视编剧和漫剧分集导演。请阅读整章原文，决定本章应改编为1集还是2集。"
                "必须根据剧情完整性、冲突转折、场景变化、人物弧线和约2分钟单集节奏判断，禁止按字数机械对半切。"
                "如果一集能完整表达本章核心剧情，返回1集；只有剧情确实过长且存在自然戏剧断点时才返回2集。"
                "若拆为2集，split_quote 必须是原文中连续、逐字一致的一段短句，作为第二集开始位置；不要改写标点。"
                "只返回 JSON：{\"episode_count\":1或2,\"split_quote\":\"原文精确句子或空字符串\",\"synopsis\":\"本章剧情摘要\",\"episode1_title\":\"标题\",\"episode1_synopsis\":\"上集摘要\",\"episode2_title\":\"下集标题\",\"episode2_synopsis\":\"下集摘要\"}。"
                "小说章节标题：" + title + "\n小说原文：\n" + source[:24000]
            )
            body = json.dumps({"model": provider.model, "temperature": 0.2, "messages": [
                {"role": "system", "content": "严格输出 JSON，不要 Markdown，不要编造原文没有的剧情。"},
                {"role": "user", "content": prompt},
            ]}, ensure_ascii=False).encode("utf-8")
            request = urllib.request.Request(
                provider.base_url.rstrip("/") + "/chat/completions",
                data=body,
                headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=240) as response:
                result = json.loads(response.read().decode("utf-8"))
            raw = result["choices"][0]["message"]["content"]
            if isinstance(raw, list):
                raw = "".join(part.get("text", "") for part in raw if isinstance(part, dict))
            raw = re.sub(r"^```(?:json)?\\s*", "", str(raw).strip())
            raw = re.sub(r"\\s*```$", "", raw)
            try:
                decision = json.loads(raw)
            except json.JSONDecodeError:
                left, right = raw.find("{"), raw.rfind("}")
                if left < 0 or right <= left:
                    raise ValueError("AI 分集规划返回的 JSON 无效")
                decision = json.loads(raw[left:right + 1])
            count = 2 if int(decision.get("episode_count", 1)) == 2 else 1
            split_at = -1
            quote = str(decision.get("split_quote", "")).strip()
            if count == 2 and quote:
                split_at = source.find(quote)
            if count == 2 and (split_at <= 0 or split_at >= len(source) - len(quote)):
                count = 1
            if count == 1:
                parts = [source]
            else:
                parts = [source[:split_at], source[split_at:]]
            for index, part in enumerate(parts):
                suffix = "（上）" if count == 2 and index == 0 else ("（下）" if count == 2 else "")
                ep_title = decision.get("episode1_title" if index == 0 else "episode2_title") or title + suffix
                ep_synopsis = decision.get("episode1_synopsis" if index == 0 else "episode2_synopsis") or decision.get("synopsis", "")
                db.add(EpisodePlan(
                    project_id=project.id, episode_number=episode_number, title=str(ep_title)[:300],
                    source_chapter_ids=[chapter.id], source_text=part, synopsis=str(ep_synopsis)[:12000],
                    screenplay="", duration_seconds=project.target_duration, status="ai_planned", sort_order=episode_number,
                ))
                episode_number += 1
        if episode_number == 1:
            raise ValueError("小说章节均为空，无法规划分集")
        project.status = "ai_planned"
        db.add(AIUsageLog(provider=provider.provider, model=provider.model, task="ai_episode_planning", status="success", detail="AI按剧情结构完成章节分集规划"))
        db.commit()
    except urllib.error.HTTPError as exc:
        db.rollback()
        detail = exc.read().decode("utf-8", errors="replace")[:1500]
        raise HTTPException(502, "AI 分集规划接口错误：" + str(exc.code) + " " + detail) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(502, "AI 分集规划失败：" + str(exc)) from exc
    episodes = db.query(EpisodePlan).filter(EpisodePlan.project_id == project_id).order_by(EpisodePlan.sort_order).all()
    return {"success": True, "message": "AI 已根据剧情结构完成分集规划；每章可规划为1集或在自然剧情断点拆为2集。", "episode_count": len(episodes), "episodes": [serialize_model(x) for x in episodes]}

@router.put("/episodes/{episode_id}")
def update_episode(episode_id: int, payload: EpisodeUpdate, db: Session = Depends(get_db)):
    episode = db.query(EpisodePlan).filter(EpisodePlan.id == episode_id).first()
    if not episode:
        raise HTTPException(404, "分集不存在")
    for key, value in payload.model_dump(exclude_unset=True).items():
        if key == "duration_seconds" and (value < 1 or value > 3600):
            raise HTTPException(400, "单集时长必须在 1 到 3600 秒之间")
        setattr(episode, key, value)
    episode.updated_at = datetime.utcnow()
    db.commit()
    return {"success": True, "episode": serialize_model(episode)}


@router.delete("/episodes/{episode_id}")
def delete_episode(episode_id: int, db: Session = Depends(get_db)):
    episode = db.query(EpisodePlan).filter(EpisodePlan.id == episode_id).first()
    if not episode:
        raise HTTPException(404, "分集不存在")
    db.query(StoryboardShot).filter(StoryboardShot.episode_id == episode_id).delete()
    db.query(StoryAssetLink).filter(StoryAssetLink.episode_id == episode_id).delete()
    db.delete(episode)
    db.commit()
    return {"success": True}


@router.post("/projects/{project_id}/reorder")
def reorder_episodes(project_id: int, payload: EpisodeReorder, db: Session = Depends(get_db)):
    episodes = db.query(EpisodePlan).filter(
        EpisodePlan.project_id == project_id
    ).all()
    lookup = {x.id: x for x in episodes}
    if (
        len(payload.episode_ids) != len(set(payload.episode_ids))
        or set(payload.episode_ids) != set(lookup.keys())
    ):
        raise HTTPException(400, "排序列表必须包含该项目全部分集，且不能重复")
    for index, episode_id in enumerate(payload.episode_ids, start=1):
        lookup[episode_id].sort_order = index
        lookup[episode_id].episode_number = index
    db.commit()
    return {"success": True}


@router.post("/projects/{project_id}/merge")
def merge_episodes(project_id: int, payload: EpisodeMerge, db: Session = Depends(get_db)):
    if len(payload.episode_ids) < 2:
        raise HTTPException(400, "至少选择两集才能合并")
    episodes = db.query(EpisodePlan).filter(
        EpisodePlan.project_id == project_id,
        EpisodePlan.id.in_(payload.episode_ids)
    ).order_by(EpisodePlan.sort_order).all()
    if len(episodes) != len(set(payload.episode_ids)):
        raise HTTPException(400, "分集不存在或不属于该项目")

    first = episodes[0]
    first.title = payload.title.strip() or " + ".join(x.title for x in episodes)
    first.source_text = "\n\n".join(x.source_text or "" for x in episodes)
    first.source_chapter_ids = list(dict.fromkeys(
        chapter_id for x in episodes for chapter_id in (x.source_chapter_ids or [])
    ))
    first.duration_seconds = min(
        3600, sum(x.duration_seconds for x in episodes)
    )
    for item in episodes[1:]:
        db.query(StoryboardShot).filter(StoryboardShot.episode_id == item.id).update(
            {StoryboardShot.episode_id: first.id}
        )
        db.query(StoryAssetLink).filter(StoryAssetLink.episode_id == item.id).update(
            {StoryAssetLink.episode_id: first.id}
        )
        db.delete(item)
    db.flush()
    remaining = db.query(EpisodePlan).filter(
        EpisodePlan.project_id == project_id
    ).order_by(EpisodePlan.sort_order, EpisodePlan.episode_number, EpisodePlan.id).all()
    for index, item in enumerate(remaining, start=1):
        item.sort_order = index
        item.episode_number = index
    db.commit()
    return {"success": True, "merged_episode_id": first.id}


@router.post("/episodes/{episode_id}/split")
def split_episode(episode_id: int, db: Session = Depends(get_db)):
    episode = db.query(EpisodePlan).filter(EpisodePlan.id == episode_id).first()
    if not episode:
        raise HTTPException(404, "分集不存在")
    text = episode.source_text or ""
    if len(text.strip()) < 2:
        raise HTTPException(400, "原始内容太短，无法拆分")
    midpoint = len(text) // 2
    split_at = text.find("\n", max(0, midpoint - 500), min(len(text), midpoint + 500))
    if split_at < 0:
        split_at = midpoint
    else:
        split_at += 1

    second = EpisodePlan(
        project_id=episode.project_id,
        episode_number=episode.episode_number + 1,
        title=episode.title + "（下）",
        source_chapter_ids=episode.source_chapter_ids or [],
        source_text=text[split_at:],
        synopsis="待 AI 提炼剧情摘要",
        screenplay="待 AI 改编剧本",
        duration_seconds=episode.duration_seconds,
        status="planned",
        sort_order=episode.sort_order + 1,
    )
    episode.title = episode.title + "（上）"
    episode.source_text = text[:split_at]
    db.add(second)
    db.flush()

    later = db.query(EpisodePlan).filter(
        EpisodePlan.project_id == episode.project_id,
        EpisodePlan.id != episode.id,
        EpisodePlan.id != second.id,
        EpisodePlan.sort_order >= second.sort_order
    ).all()
    for item in later:
        item.sort_order += 1
        item.episode_number += 1

    db.commit()
    return {"success": True, "new_episode": serialize_model(second)}


@router.get("/episodes/{episode_id}/shots")
def list_shots(episode_id: int, db: Session = Depends(get_db)):
    shots = db.query(StoryboardShot).filter(
        StoryboardShot.episode_id == episode_id
    ).order_by(StoryboardShot.sort_order, StoryboardShot.shot_number).all()
    return [serialize_model(x) for x in shots]


@router.post("/episodes/{episode_id}/shots")
def create_shot(episode_id: int, payload: ShotInput, db: Session = Depends(get_db)):
    episode = db.query(EpisodePlan).filter(EpisodePlan.id == episode_id).first()
    if not episode:
        raise HTTPException(404, "分集不存在")
    shot = StoryboardShot(
        project_id=episode.project_id,
        episode_id=episode_id,
        **payload.model_dump()
    )
    db.add(shot)
    db.commit()
    db.refresh(shot)
    return {"success": True, "shot": serialize_model(shot)}


@router.put("/shots/{shot_id}")
def update_shot(shot_id: int, payload: ShotInput, db: Session = Depends(get_db)):
    shot = db.query(StoryboardShot).filter(StoryboardShot.id == shot_id).first()
    if not shot:
        raise HTTPException(404, "分镜镜头不存在")
    for key, value in payload.model_dump().items():
        setattr(shot, key, value)
    shot.updated_at = datetime.utcnow()
    db.commit()
    return {"success": True}


@router.delete("/shots/{shot_id}")
def delete_shot(shot_id: int, db: Session = Depends(get_db)):
    shot = db.query(StoryboardShot).filter(StoryboardShot.id == shot_id).first()
    if not shot:
        raise HTTPException(404, "分镜镜头不存在")
    db.query(StoryAssetLink).filter(StoryAssetLink.shot_id == shot_id).delete()
    db.delete(shot)
    db.commit()
    return {"success": True}


@router.get("/assets")
def list_visual_assets(db: Session = Depends(get_db)):
    items = db.query(VisualAsset).all()
    return [{
        "id": x.id,
        "category": x.category,
        "name": x.name,
        "description": x.description,
        "appearance": x.appearance,
        "image_path": x.image_path,
        "confirmed": x.confirmed,
    } for x in items]


@router.post("/shots/{shot_id}/assets")
def link_asset(shot_id: int, payload: AssetLinkInput, db: Session = Depends(get_db)):
    shot = db.query(StoryboardShot).filter(StoryboardShot.id == shot_id).first()
    asset = db.query(VisualAsset).filter(VisualAsset.id == payload.asset_id).first()
    if not shot or not asset:
        raise HTTPException(404, "分镜或视觉素材不存在")
    existing = db.query(StoryAssetLink).filter(
        StoryAssetLink.shot_id == shot_id,
        StoryAssetLink.asset_id == payload.asset_id,
        StoryAssetLink.role == payload.role,
    ).first()
    if existing:
        return {"success": True, "link_id": existing.id, "existing": True}
    link = StoryAssetLink(
        project_id=shot.project_id,
        episode_id=shot.episode_id,
        shot_id=shot.id,
        asset_id=asset.id,
        role=payload.role,
    )
    db.add(link)
    db.commit()
    return {"success": True, "link_id": link.id}


@router.get("/shots/{shot_id}/assets")
def get_shot_assets(shot_id: int, db: Session = Depends(get_db)):
    links = db.query(StoryAssetLink).filter(StoryAssetLink.shot_id == shot_id).all()
    result = []
    for link in links:
        asset = db.query(VisualAsset).filter(VisualAsset.id == link.asset_id).first()
        if asset:
            result.append({"role": link.role, "asset": serialize_model(asset)})
    return result
