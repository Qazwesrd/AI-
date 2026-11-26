import json
import re
import hashlib
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.novel import Novel, AnalysisItem, NovelAnalysisJob
from app.models.studio import AIUsageLog
from app.models.task_runtime import TaskExecutionLease
from app.services.task_runtime import acquire_task_lease, release_task_lease, heartbeat_task_lease

router = APIRouter(prefix="/api", tags=["AI小说深度分析"])

CATEGORY_MAP = {
    "人物": "character",
    "角色": "character",
    "character": "character",
    "人物关系": "relationship",
    "关系": "relationship",
    "relationship": "relationship",
    "场景": "scene",
    "地点": "scene",
    "scene": "scene",
    "道具": "prop",
    "物品": "prop",
    "prop": "prop",
    "武器": "weapon",
    "兵器": "weapon",
    "刀具": "weapon",
    "刀剑": "weapon",
    "weapon": "weapon",
    "服装": "costume",
    "衣着": "costume",
    "costume": "costume",
    "建筑": "architecture",
    "architecture": "architecture",
    "特殊物品": "special_item",
    "法器": "special_item",
    "special_item": "special_item",
    "costume": "costume",
    "建筑": "architecture",
    "architecture": "architecture",
    "事件": "event",
    "剧情事件": "event",
    "event": "event",
    "世界观": "world",
    "world": "world",
    "时间线": "timeline",
    "timeline": "timeline",
}

ALLOWED_CATEGORIES = set(CATEGORY_MAP.values())

# 人物识别辅助工具：只依据明确名称或明确别名进行合并，
# 不使用模糊相似度，避免把不同人物错误合并。
GENERIC_CHARACTER_ALIASES = {
    "他", "她", "它", "自己", "对方",
    "男人", "女人", "少年", "少女", "老人",
    "师父", "师傅", "老师", "师兄", "师姐",
    "师弟", "师妹", "前辈", "后辈",
    "大人", "公子", "小姐", "先生", "夫人",
    "父亲", "母亲", "爹", "娘", "哥哥", "姐姐",
    "弟弟", "妹妹", "叔叔", "伯伯", "长老",
    "族长", "掌门", "宗主", "殿下", "陛下",
}

def normalize_entity_name(value):
    import unicodedata

    value = unicodedata.normalize("NFKC", str(value or ""))
    value = value.casefold().strip()
    return re.sub(r"[\W_]+", "", value, flags=re.UNICODE)




def normalize_alias_list(value):
    """将人物别名统一转换为去重后的字符串列表。"""
    if value is None:
        return []
    if isinstance(value, str):
        values = re.split(r"[,，、;；|/\\n]+", value)
    elif isinstance(value, (list, tuple, set)):
        values = list(value)
    else:
        return []

    result = []
    seen = set()
    for item in values:
        name = str(item).strip()
        key = normalize_entity_name(name)
        if name and key and key not in seen:
            seen.add(key)
            result.append(name)
    return result

def character_alias_keys(item):
    data = item.get("data", {})
    if not isinstance(data, dict):
        data = {}

    values = [item.get("name", "")]
    aliases = data.get("aliases", [])

    if isinstance(aliases, str):
        aliases = re.split(r"[,，、;；|/]", aliases)

    if isinstance(aliases, list):
        values.extend(aliases)

    result = set()

    for value in values:
        raw = str(value or "").strip()
        normalized = normalize_entity_name(raw)

        if not normalized:
            continue

        if raw in GENERIC_CHARACTER_ALIASES:
            continue

        if normalized in GENERIC_CHARACTER_ALIASES:
            continue

        # 单字称呼和极短别名容易产生误合并，不参与别名关联。
        if len(normalized) < 2:
            continue

        result.add(normalized)

    return result


def merge_data_values(old_data, new_data):
    result = dict(old_data or {})

    for key, value in (new_data or {}).items():
        if key not in result or result[key] in ("", None, [], {}):
            result[key] = value
            continue

        old_value = result[key]

        if isinstance(old_value, list) and isinstance(value, list):
            combined = list(old_value)
            for entry in value:
                if entry not in combined:
                    combined.append(entry)
            result[key] = combined

        elif isinstance(old_value, dict) and isinstance(value, dict):
            result[key] = merge_data_values(old_value, value)

        elif isinstance(old_value, str) and isinstance(value, str):
            if value and value not in old_value:
                result[key] = old_value + "\n" + value

    return result


