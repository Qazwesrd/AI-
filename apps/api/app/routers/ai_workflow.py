import json
import re
import urllib.request
import urllib.error
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.novel import Novel, Chapter, AnalysisItem
from app.models.story import EpisodePlan, StoryboardShot, StoryAssetLink
from app.models.visual import VisualAsset
from app.models.studio import AIProviderConfig, AIUsageLog
from app.routers.studio import decrypt_key, runtime

router = APIRouter(prefix="/api/ai-workflow", tags=["AI小说转漫剧工作流"])

class AIRequest(BaseModel):
    provider: str = "deepseek"
    confirm_paid: bool = False
    chapter_ids: list[int] = Field(default_factory=list)
    replace_existing: bool = False

def parse_json(value):
    value = (value or "").strip()
    value = re.sub(r"^\`\`\`(?:json)?\s*", "", value, flags=re.I)
    value = re.sub(r"\s*\`\`\`$", "", value)
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        start, end = value.find("{"), value.rfind("}")
        if start >= 0 and end > start:
            return json.loads(value[start:end + 1])
        raise ValueError("AI 返回内容不是有效 JSON")

def call_ai(db, payload, task, system_prompt, user_prompt, max_tokens=7000):
    setting = runtime(db)
    if not setting.network_enabled:
        raise HTTPException(403, "网络调用总开关已关闭")
    if not setting.paid_api_allowed or not payload.confirm_paid:
        raise HTTPException(403, "请先开启付费 API 授权，并在本次请求明确确认费用")
    config = db.query(AIProviderConfig).filter(
        AIProviderConfig.provider == payload.provider.lower()
    ).first()
    if not config or not config.enabled or not config.encrypted_api_key or not config.model or not config.base_url:
        raise HTTPException(400, "文本模型未完整配置或未启用")
    url = config.base_url.rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"
    body = json.dumps({
        "model": config.model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.35,
        "max_tokens": max_tokens
    }, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={
        "Authorization": "Bearer " + decrypt_key(config.encrypted_api_key),
        "Content-Type": "application/json"
    }, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=240) as response:
            result = json.loads(response.read().decode("utf-8"))
        choices = result.get("choices") or []
        content = choices[0].get("message", {}).get("content") if choices else ""
        if isinstance(content, list):
            content = "\n".join(str(x.get("text", "")) for x in content if isinstance(x, dict))
        if not content:
            raise ValueError("模型未返回文本")
        data = parse_json(content)
        db.add(AIUsageLog(provider=config.provider, model=config.model, task=task,
                          status="success", detail="AI 工作流调用成功"))
        db.commit()
        return data
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:2500]
        db.add(AIUsageLog(provider=config.provider, model=config.model, task=task,
                          status="failed", detail=detail))
        db.commit()
        raise HTTPException(502, "AI 服务错误：" + detail)
    except HTTPException:
        raise
    except Exception as exc:
        db.add(AIUsageLog(provider=config.provider, model=config.model, task=task,
                          status="failed", detail=str(exc)[:2000]))
        db.commit()
        raise HTTPException(502, "AI 请求失败：" + str(exc)[:1200])

@router.post("/novels/{novel_id}/analyze")
def deep_analyze_novel(novel_id: int, payload: AIRequest, db: Session = Depends(get_db)):
    novel = db.query(Novel).filter(Novel.id == novel_id).first()
    if not novel:
        raise HTTPException(404, "小说不存在")
    query = db.query(Chapter).filter(Chapter.novel_id == novel_id)
    if payload.chapter_ids:
        query = query.filter(Chapter.id.in_(payload.chapter_ids))
    chapters = query.order_by(Chapter.chapter_number).all()
    if not chapters:
        raise HTTPException(400, "没有可分析的章节")
    text = "\n\n".join(
        "【" + chapter.title + "】\n" + (chapter.content or "") for chapter in chapters
    )
    if len(text) > 120000:
        raise HTTPException(413, "本次文本超过 120000 字符，请按章节分批分析")
    system = (
        "你是长篇小说分析师和影视改编策划。只依据原文，不得臆造。"
        "严格输出 JSON 对象，字段 characters、scenes、props、relationships、events、world_settings、timeline，均为数组。"
        "人物字段 name,description,appearance,clothing,personality,aliases；"
        "场景字段 name,description,visual_details；道具字段 name,description,appearance,owner；"
        "关系字段 source,target,relation,evidence；事件字段 name,description,chapter,importance；"
        "世界观字段 name,description,rules；时间线字段 event,chapter,time,order。"
    )
    result = call_ai(db, payload, "novel_deep_analysis", system,
                     "小说：" + novel.title + "\n原文：\n" + text, 9000)
    mapping = {
        "characters": "character", "scenes": "scene", "props": "prop",
        "relationships": "relationship", "events": "event",
        "world_settings": "world", "timeline": "timeline"
    }
    created = []
    for key, category in mapping.items():
        entries = result.get(key, [])
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or entry.get("event") or "").strip()
            if not name:
                continue
            confirmed = db.query(AnalysisItem).filter(
                AnalysisItem.novel_id == novel_id,
                AnalysisItem.category == category,
                AnalysisItem.name == name,
                AnalysisItem.confirmed.is_(True)
            ).first()
            if confirmed:
                continue
            row = AnalysisItem(
                novel_id=novel_id, category=category, name=name[:300],
                description=str(entry.get("description") or entry.get("visual_details") or "")[:12000],
                data={"source": "ai", "details": entry, "chapter_ids": [x.id for x in chapters]},
                confirmed=False
            )
            db.add(row)
            created.append(row)
    db.commit()
    return {"success": True, "mode": "ai_deep_analysis", "provider": payload.provider,
            "chapter_count": len(chapters), "items_created": len(created),
            "items": [{"id": x.id, "category": x.category, "name": x.name,
                       "description": x.description, "data": x.data, "confirmed": x.confirmed}
                      for x in created]}

