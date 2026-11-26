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




def _story_local_model():
    # Lazy import avoids router-import cycles; this is the same fixed localhost
    # Ollama client used by the local runtime diagnostics.
    from app.routers.local_ai_runtime import _installed_models

    installed = _installed_models()
    names = [item["name"] for item in installed if isinstance(item, dict) and item.get("name")]
    for preferred in ("qwen3.5:4b", "qwen3:4b"):
        if preferred in names:
            return preferred
    raise HTTPException(503, "没有已安装的 Qwen 本地文本模型；离线模式不会联网下载。")


def _story_local_generate(model, system_prompt, prompt, temperature=0.2):
    from app.routers.local_ai_runtime import _ollama_request

    try:
        result = _ollama_request(
            "/api/generate",
            {
                "model": model,
                "system": system_prompt,
                "prompt": prompt,
                "stream": False,
                "format": "json",
                "think": False,
                "options": {
                    "temperature": temperature,
                    "num_ctx": 8192,
                    "num_predict": 4096,
                },
            },
            timeout=900.0,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, "本地模型生成失败：" + str(exc)[:800]) from exc
    raw = result.get("response") if isinstance(result, dict) else None
    if not isinstance(raw, str) or not raw.strip():
        raise HTTPException(502, "本地模型未返回有效 JSON 内容")
    return raw.strip()


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


def ensure_project_source_current(project):
    if project.status == "source_changed":
        raise HTTPException(
            status_code=409,
            detail=(
                "检测到小说原文或章节内容已发生变化，当前剧本项目属于旧版本。为避免使用旧分集、剧本和分镜继续生成，"
                "本项目的 AI 分集规划、剧本和后续制作已暂停。请保留此旧项目作为历史版本，"
                "并基于当前小说新建改编项目后重新规划；旧项目和已有成果不会被自动删除。"
            ),
        )


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
    chapter_number: Optional[int] = Field(default=None, ge=1)


def snapshot_asset_state(asset: VisualAsset, chapter_number: Optional[int]):
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
    field_map = {
        "appearance_after": "appearance", "clothing_after": "clothing",
        "age_after": "age", "hairstyle_after": "hairstyle",
        "injury_after": "injury", "equipment_after": "equipment",
        "condition_after": "condition", "holder": "holder",
    }
    for item in applicable:
        for source_key, target_key in field_map.items():
            value = item.get(source_key)
            if isinstance(value, str) and value.strip():
                state[target_key] = value.strip()
        state.update({
            "chapter_number": item.get("chapter_number"),
            "event": item.get("event", ""),
            "change_type": item.get("change_type", "状态变化"),
            "confirmed": bool(item.get("confirmed", True)),
            "is_evolved": True,
        })
    return state


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
    result = serialize_model(project)
    settings = result.get("settings") if isinstance(result.get("settings"), dict) else {}
    result["source_change_notice"] = settings.get("source_change_notice")
    result["generation_blocked"] = project.status == "source_changed"
    return {
        "project": result,
        "episodes": [serialize_model(x) for x in episodes],
    }


@router.post("/projects/{project_id}/plan")
def plan_episodes(project_id: int, db: Session = Depends(get_db)):
    project = db.query(StoryProject).filter(StoryProject.id == project_id).first()
    if not project:
        raise HTTPException(404, "剧本项目不存在")
    ensure_project_source_current(project)

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
    prompt_style: str = "professional"
    additional_instructions: str = Field(default="", max_length=4000)


PROMPT_STYLE_GUIDANCE = {
    "professional": "采用专业影视工业化表达，强调结构完整、人物动机、场面调度、镜头可执行性和制作细节。",
    "cinematic": "采用电影级叙事与视觉表达，强化戏剧张力、情绪递进、构图层次、光影氛围、镜头节奏和视听语言。",
    "concise": "采用简洁明确的制作指令，避免空泛形容词，优先输出可执行的信息，不遗漏关键剧情和必要字段。",
}


def prompt_style_guidance(style: str) -> str:
    return PROMPT_STYLE_GUIDANCE.get(style, PROMPT_STYLE_GUIDANCE["professional"])