def merge_analysis_items(items):
    merged = []
    exact_index = {}
    character_indices = []

    for item in items:
        if not isinstance(item, dict):
            continue

        raw_category = str(item.get("category", "")).strip()
        category = CATEGORY_MAP.get(raw_category, raw_category.lower())

        name = str(item.get("name", "")).strip()
        description = str(item.get("description", "")).strip()
        data = item.get("data", {})

        if category not in ALLOWED_CATEGORIES or not name:
            continue

        if not isinstance(data, dict):
            data = {}

        current = {
            "category": category,
            "name": name,
            "description": description,
            "data": dict(data),
        }

        normalized_name = normalize_entity_name(name)
        if not normalized_name:
            continue

        key = (category, normalized_name)
        target_index = exact_index.get(key)

        if category == "character":
            current_aliases = character_alias_keys(current)

            if target_index is None and current_aliases:
                for old_index in character_indices:
                    old_aliases = character_alias_keys(merged[old_index])

                    if current_aliases.intersection(old_aliases):
                        target_index = old_index
                        break

        if target_index is None:
            target_index = len(merged)
            merged.append(current)
            exact_index[key] = target_index

            if category == "character":
                character_indices.append(target_index)

            continue

        old = merged[target_index]

        if description and description not in old["description"]:
            old["description"] = (
                old["description"] + "\n" + description
            ).strip()

        old["data"] = merge_data_values(old["data"], current["data"])

        # 保存不同章节中出现的正式名称和明确别名。
        if category == "character":
            aliases = old["data"].get("aliases", [])
            if not isinstance(aliases, list):
                aliases = [str(aliases)]

            for alias in [name] + normalize_alias_list(current["data"].get("aliases")):
                alias = str(alias or "").strip()
                if alias and alias != old["name"] and alias not in aliases:
                    aliases.append(alias)

            old["data"]["aliases"] = aliases

    return merged


def confirmed_character_alias_keys(row):
    item = {
        "name": row.name,
        "data": row.data if isinstance(row.data, dict) else {},
    }
    return character_alias_keys(item)




class AIAnalysisInput(BaseModel):
    confirm_paid: bool = False


def split_chunk_state(chunk_results):
    raw = dict(chunk_results or {})
    states = raw.pop("__chunk_states__", {})
    return raw, dict(states) if isinstance(states, dict) else {}


def merge_chunk_state(saved_results, chunk_states):
    return {**dict(saved_results or {}), "__chunk_states__": dict(chunk_states or {})}


def unknown_chunk_keys(chunk_states):
    return [
        key for key, state in (chunk_states or {}).items()
        if state in ("submitting", "submission_unknown", "interrupted_unknown")
    ]


def make_chunks(chapters, limit=4000):
    chunks = []
    current = ""

    for chapter in chapters:
        header = f"\n\n【第{chapter.chapter_number}章：{chapter.title}】\n"
        content = chapter.content or ""

        if not content.strip():
            continue

        text = header + content

        while text:
            available = limit - len(current)

            if available <= 0:
                chunks.append(current)
                current = ""
                available = limit

            if len(text) <= available:
                current += text
                text = ""
            else:
                cut = text.rfind("\n", 0, available)
                if cut < available // 2:
                    cut = available

                current += text[:cut]
                text = text[cut:]

                chunks.append(current)
                current = ""

    if current.strip():
        chunks.append(current)

    return chunks


def extract_json(content):
    if isinstance(content, list):
        content = "".join(
            x.get("text", "") for x in content if isinstance(x, dict)
        )

    content = str(content or "").strip()
    content = re.sub(r"^```(?:json)?\s*", "", content)
    content = re.sub(r"\s*```$", "", content)

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        start = content.find("{")
        end = content.rfind("}")
        if start >= 0 and end > start:
            return json.loads(content[start:end + 1])
        raise ValueError("AI返回内容不是有效JSON")


def _analysis_local_model():
    from app.routers.local_ai_runtime import _installed_models

    installed = _installed_models()
    names = [item["name"] for item in installed if isinstance(item, dict) and item.get("name")]
    for preferred in ("qwen3.5:4b", "qwen3:4b"):
        if preferred in names:
            return preferred
    raise HTTPException(503, "没有已安装的 Qwen 本地文本模型；小说深度分析不会联网下载。")


