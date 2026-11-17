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

    runtime = db.query(AIRuntimeSetting).first()
    if not runtime or not runtime.network_enabled:
        raise HTTPException(403, "网络调用总开关尚未开启")

    providers = db.query(AIProviderConfig).filter(
        AIProviderConfig.enabled == True,
        AIProviderConfig.purpose == "text",
        AIProviderConfig.base_url != "",
        AIProviderConfig.model != "",
        AIProviderConfig.encrypted_api_key != "",
    ).all()
    priority = {"deepseek": 0, "openai": 1, "xai": 2, "grok": 2, "gemini": 3, "cloudflare": 4}
    providers.sort(key=lambda item: priority.get(item.provider.lower(), 99))
    if not providers:
        raise HTTPException(400, "没有已启用的文本 AI 模型，请先完成 AI 管理中心配置")
    provider = providers[0]
    paid = bool(getattr(provider, "paid", True))
    if paid and not runtime.paid_api_allowed:
        raise HTTPException(403, "当前模型属于付费服务，请先开启付费 API 总授权")
    if paid and not payload.confirm_paid:
        raise HTTPException(403, "本次 AI 分集规划可能产生费用，尚未确认")

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

    # 每次只处理少量章节；每章完成后立即提交，断网/关闭软件后可从断点继续。
    batch_end = min(cursor + 3, len(chapters))
    try:
        api_key = decrypt_key(provider.encrypted_api_key)
        for chapter_index in range(cursor, batch_end):
            chapter = chapters[chapter_index]
            source = get_chapter_content(chapter)
            title = get_chapter_title(chapter)
            if source.strip():
                prompt = (
                    "你是资深影视编剧和漫剧分集导演。阅读整章原文，决定本章应改编为1集还是2集。"
                    "必须根据剧情完整性、冲突转折、场景变化、人物弧线和约2分钟单集节奏判断，禁止按字数机械对半切。"
                    "如果一集能完整表达本章核心剧情，返回1集；只有剧情确实过长且存在自然戏剧断点时才返回2集。"
                    "若拆为2集，split_quote 必须是原文中连续、逐字一致的一段短句，作为第二集开始位置。"
                    "只返回 JSON：{\"episode_count\":1或2,\"split_quote\":\"原文精确句子或空字符串\","
                    "\"synopsis\":\"本章剧情摘要\",\"episode1_title\":\"标题\","
                    "\"episode1_synopsis\":\"上集摘要\",\"episode2_title\":\"下集标题\","
                    "\"episode2_synopsis\":\"下集摘要\"}。"
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
                raw = str(raw).strip()
                if raw.startswith("```json"):
                    raw = raw[7:].strip()
                elif raw.startswith("```"):
                    raw = raw[3:].strip()
                if raw.endswith("```"):
                    raw = raw[:-3].strip()
                try:
                    decision = json.loads(raw)
                except json.JSONDecodeError:
                    left, right = raw.find("{"), raw.rfind("}")
                    if left < 0 or right <= left:
                        raise ValueError("AI 分集规划返回的 JSON 无效")
                    decision = json.loads(raw[left:right + 1])

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

            # 每章都记录断点并提交，已完成章节不会因后续失败而回滚。
            progress.update({
                "status": "running",
                "chapter_cursor": chapter_index + 1,
                "total_chapters": len(chapters),
                "provider": provider.provider,
                "model": provider.model,
                "updated_at": datetime.utcnow().isoformat(),
            })
            settings["ai_episode_planning"] = progress
            project.settings = settings
            project.status = "planning"
            db.add(AIUsageLog(
                provider=provider.provider, model=provider.model,
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

    except urllib.error.HTTPError as exc:
        db.rollback()
        detail = exc.read().decode("utf-8", errors="replace")[:1500]
        raise HTTPException(502, "AI 分集规划接口错误：" + str(exc.code) + " " + detail) from exc
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
    if not (episode.source_text or "").strip():
        raise HTTPException(400, "本集原著内容为空")
    if db.query(StoryboardShot).filter(StoryboardShot.episode_id == episode_id).count():
        raise HTTPException(409, "本集已有分镜。请先备份现有成果，再决定是否清除后重新生成。")
    runtime = db.query(AIRuntimeSetting).first()
    if not runtime or not runtime.network_enabled:
        raise HTTPException(403, "网络调用总开关尚未开启")
    providers = db.query(AIProviderConfig).filter(
        AIProviderConfig.enabled == True, AIProviderConfig.purpose == "text",
        AIProviderConfig.base_url != "", AIProviderConfig.model != "",
        AIProviderConfig.encrypted_api_key != "",
    ).all()
    priority = {"deepseek": 0, "openai": 1, "xai": 2, "grok": 2, "gemini": 3, "cloudflare": 4}
    providers.sort(key=lambda item: priority.get(item.provider.lower(), 99))
    if not providers:
        raise HTTPException(400, "没有已启用的文本 AI 模型，请先完成 AI 管理中心配置")
    provider = providers[0]
    paid = bool(getattr(provider, "paid", True))
    if paid and not runtime.paid_api_allowed:
        raise HTTPException(403, "当前模型属于付费服务，请先开启付费 API 总授权")
    if paid and not payload.confirm_paid:
        raise HTTPException(403, "本次剧本与分镜生成可能产生费用，尚未确认")
    project = db.query(StoryProject).filter(StoryProject.id == episode.project_id).first()
    style = project.adaptation_style if project else "3D动漫电影风格"
    target = project.target_duration if project else 120
    source = (episode.source_text or "")[:28000]
    prompt = (
        "你是专业影视编剧、漫剧导演和分镜师。把原著改编成可直接制作的短篇漫剧。"
        "忠实保留原著核心含义、人物身份关系、剧情因果、关键事件和人物立场；允许压缩重复描写，不得擅自改结局或编造关键事实。"
        "剧本必须包含场景、动作、表情、走位、对白、旁白和转场；分镜必须逐段覆盖剧本，不得遗漏关键剧情。"
        "每个镜头必须写明景别、运镜、画面构图、角色动作、场景、对白/旁白、音效和可直接用于图像模型的提示词。"
        "画面风格：" + style + "。目标时长约" + str(target) + "秒，成片允许90至150秒。生成10至24个镜头，镜头时长合计尽量接近目标时长。"
        "输出简体中文 JSON，不要 Markdown，格式：{\"title\":\"标题\",\"synopsis\":\"剧情摘要\",\"screenplay\":\"分场影视剧本\",\"shots\":[{\"scene_name\":\"场景\",\"shot_type\":\"景别\",\"camera_movement\":\"运镜\",\"duration_seconds\":5,\"visual_description\":\"画面\",\"character_actions\":\"动作表情走位\",\"dialogue\":\"对白旁白\",\"sound_effects\":\"音效\",\"image_prompt\":\"图像模型提示词\"}]}。"
        "小说章节原文：\n" + source
    )
    try:
        api_key = decrypt_key(provider.encrypted_api_key)
        body = json.dumps({"model": provider.model, "temperature": 0.35, "messages": [
            {"role": "system", "content": "你只输出符合结构的 JSON，不输出 Markdown。"},
            {"role": "user", "content": prompt},
        ]}, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(provider.base_url.rstrip("/") + "/chat/completions", data=body,
            headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=360) as response:
            result = json.loads(response.read().decode("utf-8"))
        raw = result["choices"][0]["message"]["content"]
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
        for index, shot in enumerate(shots[:24], 1):
            if not isinstance(shot, dict):
                continue
            try:
                duration = float(shot.get("duration_seconds", 5) or 5)
            except (TypeError, ValueError):
                duration = 5.0
            row = StoryboardShot(
                project_id=episode.project_id, episode_id=episode.id, shot_number=index, sort_order=index,
                scene_name=str(shot.get("scene_name", ""))[:300],
                shot_type=str(shot.get("shot_type", "中景"))[:100],
                camera_movement=str(shot.get("camera_movement", "固定镜头"))[:100],
                duration_seconds=max(1.0, min(60.0, duration)),
                visual_description=str(shot.get("visual_description", ""))[:10000],
                character_actions=str(shot.get("character_actions", ""))[:10000],
                dialogue=str(shot.get("dialogue", ""))[:10000],
                sound_effects=str(shot.get("sound_effects", ""))[:5000],
                image_prompt=str(shot.get("image_prompt", ""))[:12000],
            )
            db.add(row)
            created += 1
        if created == 0:
            raise ValueError("AI返回的分镜列表没有有效镜头")
        db.add(AIUsageLog(provider=provider.provider, model=provider.model, task="episode_screenplay_storyboard", status="success", detail="分集：" + episode.title + "；镜头数：" + str(created)))
        db.commit()
        return {"success": True, "episode_id": episode.id, "provider": provider.provider, "model": provider.model, "shot_count": created, "message": "AI影视剧本和分镜生成完成，共" + str(created) + "个镜头。"}
    except urllib.error.HTTPError as exc:
        db.rollback()
        detail = exc.read().decode("utf-8", errors="replace")[:2000]
        db.add(AIUsageLog(provider=provider.provider, model=provider.model, task="episode_screenplay_storyboard", status="failed", detail=detail))
        db.commit()
        raise HTTPException(502, "AI剧本生成接口错误 HTTP " + str(exc.code) + "：" + detail) from exc
    except Exception as exc:
        db.rollback()
        db.add(AIUsageLog(provider=provider.provider, model=provider.model, task="episode_screenplay_storyboard", status="failed", detail=str(exc)[:2000]))
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