@router.post("/novels/{novel_id}/sync-visual-assets")
def sync_visual_assets(novel_id: int, db: Session = Depends(get_db)):
    if not db.query(Novel).filter(Novel.id == novel_id).first():
        raise HTTPException(404, "小说不存在")
    mapping = {"character": "characters", "scene": "scenes", "prop": "props"}
    items = db.query(AnalysisItem).filter(
        AnalysisItem.novel_id == novel_id,
        AnalysisItem.confirmed.is_(True),
        AnalysisItem.category.in_(list(mapping.keys()))
    ).all()
    created = []
    for item in items:
        category = mapping[item.category]
        found = db.query(VisualAsset).filter(
            VisualAsset.category == category, VisualAsset.name == item.name
        ).first()
        if found:
            continue
        details = (item.data or {}).get("details", {})
        asset = VisualAsset(
            category=category, name=item.name, description=item.description or "",
            appearance=str(details.get("appearance") or details.get("visual_details") or ""),
            clothing=str(details.get("clothing") or ""),
            personality=str(details.get("personality") or ""),
            consistency_prompt=str(details.get("consistency_prompt") or ""),
            style="3D动漫电影风格",
            data={"novel_id": novel_id, "analysis_item_id": item.id, "details": details},
            confirmed=False
        )
        db.add(asset)
        db.flush()
        created.append(asset.id)
    db.commit()
    return {"success": True, "created_count": len(created), "asset_ids": created,
            "message": "已同步确认的人物、场景和道具；图片仍需在视觉工作室主动生成。"}

@router.post("/episodes/{episode_id}/generate-screenplay")
def generate_screenplay(episode_id: int, payload: AIRequest, db: Session = Depends(get_db)):
    episode = db.query(EpisodePlan).filter(EpisodePlan.id == episode_id).first()
    if not episode:
        raise HTTPException(404, "分集不存在")
    source = (episode.source_text or "").strip()
    if not source:
        raise HTTPException(400, "分集没有原著内容")
    if len(source) > 50000:
        raise HTTPException(413, "单集原文超过 50000 字符，请先拆分")
    system = (
        "你是专业影视编剧。保留原著主线、人物关系、因果和关键冲突，不得臆造重大情节。"
        "改编为约 120 秒漫剧剧本，时长范围 90-150 秒。"
        "严格输出 JSON：title、synopsis、screenplay、estimated_duration_seconds。"
        "剧本需包含场景、动作、表情、对白、旁白和转场。"
    )
    result = call_ai(db, payload, "episode_screenplay", system,
                     "分集：" + episode.title + "\n原文：\n" + source, 7000)
    screenplay = str(result.get("screenplay") or "").strip()
    if not screenplay:
        raise HTTPException(502, "AI 没有返回剧本正文，原有内容未修改")
    try:
        duration = max(90, min(150, int(result.get("estimated_duration_seconds", 120))))
    except (ValueError, TypeError):
        duration = 120
    episode.title = str(result.get("title") or episode.title)[:300]
    episode.synopsis = str(result.get("synopsis") or "")[:12000]
    episode.screenplay = screenplay[:50000]
    episode.duration_seconds = duration
    episode.status = "screenplay_ready"
    episode.updated_at = datetime.utcnow()
    db.commit()
    return {"success": True, "episode_id": episode.id, "title": episode.title,
            "synopsis": episode.synopsis, "screenplay": episode.screenplay,
            "duration_seconds": episode.duration_seconds}