def analyze_chunk(model, content, chapter_label):
    from app.routers.local_ai_runtime import _ollama_request

    system_prompt = """
你是一名专业的长篇小说人物档案分析师、影视改编分析师和漫剧资产策划师。
从小说正文中提取可靠、可用于后续影视制作的结构化资料。

人物识别：
1. 识别所有有明确剧情作用的人物，区分主要、次要、配角和临时人物。
2. 优先使用正文正式全名；只有正文明确证明同一人物时才记录别名。
3. 不得因姓氏、名字、身份或称呼相似而合并人物。
4. 不得把“师父”“长老”“公子”“小姐”等普通称呼自动认定为人物别名。
5. 不确定是否同一人物时，不要合并，在 uncertain 中说明。
6. 尽可能记录身份、外貌、发型、体型、服装、性格、能力、所属势力和人物变化。
7. 不能把其他人物的身份或外貌移植过来。

其他信息：
识别人物关系、场景、道具、武器、服装、建筑、特殊物品、事件、世界观和时间线。
武器必须记录类别、结构、材质、颜色、使用者和原文依据，并与普通道具区分。
只提取正文有依据的信息，不得编造；尽量记录章节编号和原文证据。
同一人物跨章节的外貌、服装和身份变化应说明变化，不得覆盖成矛盾设定。

人物 data 尽可能包含 aliases、role_level、identity、appearance、clothing、
personality、abilities、faction、relationships、participants、location、evidence、uncertain。

只输出 JSON，不要 Markdown，结构：
{"items":[{"category":"character","name":"人物正式名称","description":"完整描述","data":{"aliases":[],"role_level":"main","identity":"","appearance":"","clothing":"","personality":"","abilities":"","faction":"","relationships":[],"participants":[],"location":"","evidence":"","uncertain":[]}}]}
category 只能是 character, relationship, scene, prop, weapon, costume, architecture, special_item, event, world, timeline。
没有可靠信息时返回空 items。
"""
    prompt = (
        f"小说片段：{chapter_label}\n\n"
        "请分析以下正文，提取所有有可靠依据的实体和关系：\n" + content
    )
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
                    "temperature": 0.2,
                    "num_ctx": 12288,
                    "num_predict": 4096,
                },
            },
            timeout=900.0,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise RuntimeError("本地小说分析模型调用失败：" + str(exc)[:1200]) from exc

    answer = result.get("response") if isinstance(result, dict) else None
    parsed = extract_json(answer)
    if not isinstance(parsed, dict):
        raise ValueError("本地模型返回的数据结构不正确")
    items = parsed.get("items", [])
    if not isinstance(items, list):
        raise ValueError("本地模型返回的 items 不是列表")
    return items