@router.post("/projects/{project_id}/plan-ai")
def plan_episodes_with_ai(project_id: int, payload: AIPlanInput, db: Session = Depends(get_db)):
    project = db.query(StoryProject).filter(StoryProject.id == project_id).first()
    if not project:
        raise HTTPException(404, "剧本项目不存在")
    ensure_project_source_current(project)

    local_model = _story_local_model()

    chapters = get_novel_chapters(db, project.novel_id)
    if not chapters:
        raise HTTPException(400, "小说没有可用章节")

    settings = dict(project.settings or {})
    progress = dict(settings.get("ai_episode_planning") or {})
    if not progress and db.query(EpisodePlan).filter(
        EpisodePlan.project_id == project_id
    ).count():
        raise HTTPException(
            409,
            "该项目已有分集草案。为避免把新规划追加到旧分集，请新建改编项目后再执行 AI 规划。",
        )
    cursor = max(0, min(int(progress.get("chapter_cursor", 0)), len(chapters)))
    if progress.get("status") == "completed":
        episodes = db.query(EpisodePlan).filter(
            EpisodePlan.project_id == project_id
        ).order_by(EpisodePlan.sort_order).all()
        return {
            "success": True, "done": True, "processed_chapters": cursor,
            "total_chapters": len(chapters), "episode_count": len(episodes),
            "episodes": [serialize_model(x) for x in episodes],
            "message": "AI 分集规划此前已完成，无需重复调用模型。",
        }

    # 每次只处理一章；每章完成后立即提交，关闭软件后可从断点继续。
    batch_end = min(cursor + 1, len(chapters))
    try:
        batch_chapters = []
        for chapter_index in range(cursor, batch_end):
            chapter = chapters[chapter_index]
            source = get_chapter_content(chapter)
            if source.strip():
                batch_chapters.append({
                    "chapter_index": chapter_index,
                    "title": get_chapter_title(chapter),
                    "content": source[:7000],
                })

        decisions_by_index = {}
        if batch_chapters:
            prompt = (
                "你是资深影视编剧和漫剧分集导演。下面提供一章小说，请判断本章应改编为1集还是2集。"
                "必须依据剧情完整性、冲突转折、场景变化、人物弧线和约2分钟单集节奏判断，禁止按字数机械对半切。"
                "若一集能完整表达本章核心剧情，返回1集；只有剧情确实过长且存在自然戏剧断点时才返回2集。"
                "若拆为2集，split_quote必须是原文中连续、逐字一致的一段短句，作为第二集开始位置。"
                "必须返回本章一条结果，chapter_index必须与输入完全一致。"
                "提示词方案：" + prompt_style_guidance(payload.prompt_style) + "。"
                + ("用户补充要求：" + payload.additional_instructions.strip() + "。" if payload.additional_instructions.strip() else "")
                + '只返回JSON：{"chapters":[{"chapter_index":0,"episode_count":1,"split_quote":"",'
                '"synopsis":"本章剧情摘要","episode1_title":"标题",'
                '"episode1_synopsis":"上集摘要","episode2_title":"下集标题",'
                '"episode2_synopsis":"下集摘要"}]}'
                "\\n章节原文数据：\\n" + json.dumps(batch_chapters, ensure_ascii=False)
            )
            raw = _story_local_generate(
                local_model,
                "严格输出JSON，不要Markdown；每个输入章节都必须有对应结果，不得混淆章节编号，不得编造剧情。",
                prompt, temperature=0.2,
            )
            if isinstance(raw, list):
                raw = "".join(part.get("text", "") for part in raw if isinstance(part, dict))
            raw = str(raw).strip()
            if raw.startswith("```json"):
                raw = raw[7:].strip()
            elif raw.startswith("```"):
                raw = raw[3:].strip()
            if raw.endswith("```"):
                raw = raw[:-3].strip()
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                left, right = raw.find("{"), raw.rfind("}")
                if left < 0 or right <= left:
                    raise ValueError("AI 分集规划返回的 JSON 无效")
                parsed = json.loads(raw[left:right + 1])
            decisions = parsed.get("chapters") if isinstance(parsed, dict) else None
            if not isinstance(decisions, list):
                raise ValueError("AI分集规划没有返回chapters数组")
            for item in decisions:
                if isinstance(item, dict):
                    try:
                        decisions_by_index[int(item.get("chapter_index"))] = item
                    except (TypeError, ValueError):
                        continue

        for chapter_index in range(cursor, batch_end):
            chapter = chapters[chapter_index]
            source = get_chapter_content(chapter)
            if source.strip():
                decision = decisions_by_index.get(chapter_index)
                if not decision:
                    raise ValueError(f"AI未返回第{chapter_index + 1}章的规划结果")
                title = get_chapter_title(chapter)
                count = 2 if int(decision.get("episode_count", 1)) == 2 else 1
                quote = str(decision.get("split_quote", "")).strip()
                split_at = source.find(quote) if count == 2 and quote else -1
                if count == 2 and (split_at <= 0 or split_at >= len(source) - len(quote)):
                    count = 1
                parts = [source] if count == 1 else [source[:split_at], source[split_at:]]
                episode_number = db.query(EpisodePlan).filter(
                    EpisodePlan.project_id == project_id
                ).count() + 1
                for part_index, part in enumerate(parts):
                    suffix = "（上）" if count == 2 and part_index == 0 else ("（下）" if count == 2 else "")
                    ep_title = decision.get("episode1_title" if part_index == 0 else "episode2_title") or title + suffix
                    ep_synopsis = decision.get("episode1_synopsis" if part_index == 0 else "episode2_synopsis") or decision.get("synopsis", "")
                    db.add(EpisodePlan(
                        project_id=project.id, episode_number=episode_number,
                        title=str(ep_title)[:300], source_chapter_ids=[chapter.id],
                        source_text=part, synopsis=str(ep_synopsis)[:12000],
                        screenplay="", duration_seconds=project.target_duration,
                        status="ai_planned", sort_order=episode_number,
                    ))
                    episode_number += 1

            progress.update({
                "status": "running",
                "chapter_cursor": chapter_index + 1,
                "total_chapters": len(chapters),
                "provider": "ollama-local",
                "model": local_model,
                "updated_at": datetime.utcnow().isoformat(),
            })
            settings["ai_episode_planning"] = progress
            project.settings = settings
            project.status = "planning"
            db.add(AIUsageLog(
                provider="ollama-local", model=local_model,
                task="ai_episode_planning", status="success",
                detail=f"项目#{project_id}；章节{chapter_index + 1}/{len(chapters)}",
            ))
            db.commit()

        done = batch_end >= len(chapters)
        if done:
            progress["status"] = "completed"
            project.status = "ai_planned"
            settings["ai_episode_planning"] = progress
            project.settings = settings
            db.commit()

    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(502, "AI 分集规划失败：" + str(exc) + "；已完成章节已保存，再次点击可从断点继续。") from exc

    episodes = db.query(EpisodePlan).filter(
        EpisodePlan.project_id == project_id
    ).order_by(EpisodePlan.sort_order).all()
    return {
        "success": True,
        "done": done,
        "processed_chapters": min(batch_end, len(chapters)),
        "total_chapters": len(chapters),
        "episode_count": len(episodes),
        "episodes": [serialize_model(x) for x in episodes],
        "message": (
            f"AI 分集规划进度：{min(batch_end, len(chapters))}/{len(chapters)} 章，"
            f"已生成 {len(episodes)} 集。" + ("全部完成。" if done else "已保存本批结果，可继续执行下一批。")
        ),
    }