@router.post("/episodes/{episode_id}/generate-storyboard")
def generate_storyboard(episode_id: int, payload: AIRequest, db: Session = Depends(get_db)):
    episode = db.query(EpisodePlan).filter(EpisodePlan.id == episode_id).first()
    if not episode:
        raise HTTPException(404, "分集不存在")
    existing = db.query(StoryboardShot).filter(StoryboardShot.episode_id == episode_id).count()
    if existing and not payload.replace_existing:
        raise HTTPException(409, "已有分镜；如需覆盖，请明确设置 replace_existing=true")
    source = (episode.screenplay or episode.source_text or "").strip()
    if not source:
        raise HTTPException(400, "请先生成剧本或导入分集原文")
    system = (
        "你是漫剧分镜导演。按剧情节奏拆成连续镜头，适配 90-150 秒。"
        "严格输出 JSON 对象，字段 shots 为数组。每个镜头字段：scene_name、shot_type、"
        "camera_movement、duration_seconds、visual_description、character_actions、"
        "dialogue、sound_effects、image_prompt、characters、props。"
        "characters 和 props 必须是实体名称数组；不要输出 Markdown。"
    )
    result = call_ai(db, payload, "episode_storyboard", system,
                     "分集：" + episode.title + "\n目标时长：" + str(episode.duration_seconds) +
                     "\n剧本：\n" + source, 9000)
    rows = result.get("shots")
    if not isinstance(rows, list) or not rows:
        raise HTTPException(502, "AI 未返回有效分镜，原有分镜未修改")
    if existing:
        old_ids = [x[0] for x in db.query(StoryboardShot.id).filter(
            StoryboardShot.episode_id == episode_id).all()]
        if old_ids:
            db.query(StoryAssetLink).filter(StoryAssetLink.shot_id.in_(old_ids)).delete(
                synchronize_session=False)
        db.query(StoryboardShot).filter(
            StoryboardShot.episode_id == episode_id).delete(synchronize_session=False)
    assets = db.query(VisualAsset).all()
    lookup = {(x.category, x.name.strip().casefold()): x for x in assets}
    created, matched = [], 0
    for index, raw in enumerate(rows, start=1):
        if not isinstance(raw, dict):
            continue
        scene_name = str(raw.get("scene_name") or "").strip()
        character_ids, prop_ids, links = [], [], []
        for name in raw.get("characters", []) if isinstance(raw.get("characters"), list) else []:
            asset = lookup.get(("characters", str(name).strip().casefold()))
            if asset:
                character_ids.append(asset.id)
                links.append((asset.id, "character"))
        for name in raw.get("props", []) if isinstance(raw.get("props"), list) else []:
            asset = lookup.get(("props", str(name).strip().casefold()))
            if asset:
                prop_ids.append(asset.id)
                links.append((asset.id, "prop"))
        scene = lookup.get(("scenes", scene_name.casefold())) if scene_name else None
        try:
            duration = max(1.0, min(30.0, float(raw.get("duration_seconds", 5))))
        except (ValueError, TypeError):
            duration = 5.0
        shot = StoryboardShot(
            project_id=episode.project_id, episode_id=episode.id, shot_number=index,
            scene_name=scene_name[:300], shot_type=str(raw.get("shot_type") or "中景")[:100],
            camera_movement=str(raw.get("camera_movement") or "固定镜头")[:100],
            duration_seconds=duration,
            visual_description=str(raw.get("visual_description") or "")[:12000],
            character_actions=str(raw.get("character_actions") or "")[:12000],
            dialogue=str(raw.get("dialogue") or "")[:12000],
            sound_effects=str(raw.get("sound_effects") or "")[:3000],
            image_prompt=str(raw.get("image_prompt") or "")[:12000],
            character_ids=character_ids, scene_asset_id=scene.id if scene else None,
            prop_ids=prop_ids, status="draft", sort_order=index
        )
        db.add(shot)
        db.flush()
        if scene:
            links.append((scene.id, "scene"))
        for asset_id, role in links:
            db.add(StoryAssetLink(project_id=episode.project_id, episode_id=episode.id,
                                  shot_id=shot.id, asset_id=asset_id, role=role))
        matched += len(links)
        created.append(shot)
    db.commit()
    return {"success": True, "episode_id": episode.id, "shot_count": len(created),
            "matched_asset_links": matched,
            "total_duration_seconds": round(sum(x.duration_seconds for x in created), 2),
            "shots": [{"id": x.id, "shot_number": x.shot_number, "scene_name": x.scene_name,
                       "duration_seconds": x.duration_seconds, "image_prompt": x.image_prompt,
                       "character_ids": x.character_ids, "scene_asset_id": x.scene_asset_id,
                       "prop_ids": x.prop_ids} for x in created]}