def deduplicate_character_items(items):
    """
    合并跨章节重复人物。
    只使用正式姓名或有明确证据的正式姓名-别名对应关系。
    不使用泛称交集直接合并人物。
    """

    import re
    import unicodedata

    generic_names = {
        "他", "她", "他们", "她们", "我", "你",
        "师父", "师傅", "师尊", "长老", "掌门",
        "宗主", "公子", "小姐", "少爷", "大人",
        "前辈", "后辈", "弟子", "徒弟", "师兄",
        "师姐", "师弟", "师妹", "老人", "男子",
        "女子", "少年", "少女", "男人", "女人",
        "黑衣人", "白衣人", "守卫", "侍卫",
        "士兵", "下人", "仆人", "路人"
    }

    def norm(value):
        if value is None:
            return ""
        value = unicodedata.normalize("NFKC", str(value)).casefold()
        return re.sub(r"[\s\W_]+", "", value, flags=re.UNICODE)

    def get_aliases(item):
        data = item.get("data") or {}
        raw = data.get("aliases", [])

        if isinstance(raw, str):
            raw = re.split(r"[,，、;；|/]+", raw)

        if not isinstance(raw, (list, tuple, set)):
            raw = []

        result = set()
        for value in raw:
            key = norm(value)
            if key and key not in generic_names and len(key) >= 2:
                result.add(key)

        return result

    def identity_conflict(a, b):
        da = a.get("data") or {}
        db = b.get("data") or {}

        for key in ("identity", "faction", "role_level"):
            va = norm(da.get(key))
            vb = norm(db.get(key))

            if va and vb and va != vb:
                return True

        return False

    def should_merge(a, b):
        if a.get("category") != b.get("category"):
            return False

        if a.get("category") != "character":
            return norm(a.get("name")) == norm(b.get("name"))

        na = norm(a.get("name"))
        nb = norm(b.get("name"))

        if not na or not nb:
            return False

        if identity_conflict(a, b):
            return False

        if na == nb:
            return True

        aa = get_aliases(a)
        ab = get_aliases(b)

        # 必须存在正式姓名与另一条记录明确别名的对应关系。
        # 不因双方都叫“师父”“长老”等泛称而合并。
        return na in ab or nb in aa

    def merge_data(target, incoming):
        if not isinstance(target, dict):
            target = {}

        if not isinstance(incoming, dict):
            return target

        for key, value in incoming.items():
            if value is None or value == "":
                continue

            old = target.get(key)

            if old is None or old == "":
                target[key] = value
            elif isinstance(old, list):
                values = value if isinstance(value, list) else [value]
                for entry in values:
                    if entry not in old:
                        old.append(entry)
            elif isinstance(old, dict) and isinstance(value, dict):
                old.update(value)
            elif old != value and key in (
                "aliases", "relationships", "abilities",
                "clothing", "appearance", "evidence"
            ):
                values = value if isinstance(value, list) else [value]
                old_values = old if isinstance(old, list) else [old]
                combined = list(old_values)
                for entry in values:
                    if entry not in combined:
                        combined.append(entry)
                target[key] = combined

        return target

    result = []

    for original in items:
        if not isinstance(original, dict):
            continue

        current = dict(original)
        current["data"] = dict(current.get("data") or {})

        matched = None

        for existing in result:
            if should_merge(existing, current):
                matched = existing
                break

        if matched is None:
            result.append(current)
            continue

        # 保留先出现的正式姓名，补充描述和识别信息。
        old_description = str(matched.get("description") or "")
        new_description = str(current.get("description") or "")

        if new_description and new_description not in old_description:
            matched["description"] = (
                old_description + "\n" + new_description
            ).strip()

        matched["data"] = merge_data(
            matched.get("data") or {},
            current.get("data") or {}
        )

    return result


@router.get("/novels/{novel_id}/analysis-progress")
def get_novel_analysis_progress(novel_id: int, db: Session = Depends(get_db)):
    job = db.query(NovelAnalysisJob).filter(
        NovelAnalysisJob.novel_id == novel_id
    ).order_by(NovelAnalysisJob.id.desc()).first()
    if not job:
        return {"has_job": False, "status": "not_started"}
    return {
        "has_job": True,
        "job_id": job.id,
        "status": job.status,
        "completed_chunks": job.completed_chunks,
        "total_chunks": job.total_chunks,
        "progress_percent": round(
            job.completed_chunks / job.total_chunks * 100, 1
        ) if job.total_chunks else 0,
        "last_error": job.last_error or "",
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }


def _release_stale_analysis_lease(db, job_id):
    lease = db.query(TaskExecutionLease).filter(
        TaskExecutionLease.source == "analysis",
        TaskExecutionLease.task_id == job_id,
        TaskExecutionLease.status == "active",
    ).first()
    if lease is None:
        return False
    last_heartbeat = lease.heartbeat_at or lease.started_at or datetime.utcnow()
    if datetime.utcnow() - last_heartbeat <= timedelta(minutes=20):
        return False
    release_task_lease(
        db, "analysis", job_id, False,
        "本地小说分析租约心跳超过20分钟；已释放上次中断的孤立租约，可安全重试。",
    )
    return True


