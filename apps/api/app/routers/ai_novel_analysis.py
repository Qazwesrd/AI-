import json
import re
import hashlib
from datetime import datetime, timedelta
import urllib.request
import urllib.error

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.novel import Novel, AnalysisItem, NovelAnalysisJob
from app.models.studio import AIProviderConfig, AIUsageLog
from app.routers.studio import runtime, decrypt_key
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


def make_chunks(chapters, limit=14000):
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


def choose_provider(db):
    items = db.query(AIProviderConfig).filter(
        AIProviderConfig.enabled == True,
        AIProviderConfig.purpose == "text",
    ).all()

    priority = {
        "deepseek": 0,
        "openai": 1,
        "xai": 2,
        "gemini": 3,
        "cloudflare": 4,
    }

    items = [
        x for x in items
        if x.provider.lower() != "doubao"
        and x.base_url and x.model and x.encrypted_api_key
    ]

    items.sort(key=lambda x: priority.get(x.provider.lower(), 99))

    return items[0] if items else None


def analyze_chunk(config, api_key, content, chapter_label):
    prompt = """
你是一名专业的长篇小说人物档案分析师、影视改编分析师和漫剧资产策划师。

你的任务是从小说正文中提取可靠、可用于后续影视制作的结构化资料。

【一、人物识别必须严格执行】

1. 识别所有有明确剧情作用的人物，不要只识别主角。
2. 区分主要人物、次要人物、配角、临时出场人物。
3. 每个人物尽可能使用正文中出现的正式全名作为 name。
4. 同一个人物可能出现全名、姓名简称、昵称、乳名、化名、代号、尊称。
5. 只有正文明确表明这些称呼指向同一人物时，才写入 aliases。
6. 不得仅因为姓氏相同、名字相似、身份相似或称呼相似，就判断为同一人物。
7. 不得把“师父”“长老”“公子”“小姐”等普通称呼自动认定为人物别名。
8. 不同人物即使同名，也必须结合身份、关系、所在地点、事件和章节证据区分。
9. 如果暂时无法确定两种称呼是否属于同一人，不要强行归并，在 uncertain 中说明。
10. 人物必须尽可能记录身份、年龄线索、性格、外貌、发型、体型、服装、能力、所属势力和人物变化。
11. 人物身份信息不能从其他人物身上移植过来。
12. 主要人物需要尽可能完整，不能只返回少量主角而遗漏重要配角。

【二、其他结构化信息】

必须识别：
- 人物关系：双方人物、关系类型、变化和证据。
- 场景：名称、地点、环境、时间和氛围。
- 道具：名称、外观、材质、颜色、用途和所属人物。\n- 武器与刀具：名称、类别、刀身/刃口/握柄/护手/鞘的结构、材质、颜色、尺寸、使用者、剧情状态和原文依据；必须与普通道具区分。
- 服装：穿着人物、款式、颜色、材质和使用场合。
- 建筑：名称、结构、外观、建筑风格、材质、规模和所在地点。\n- 特殊物品：法器、神器、信物、关键线索物和具有剧情作用的独特物品。
- 事件：事件名称、参与人物、地点、起因和结果。
- 世界观：时代、地理、势力、规则和能力体系。
- 时间线：事件顺序、时间线索和涉及章节。

【三、证据和真实性】

1. 只提取正文有依据的信息，不得凭空编造。
2. 尽量保留章节编号、章节标题和原文证据。
3. 不确定的内容写入 data.uncertain。
4. 不要把普通形容词当成人物、场景或道具。
5. 人物关系必须尽可能明确双方名称。
6. 每个人物都要尽可能提供可用于保持视觉一致性的描述。
7. 对同一人物不同章节出现的外貌、服装或身份变化，应说明变化，不要覆盖成互相矛盾的固定设定。

【四、人物数据字段】

人物 data 应尽可能包含：
- aliases：正文明确证实的其他称呼
- role_level：main、secondary、supporting 或 temporary
- identity：身份和社会角色
- appearance：外貌
- clothing：服装
- personality：性格
- abilities：能力
- faction：所属势力
- relationships：人物关系线索
- evidence：原文依据和章节
- uncertain：待确认信息

【五、输出要求】

只输出 JSON，不要输出 Markdown 或解释。

严格使用以下结构：
{
  "items": [
    {
      "category": "character",
      "name": "人物正式名称",
      "description": "人物完整描述",
      "data": {
        "aliases": [],
        "role_level": "main",
        "identity": "",
        "appearance": "",
        "clothing": "",
        "personality": "",
        "abilities": "",
        "faction": "",
        "relationships": [],
        "participants": [],
        "location": "",
        "evidence": "",
        "uncertain": []
      }
    }
  ]
}

category 只能使用：
character, relationship, scene, prop, weapon, costume,
architecture, special_item, event, world, timeline。

没有可靠信息时返回空 items。
"""

    body = {
        "model": config.model,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": (
                    f"小说片段：{chapter_label}\n\n"
                    f"请分析以下正文：\n{content}"
                ),
            },
        ],
    }

    url = config.base_url.rstrip("/") + "/chat/completions"

    request = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            result = json.loads(response.read().decode("utf-8"))

        answer = result["choices"][0]["message"]["content"]
        parsed = extract_json(answer)

        if not isinstance(parsed, dict):
            raise ValueError("AI返回的数据结构不正确")

        items = parsed.get("items", [])
        if not isinstance(items, list):
            raise ValueError("AI返回的items不是列表")

        return items

    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:2000]
        raise RuntimeError(f"模型接口返回HTTP {exc.code}: {detail}") from exc
    except Exception as exc:
        raise RuntimeError(f"AI分析请求失败：{exc}") from exc


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

    settings = runtime(db)

    if not settings.network_enabled:
        raise HTTPException(403, "网络调用总开关尚未开启")

    config = choose_provider(db)

    if not config:
        raise HTTPException(
            400,
            "没有找到已启用且配置完整的文本AI模型。请先到AI设置配置DeepSeek、OpenAI、Grok、Gemini或其他文本模型。",
        )

    # 仅对明确标记为付费的服务要求付费总授权和本次确认。
    # 免费服务无需打开付费授权；计费属性缺失时默认按付费处理。
    is_paid = bool(getattr(config, "paid", True))
    if is_paid and not settings.paid_api_allowed:
        raise HTTPException(403, "该文本模型属于付费服务，尚未开启付费API总授权")

    if is_paid and not payload.confirm_paid:
        raise HTTPException(403, "本次AI分析可能产生费用，尚未获得明确确认")

    try:
        api_key = decrypt_key(config.encrypted_api_key)
    except Exception as exc:
        raise HTTPException(500, f"API密钥读取失败：{exc}") from exc

    if not api_key:
        raise HTTPException(400, "文本模型API密钥为空")

    chunks = make_chunks(chapters)

    if not chunks:
        raise HTTPException(400, "小说正文为空")

    source_payload = "\\n".join(
        f"{chapter.chapter_number}|{chapter.title}|{chapter.content or ''}"
        for chapter in chapters
    )
    source_signature = hashlib.sha256(source_payload.encode("utf-8")).hexdigest()

    job = db.query(NovelAnalysisJob).filter(
        NovelAnalysisJob.novel_id == novel_id,
        NovelAnalysisJob.source_signature == source_signature,
        NovelAnalysisJob.provider == config.provider,
        NovelAnalysisJob.model == config.model,
    ).order_by(NovelAnalysisJob.id.desc()).first()

    if job is None:
        job = NovelAnalysisJob(
            novel_id=novel_id,
            source_signature=source_signature,
            provider=config.provider,
            model=config.model,
            status="pending",
            total_chunks=len(chunks),
            completed_chunks=0,
            chunk_results={},
        )
        db.add(job)
        db.commit()
        db.refresh(job)

    if job.status == "running":
        updated = job.updated_at or job.created_at or datetime.utcnow()
        if datetime.utcnow() - updated < timedelta(minutes=15):
            raise HTTPException(409, "该小说已有分析任务正在执行，请勿重复点击；可稍后查看进度或继续。")
        job.status = "partial"
        job.last_error = "检测到上次进程中断，已安全恢复为可续作状态"
        db.commit()

    saved_results = dict(job.chunk_results or {})
    acquire_task_lease(db, "analysis", job.id, priority=10)
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
            result_items = analyze_chunk(
                config,
                api_key,
                chunk,
                f"第{index}/{len(chunks)}段",
            )

            saved_results[chunk_key] = result_items
            job.chunk_results = dict(saved_results)
            job.completed_chunks = len(saved_results)
            job.status = "partial"
            job.last_error = ""
            job.updated_at = datetime.utcnow()

            db.add(AIUsageLog(
                provider=config.provider,
                model=config.model,
                task="novel_deep_analysis",
                status="success",
                detail=f"小说：{novel.title}；分析片段：{index}/{len(chunks)}；任务：{job.id}",
            ))
            db.commit()
            heartbeat_task_lease(db, "analysis", job.id)

        except Exception as exc:
            job.status = "partial"
            job.last_error = str(exc)[:2000]
            job.completed_chunks = len(saved_results)
            db.add(AIUsageLog(
                provider=config.provider,
                model=config.model,
                task="novel_deep_analysis",
                status="failed",
                detail=f"小说：{novel.title}；片段：{index}/{len(chunks)}；任务：{job.id}；{str(exc)[:1500]}",
            ))
            db.commit()
            release_task_lease(db, "analysis", job.id, False, job.last_error)

            raise HTTPException(
                502,
                f"第{index}/{len(chunks)}段分析失败。已保存{len(saved_results)}/{len(chunks)}段；再次点击AI深度分析将从未完成片段继续，不会重复调用已完成片段。错误：{exc}",
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

        # 已确认人物的名称和明确别名也参与冲突保护。
        confirmed_character_keys = set()
        confirmed_exact_keys = set()

        for row in confirmed:
            confirmed_exact_keys.add(
                (row.category, normalize_entity_name(row.name))
            )

            if row.category == "character":
                confirmed_character_keys.update(
                    confirmed_character_alias_keys(row)
                )

        # 只清理本次分析之前的未确认结果，绝不删除已确认资料。
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

                # 如果新分析结果与已确认人物的正式名称或明确别名重合，
                # 保留已确认记录，不另外制造重复人物。
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
                    "provider": config.provider,
                    "model": config.model,
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
            "provider": config.provider,
            "model": config.model,
            "chunk_count": len(chunks),
            "job_id": job.id,
            "completed_chunks": job.completed_chunks,
            "total_chunks": job.total_chunks,
            "total": len(created),
            "message": (
                f"AI深度分析完成。使用{config.display_name}，"
                f"共分析{len(chunks)}个文本片段，"
                f"提取{len(created)}条待确认信息。"
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

    except Exception:
        db.rollback()
        raise