@router.post("/episodes/{episode_id}/generate-ai")
def generate_episode_script_and_shots(episode_id: int, payload: AIPlanInput, db: Session = Depends(get_db)):
    episode = db.query(EpisodePlan).filter(EpisodePlan.id == episode_id).first()
    if not episode:
        raise HTTPException(404, "分集不存在")
    project = db.query(StoryProject).filter(
        StoryProject.id == episode.project_id
    ).first()
    if not project:
        raise HTTPException(404, "分集所属的剧本项目不存在")
    ensure_project_source_current(project)
    if not (episode.source_text or "").strip():
        raise HTTPException(400, "本集原著内容为空")
    if db.query(StoryboardShot).filter(StoryboardShot.episode_id == episode_id).count():
        raise HTTPException(409, "本集已有分镜。请先备份现有成果，再决定是否清除后重新生成。")
    local_model = _story_local_model()
    project = db.query(StoryProject).filter(StoryProject.id == episode.project_id).first()
    if project:
        ensure_project_source_current(project)
    style = project.adaptation_style if project else "3D动漫电影风格"
    target = project.target_duration if project else 120
    novel_id = project.novel_id if project else None
    asset_query = db.query(VisualAsset).filter(VisualAsset.confirmed == True)
    confirmed_assets = asset_query.order_by(VisualAsset.id.asc()).limit(300).all()
    catalog_rows = []
    for asset in confirmed_assets:
        asset_data = asset.data if isinstance(asset.data, dict) else {}
        if novel_id is not None and asset_data.get("source_novel_id") not in (None, novel_id):
            continue
        catalog_rows.append({
            "category": asset.category, "name": asset.name,
            "description": (asset.description or "")[:300],
            "appearance": (asset.appearance or "")[:300],
            "consistency": (asset.consistency_prompt or "")[:200],
        })
    asset_catalog_text = json.dumps(catalog_rows[:160], ensure_ascii=False)
    source = (episode.source_text or "")[:7000]
    prompt = (
        "你是专业影视编剧、漫剧导演和分镜师。把原著改编成可直接制作的短篇漫剧。"
        "提示词方案：" + prompt_style_guidance(payload.prompt_style) + "。"
        + ("用户补充要求：" + payload.additional_instructions.strip() + "。" if payload.additional_instructions.strip() else "")
        + "忠实保留原著核心含义、人物身份关系、剧情因果、关键事件和人物立场；允许压缩重复描写，不得擅自改结局或编造关键事实。"
        "剧本必须包含场景、动作、表情、走位、对白、旁白和转场；分镜必须逐段覆盖剧本，不得遗漏关键剧情。"
        "每个镜头必须写明景别、运镜、画面构图、角色动作、场景、对白/旁白、音效和可直接用于图像模型的提示词。"
        "画面风格：" + style + "。目标时长约" + str(target) + "秒，成片允许90至150秒。生成10至24个镜头，镜头时长合计尽量接近目标时长。"
        "优先复用下列已确认视觉素材的准确名称，人物/场景/武器/道具名称必须与素材库一致；没有匹配项时才使用原文名称，不得把不同素材混为一体。"
        "已确认素材目录（JSON）：" + asset_catalog_text + "。"
        "输出简体中文 JSON，不要 Markdown，格式：{\"title\":\"标题\",\"synopsis\":\"剧情摘要\",\"screenplay\":\"分场影视剧本\",\"shots\":[{\"scene_name\":\"场景名称，尽量与已识别场景素材名称一致\",\"character_names\":[\"本镜头出场人物素材名称\"],\"prop_names\":[\"本镜头出现的武器/道具/特殊物品素材名称\"],\"shot_type\":\"景别\",\"camera_movement\":\"运镜\",\"duration_seconds\":5,\"visual_description\":\"画面\",\"character_actions\":\"动作表情走位\",\"dialogue\":\"对白旁白\",\"sound_effects\":\"音效\",\"image_prompt\":\"图像模型提示词\"}]}。"
        "小说章节原文：\n" + source
    )
    try:
        raw = _story_local_generate(
            local_model,
            "你只输出符合结构的 JSON，不输出 Markdown。",
            prompt, temperature=0.35,
        )
        if isinstance(raw, list):
            raw = "".join(part.get("text", "") for part in raw if isinstance(part, dict))
        raw = re.sub(r"^```(?:json)?\s*", "", str(raw).strip())
        raw = re.sub(r"\s*```$", "", raw)
        try:
            generated = json.loads(raw)
        except json.JSONDecodeError:
            left, right = raw.find("{"), raw.rfind("}")
            if left < 0 or right <= left:
                raise ValueError("AI返回的剧本/分镜JSON无效")
            generated = json.loads(raw[left:right + 1])
        shots = generated.get("shots")
        if not isinstance(shots, list) or not shots:
            raise ValueError("AI未生成有效分镜镜头")
        screenplay = str(generated.get("screenplay", "")).strip()
        if not screenplay:
            raise ValueError("AI未生成影视剧本正文")
        episode.title = str(generated.get("title") or episode.title)[:300]
        episode.synopsis = str(generated.get("synopsis") or episode.synopsis)[:12000]
        episode.screenplay = screenplay[:100000]
        episode.status = "scripted"
        created = 0
        novel_id = project.novel_id if project else None
        visual_assets = db.query(VisualAsset).filter(VisualAsset.confirmed == True).all()
        asset_lookup = {}
        for asset in visual_assets:
            asset_data = asset.data if isinstance(asset.data, dict) else {}
            if novel_id is not None and asset_data.get("source_novel_id") not in (None, novel_id):
                continue
            asset_lookup.setdefault((asset.category, asset.name.strip().casefold()), asset)
        for index, shot in enumerate(shots[:24], 1):
            if not isinstance(shot, dict):
                continue
            try:
                duration = float(shot.get("duration_seconds", 5) or 5)
            except (TypeError, ValueError):
                duration = 5.0
            scene_name = str(shot.get("scene_name", "")).strip()
            character_names = shot.get("character_names") if isinstance(shot.get("character_names"), list) else []
            prop_names = shot.get("prop_names") if isinstance(shot.get("prop_names"), list) else []
            character_assets = [
                asset_lookup.get(("characters", str(name).strip().casefold()))
                for name in character_names if str(name).strip()
            ]
            prop_assets = []
            for name in prop_names:
                normalized = str(name).strip().casefold()
                for category in ("weapons", "props", "special_items"):
                    found = asset_lookup.get((category, normalized))
                    if found:
                        prop_assets.append(found)
                        break
            scene_asset = asset_lookup.get(("scenes", scene_name.casefold())) if scene_name else None
            row = StoryboardShot(
                project_id=episode.project_id, episode_id=episode.id, shot_number=index, sort_order=index,
                scene_name=scene_name[:300],
                shot_type=str(shot.get("shot_type", "中景"))[:100],
                camera_movement=str(shot.get("camera_movement", "固定镜头"))[:100],
                duration_seconds=max(1.0, min(60.0, duration)),
                visual_description=str(shot.get("visual_description", ""))[:10000],
                character_actions=str(shot.get("character_actions", ""))[:10000],
                dialogue=str(shot.get("dialogue", ""))[:10000],
                sound_effects=str(shot.get("sound_effects", ""))[:5000],
                image_prompt=str(shot.get("image_prompt", ""))[:12000],
                character_ids=[asset.id for asset in character_assets if asset],
                prop_ids=list(dict.fromkeys(asset.id for asset in prop_assets)),
                scene_asset_id=scene_asset.id if scene_asset else None,
            )
            db.add(row)
            db.flush()
            for asset in character_assets:
                if asset:
                    db.add(StoryAssetLink(project_id=episode.project_id, episode_id=episode.id,
                                          shot_id=row.id, asset_id=asset.id, role="character"))
            for asset in prop_assets:
                db.add(StoryAssetLink(project_id=episode.project_id, episode_id=episode.id,
                                      shot_id=row.id, asset_id=asset.id, role="prop"))
            if scene_asset:
                db.add(StoryAssetLink(project_id=episode.project_id, episode_id=episode.id,
                                      shot_id=row.id, asset_id=scene_asset.id, role="scene"))
            created += 1
        if created == 0:
            raise ValueError("AI返回的分镜列表没有有效镜头")
        db.add(AIUsageLog(provider="ollama-local", model=local_model, task="episode_screenplay_storyboard", status="success", detail="分集：" + episode.title + "；镜头数：" + str(created)))
        db.commit()
        return {"success": True, "episode_id": episode.id, "provider": "ollama-local", "model": local_model, "shot_count": created, "message": "AI影视剧本和分镜生成完成，共" + str(created) + "个镜头。"}
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        db.add(AIUsageLog(provider="ollama-local", model=local_model, task="episode_screenplay_storyboard", status="failed", detail=str(exc)[:2000]))
        db.commit()
        raise HTTPException(502, "AI剧本与分镜生成失败：" + str(exc)) from exc

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
def list_visual_assets(project_id: Optional[int] = None, db: Session = Depends(get_db)):
    items = db.query(VisualAsset).all()
    if project_id is not None:
        project = db.query(StoryProject).filter(StoryProject.id == project_id).first()
        if project is None:
            raise HTTPException(404, "漫剧项目不存在")
        scoped = []
        for asset in items:
            data = asset.data if isinstance(asset.data, dict) else {}
            owner = data.get("source_novel_id")
            if owner in (None, "") or str(owner) == str(project.novel_id):
                scoped.append(asset)
        items = scoped
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
    project = db.query(StoryProject).filter(StoryProject.id == shot.project_id).first()
    if project is None:
        raise HTTPException(404, "漫剧项目不存在")
    asset_data = asset.data if isinstance(asset.data, dict) else {}
    owner = asset_data.get("source_novel_id")
    if owner not in (None, "") and str(owner) != str(project.novel_id):
        raise HTTPException(403, "该素材属于其他小说，不能关联到当前漫剧项目")
    if asset.category not in {"characters", "scenes", "props", "weapons", "costumes", "buildings", "special_items"}:
        raise HTTPException(400, "该素材类别不能关联到分镜")
    snapshot = snapshot_asset_state(asset, payload.chapter_number)
    existing = db.query(StoryAssetLink).filter(
        StoryAssetLink.shot_id == shot_id,
        StoryAssetLink.asset_id == payload.asset_id,
        StoryAssetLink.role == payload.role,
    ).first()
    if existing:
        existing.chapter_number = payload.chapter_number
        existing.state_snapshot = snapshot
        db.commit()
        return {"success": True, "link_id": existing.id, "existing": True, "state_snapshot": snapshot}
    link = StoryAssetLink(
        project_id=shot.project_id,
        episode_id=shot.episode_id,
        shot_id=shot.id,
        asset_id=asset.id,
        role=payload.role,
        chapter_number=payload.chapter_number,
        state_snapshot=snapshot,
    )
    db.add(link)
    db.commit()
    db.refresh(link)
    return {"success": True, "link_id": link.id, "state_snapshot": snapshot}