@router.post("/novels/{novel_id}/analyze-ai")
def analyze_novel_with_ai(
    novel_id: int,
    payload: AIAnalysisInput,
    db: Session = Depends(get_db),
):
    novel = db.query(Novel).filter(Novel.id == novel_id).first()
    if not novel:
        raise HTTPException(404, "小说不存在")

    chapters = sorted(novel.chapters, key=lambda x: x.chapter_number)
    if not chapters:
        raise HTTPException(400, "请先导入小说或创建章节")

    local_model = _analysis_local_model()
    chunks = make_chunks(chapters, limit=4000)
    if not chunks:
        raise HTTPException(400, "小说正文为空")

    source_payload = "\n".join(
        f"{chapter.chapter_number}|{chapter.title}|{chapter.content or ''}"
        for chapter in chapters
    )
    source_signature = hashlib.sha256(source_payload.encode("utf-8")).hexdigest()

    job = db.query(NovelAnalysisJob).filter(
        NovelAnalysisJob.novel_id == novel_id,
        NovelAnalysisJob.source_signature == source_signature,
        NovelAnalysisJob.provider == "ollama-local",
        NovelAnalysisJob.model == local_model,
    ).order_by(NovelAnalysisJob.id.desc()).first()

    if job is None:
        job = NovelAnalysisJob(
            novel_id=novel_id,
            source_signature=source_signature,
            provider="ollama-local",
            model=local_model,
            status="pending",
            total_chunks=len(chunks),
            completed_chunks=0,
            chunk_results={},
        )
        db.add(job)
        db.commit()
        db.refresh(job)

    _release_stale_analysis_lease(db, job.id)
    saved_results, chunk_states = split_chunk_state(job.chunk_results)
    # Local inference has no external billing ambiguity. Any interrupted,
    # uncommitted chunk can be safely retried without a cloud request.
    for key, state in list(chunk_states.items()):
        if state in ("submitting", "submission_unknown", "interrupted_unknown"):
            chunk_states[key] = "pending"
    if (
        not chunk_states
        and len(saved_results) < len(chunks)
        and job.status == "partial"
        and bool(job.last_error)
    ):
        chunk_states[str(len(saved_results) + 1)] = "pending"

    if job.status == "running":
        updated = job.updated_at or job.created_at or datetime.utcnow()
        if datetime.utcnow() - updated < timedelta(minutes=20):
            raise HTTPException(409, "该小说已有本地分析任务正在执行，请勿重复点击；可稍后查看进度或继续。")
        for key, state in list(chunk_states.items()):
            if state == "submitting":
                chunk_states[key] = "pending"
        job.status = "partial"
        job.last_error = "检测到上次本地模型进程中断；未调用云端服务，可安全续作未完成片段。"
        job.chunk_results = merge_chunk_state(saved_results, chunk_states)
        db.commit()

    if job.status == "interrupted_unknown" or unknown_chunk_keys(chunk_states):
        for key in list(unknown_chunk_keys(chunk_states)):
            chunk_states[key] = "pending"
        job.status = "partial"
        job.chunk_results = merge_chunk_state(saved_results, chunk_states)
        job.last_error = "已将旧版本遗留的未知片段标记为可安全重试的本地任务。"
        db.commit()

    job_id = job.id
    acquire_task_lease(db, "analysis", job_id, priority=10)
    if job.total_chunks != len(chunks):
        job.total_chunks = len(chunks)
    job.status = "running"
    job.last_error = ""
    db.commit()

    for index, chunk in enumerate(chunks, start=1):
        chunk_key = str(index)
        if chunk_key in saved_results:
            continue

        try:
            chunk_states[chunk_key] = "submitting"
            job.chunk_results = merge_chunk_state(saved_results, chunk_states)
            job.status = "running"
            job.updated_at = datetime.utcnow()
            db.commit()
            heartbeat_task_lease(db, "analysis", job.id)

            result_items = analyze_chunk(
                local_model,
                chunk,
                f"第{index}/{len(chunks)}段",
            )

            saved_results[chunk_key] = result_items
            chunk_states[chunk_key] = "completed"
            job.chunk_results = merge_chunk_state(saved_results, chunk_states)
            job.completed_chunks = len(saved_results)
            job.status = "partial"
            job.last_error = ""
            job.updated_at = datetime.utcnow()

            db.add(AIUsageLog(
                provider="ollama-local",
                model=local_model,
                task="novel_deep_analysis",
                status="success",
                detail=f"小说：{novel.title}；分析片段：{index}/{len(chunks)}；任务：{job.id}",
            ))
            db.commit()
            heartbeat_task_lease(db, "analysis", job.id)

        except Exception as exc:
            db.rollback()
            job = db.query(NovelAnalysisJob).filter(NovelAnalysisJob.id == job.id).first()
            current_results, current_states = split_chunk_state(job.chunk_results if job else {})
            result_already_saved = chunk_key in current_results
            if result_already_saved:
                current_states[chunk_key] = "completed"
            else:
                current_states[chunk_key] = "pending"
            if job:
                job.chunk_results = merge_chunk_state(current_results, current_states)
                job.completed_chunks = len(current_results)
                job.status = "partial"
                job.last_error = (
                    f"第{index}/{len(chunks)}段结果已保存，但本地心跳更新失败；可安全继续。"
                    if result_already_saved else
                    f"第{index}/{len(chunks)}段本地分析未完成；未调用云端服务或产生 API 费用，可安全重试。诊断详情：{str(exc)[:1200]}"
                )
                job.updated_at = datetime.utcnow()
                if not result_already_saved:
                    db.add(AIUsageLog(
                        provider="ollama-local",
                        model=local_model,
                        task="novel_deep_analysis",
                        status="failed",
                        detail=f"小说：{novel.title}；片段：{index}/{len(chunks)}；任务：{job.id}；{str(exc)[:1200]}",
                    ))
                db.commit()
            release_task_lease(db, "analysis", job_id, False, job.last_error if job else str(exc)[:2000])
            if result_already_saved:
                raise HTTPException(
                    502,
                    f"第{index}/{len(chunks)}段分析结果已保存，但本地心跳更新失败；不会重复分析该片段，可再次点击继续。",
                ) from exc
            raise HTTPException(
                502,
                f"第{index}/{len(chunks)}段本地分析未完成，已保存之前完成的片段；可再次点击继续。错误：{exc}",
            ) from exc

    all_items = []
    for index in range(1, len(chunks) + 1):
        all_items.extend(saved_results.get(str(index), []))
    merged_items = merge_analysis_items(all_items)

    try:
        existing = db.query(AnalysisItem).filter(
            AnalysisItem.novel_id == novel_id
        ).all()
        confirmed = [x for x in existing if x.confirmed]
        confirmed_character_keys = set()
        confirmed_exact_keys = set()

        for row in confirmed:
            confirmed_exact_keys.add((row.category, normalize_entity_name(row.name)))
            if row.category == "character":
                confirmed_character_keys.update(confirmed_character_alias_keys(row))

        for old in existing:
            if not old.confirmed:
                db.delete(old)

        created = []
        merged_items = deduplicate_character_items(merged_items)
        for value in merged_items:
            category = value["category"]
            name = value["name"]
            key = (category, normalize_entity_name(name))
            if key in confirmed_exact_keys:
                continue
            if category == "character":
                aliases = character_alias_keys(value)
                if aliases.intersection(confirmed_character_keys):
                    continue

            row = AnalysisItem(
                novel_id=novel_id,
                category=category,
                name=name,
                description=value["description"],
                data={
                    **value["data"],
                    "source": "ai_deep_analysis",
                    "needs_review": True,
                    "provider": "ollama-local",
                    "model": local_model,
                },
                confirmed=False,
            )
            db.add(row)
            created.append(row)

        db.commit()
        job.status = "completed"
        job.completed_chunks = len(chunks)
        job.last_error = ""
        job.updated_at = datetime.utcnow()
        db.commit()
        release_task_lease(db, "analysis", job.id, True)

        for row in created:
            db.refresh(row)

        return {
            "success": True,
            "mode": "ai_deep_analysis",
            "provider": "ollama-local",
            "model": local_model,
            "chunk_count": len(chunks),
            "job_id": job.id,
            "completed_chunks": job.completed_chunks,
            "total_chunks": job.total_chunks,
            "total": len(created),
            "message": (
                f"本地模型 {local_model} 完成小说深度分析，共分析{len(chunks)}个文本片段，"
                f"提取{len(created)}条待确认信息；不会调用云端或付费 API。"
            ),
            "items": [
                {
                    "id": x.id,
                    "novel_id": x.novel_id,
                    "category": x.category,
                    "name": x.name,
                    "description": x.description,
                    "data": x.data or {},
                    "confirmed": x.confirmed,
                }
                for x in created
            ],
        }

    except Exception as exc:
        db.rollback()
        release_task_lease(db, "analysis", job.id, False, str(exc)[:2000])
        raise