def build_episode_asset_consistency_report(shot_rows):
    expected_categories = {
        "character": {"characters"},
        "scene": {"scenes"},
        "prop": {"props", "special_items"},
        "weapon": {"weapons"},
        "costume": {"costumes"},
        "building": {"buildings"},
        "special_item": {"special_items"},
        "visual": {"characters", "scenes", "props", "weapons", "costumes", "buildings", "special_items"},
    }
    issues = []
    checked_links = 0
    for shot in shot_rows:
        shot_id = shot["id"]
        shot_number = shot["shot_number"]
        links = shot.get("links") or []
        roles = {link.get("role") for link in links}
        categories = {
            (link.get("asset") or {}).get("category")
            for link in links if isinstance(link.get("asset"), dict)
        }
        if not links:
            issues.append({
                "shot_id": shot_id, "shot_number": shot_number,
                "code": "no_visual_assets", "severity": "warning",
                "message": "该镜头尚未关联任何视觉素材。",
            })
        if (shot.get("scene_name") or "").strip() and "scene" not in roles and "scenes" not in categories:
            issues.append({
                "shot_id": shot_id, "shot_number": shot_number,
                "code": "missing_scene_asset", "severity": "warning",
                "message": "镜头填写了场景名称，但尚未关联场景素材。",
            })
        for link in links:
            checked_links += 1
            asset = link.get("asset") or {}
            asset_id = link.get("asset_id")
            role = link.get("role") or "visual"
            category = asset.get("category")
            base = {
                "shot_id": shot_id, "shot_number": shot_number,
                "asset_id": asset_id, "asset_name": asset.get("name") or "未知素材",
            }
            if not asset:
                issues.append({**base, "code": "missing_asset", "severity": "error",
                               "message": "关联的视觉素材记录不存在。"})
                continue
            allowed = expected_categories.get(role, expected_categories["visual"])
            if category not in allowed:
                issues.append({**base, "code": "role_category_mismatch", "severity": "error",
                               "message": f"关联角色“{role}”与素材分类“{category}”不匹配。"})
            if not asset.get("confirmed"):
                issues.append({**base, "code": "asset_not_confirmed", "severity": "warning",
                               "message": "该素材尚未审核确认，正式生成前请先检查并确认。"})
            owner = (asset.get("data") or {}).get("source_novel_id") if isinstance(asset.get("data"), dict) else None
            if owner not in (None, "") and str(owner) != str(shot.get("novel_id")):
                issues.append({**base, "code": "foreign_novel_asset", "severity": "error",
                               "message": "该素材来源于其他小说，不能用于当前改编项目。"})
            if not link.get("state_snapshot"):
                issues.append({**base, "code": "missing_state_snapshot", "severity": "warning",
                               "message": "该关联缺少剧情状态快照，建议重新确认素材关联。"})
            if link.get("newer_state_available"):
                issues.append({**base, "code": "newer_state_available", "severity": "warning",
                               "message": "素材在后续章节发生变化；本镜头仍锁定旧状态，请核对是否符合当前剧情。"})
    errors = sum(1 for issue in issues if issue["severity"] == "error")
    warnings = sum(1 for issue in issues if issue["severity"] == "warning")
    return {
        "success": True,
        "shot_count": len(shot_rows),
        "checked_link_count": checked_links,
        "error_count": errors,
        "warning_count": warnings,
        "ready": errors == 0 and warnings == 0,
        "issues": issues,
        "note": "这是本地规则检查，不调用 AI 或第三方服务；警告需要人工判断，不会自动替换或修改素材。",
    }


@router.get("/episodes/{episode_id}/asset-consistency")
def check_episode_asset_consistency(episode_id: int, db: Session = Depends(get_db)):
    episode = db.query(EpisodePlan).filter(EpisodePlan.id == episode_id).first()
    if episode is None:
        raise HTTPException(404, "分集不存在")
    project = db.query(StoryProject).filter(StoryProject.id == episode.project_id).first()
    if project is None:
        raise HTTPException(404, "分集所属改编项目不存在")
    shots = db.query(StoryboardShot).filter(
        StoryboardShot.episode_id == episode_id,
        StoryboardShot.project_id == episode.project_id,
    ).order_by(StoryboardShot.sort_order, StoryboardShot.shot_number).all()
    report_rows = []
    for shot in shots:
        links = db.query(StoryAssetLink).filter(
            StoryAssetLink.shot_id == shot.id,
            StoryAssetLink.project_id == project.id,
        ).all()
        serialized_links = []
        for link in links:
            asset = db.query(VisualAsset).filter(VisualAsset.id == link.asset_id).first()
            if asset is None:
                serialized_links.append({"asset_id": link.asset_id, "role": link.role})
                continue
            locked = link.state_snapshot or snapshot_asset_state(asset, link.chapter_number)
            latest = snapshot_asset_state(asset, None)
            locked_chapter = link.chapter_number
            latest_chapter = latest.get("chapter_number")
            serialized_links.append({
                "asset_id": asset.id,
                "role": link.role,
                "state_snapshot": locked,
                "newer_state_available": bool(
                    locked_chapter is not None and latest_chapter is not None
                    and int(latest_chapter) > int(locked_chapter)
                ),
                "asset": {
                    "name": asset.name, "category": asset.category,
                    "confirmed": bool(asset.confirmed), "data": asset.data or {},
                },
            })
        report_rows.append({
            "id": shot.id, "shot_number": shot.shot_number,
            "scene_name": shot.scene_name, "novel_id": project.novel_id,
            "links": serialized_links,
        })
    report = build_episode_asset_consistency_report(report_rows)
    report.update({"episode_id": episode.id, "episode_number": episode.episode_number,
                   "project_id": project.id})
    return report


@router.get("/shots/{shot_id}/assets")
def get_shot_assets(shot_id: int, db: Session = Depends(get_db)):
    shot = db.query(StoryboardShot).filter(StoryboardShot.id == shot_id).first()
    if not shot:
        raise HTTPException(404, "分镜不存在")
    links = db.query(StoryAssetLink).filter(StoryAssetLink.shot_id == shot_id).all()
    result = []
    for link in links:
        asset = db.query(VisualAsset).filter(VisualAsset.id == link.asset_id).first()
        if asset:
            locked_snapshot = link.state_snapshot or snapshot_asset_state(asset, link.chapter_number)
            latest_state = snapshot_asset_state(asset, None)
            locked_chapter = link.chapter_number
            latest_chapter = latest_state.get("chapter_number")
            has_newer_state = bool(
                locked_chapter is not None
                and latest_chapter is not None
                and int(latest_chapter) > int(locked_chapter)
            )
            result.append({
                "role": link.role,
                "chapter_number": link.chapter_number,
                "state_snapshot": locked_snapshot,
                "current_state": latest_state,
                "newer_state_available": has_newer_state,
                "asset": serialize_model(asset),
            })
    return result
