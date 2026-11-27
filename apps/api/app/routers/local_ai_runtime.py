"""Explicit, loopback-only smoke tests for an already installed Ollama runtime.

No model pulls, cloud fallback, arbitrary endpoints, or automatic process startup.
"""
import json
import os
import re
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator
from typing import Literal

router = APIRouter(prefix="/api/local-ai", tags=["本地 AI 引擎"])
OLLAMA_BASE_URL = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11435").rstrip("/")
MODEL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
TEST_PROMPT = "请用简体中文写一句话，说明你已成功在本机运行。"
OPENER = build_opener(ProxyHandler({}))


def _resource_bundle_status(data_directory: str | None = None):
    """Inspect the separately installed AI resource bundle without changing it."""
    configured = (data_directory or os.environ.get("AICOMICS_DATA_DIR") or os.environ.get("APP_DATA_DIR") or "").strip()
    if not configured:
        return {
            "status": "not_configured", "installed": False, "data_directory": None,
            "manifest_present": False, "manifest_valid": False, "components": {},
            "message": "尚未配置 AI 资源包数据目录。请先单独安装 AI 离线资源包，再执行检测。",
            "local_only": True, "downloaded": False, "cloud_fallback": False,
            "modified": False,
        }
    root = Path(configured).expanduser()
    manifest_path = root / "resource-manifest.json"
    manifest_present = manifest_path.is_file()
    manifest_valid = False
    manifest_error = None
    if manifest_present:
        try:
            if manifest_path.stat().st_size > 2 * 1024 * 1024:
                raise ValueError("资源清单超过 2 MiB")
            manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest_valid = (
                isinstance(manifest_data, dict)
                and manifest_data.get("schema_version") == 1
                and manifest_data.get("product") == "AI漫剧制作大师"
                and isinstance(manifest_data.get("resource_bundle_version"), str)
                and bool(manifest_data.get("resource_bundle_version").strip())
                and isinstance(manifest_data.get("engines"), list)
                and len(manifest_data.get("engines")) >= 3
            )
            if not manifest_valid:
                manifest_error = "资源清单版本、产品标识、资源包版本或引擎目录结构不符合要求"
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            manifest_error = str(exc)[:300]

    engines = root / "engines"
    def file_exists(relative):
        candidate = engines / relative
        try:
            return candidate.is_file()
        except OSError:
            return False
    def directory_exists(relative):
        candidate = engines / relative
        try:
            return candidate.is_dir()
        except OSError:
            return False

    comfy_layouts = [
        ("ComfyUI/python_embeded/python.exe", "ComfyUI/ComfyUI/main.py"),
        ("ComfyUI/venv/Scripts/python.exe", "ComfyUI/main.py"),
    ]
    comfy_ready = any(file_exists(python) and file_exists(main) for python, main in comfy_layouts)
    video_cli_found = False
    stable_root = engines / "StableDiffusionCpp"
    if stable_root.is_dir():
        for current, dirs, files in os.walk(stable_root):
            dirs[:] = [name for name in dirs if not (Path(current) / name).is_symlink()]
            if "sd-cli.exe" in files:
                video_cli_found = True
                break
    components = {
        "ollama_runtime": file_exists("Ollama/runtime/ollama.exe"),
        "ollama_model_store": directory_exists("Ollama/models/blobs"),
        "comfyui_runtime": comfy_ready,
        "ffmpeg": file_exists("FFmpeg/ffmpeg.exe"),
        "ffprobe": file_exists("FFmpeg/ffprobe.exe"),
        "stable_diffusion_cpp": video_cli_found,
        "vace_model": file_exists("VACE/models/wan2.1-v3-vace-1.3b-q4_0.gguf"),
        "vace_text_encoder": file_exists("VACE/models/umt5-xxl-encoder-q4_k_m.gguf"),
        "vace_vae": file_exists("VACE/models/pig_wan_vae_fp32-f16.gguf"),
    }
    missing = [name for name, present in components.items() if not present]
    installed = manifest_valid and not missing
    if installed:
        status = "installed"
        message = "已识别独立 AI 资源包；必要引擎和资源目录均存在。模型能否实际加载仍需单独测试。"
    elif not manifest_present:
        status = "not_installed"
        message = "未找到 AI 资源包安装清单。桌面程序与 AI 资源包是分开安装的；请使用独立资源包安装脚本。"
    elif not manifest_valid:
        status = "manifest_invalid"
        message = "发现资源清单但无法读取或格式无效；没有修改任何文件。"
    else:
        status = "incomplete"
        message = "发现资源包清单，但部分组件缺失；不会自动下载或覆盖，请查看缺失项目。"
    return {
        "status": status, "installed": installed, "data_directory": str(root),
        "manifest_present": manifest_present, "manifest_valid": manifest_valid,
        "manifest_error": manifest_error, "components": components, "missing_components": missing,
        "message": message, "local_only": True, "downloaded": False,
        "cloud_fallback": False, "modified": False,
    }


@router.get("/resource-bundle/status")
def local_resource_bundle_status():
    """Report whether the separately installed resource bundle is present."""
    return _resource_bundle_status()



class LocalChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=12000)


class LocalChatInput(BaseModel):
    model: str = Field(min_length=1, max_length=128)
    messages: list[LocalChatMessage] = Field(min_length=1, max_length=20)


class LocalModelTestInput(BaseModel):
    model: str = Field(min_length=1, max_length=128)


class LocalCreativeInput(BaseModel):
    model: str = Field(min_length=1, max_length=128)
    task: Literal["screenplay", "character", "plot", "storyboard", "image_prompt"] = "screenplay"
    style: Literal["国漫", "仿真人", "3D漫", "2D漫"] = "国漫"
    source_text: str = Field(min_length=1, max_length=12000)
    extra_requirements: str = Field(default="", max_length=3000)


class LocalEpisodePackageInput(BaseModel):
    model: str = Field(min_length=1, max_length=128)
    style: Literal["国漫", "仿真人", "3D漫", "2D漫"] = "国漫"
    source_text: str = Field(min_length=1, max_length=12000)
    extra_requirements: str = Field(default="", max_length=3000)




class LocalSpecializedModelInput(BaseModel):
    base_model: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=64)
    role: Literal["screenwriter", "storyboard_director", "character_designer", "prompt_artist", "all_rounder"] = "all_rounder"
    style: Literal["国漫", "仿真人", "3D漫", "2D漫"] = "国漫"
    instructions: str = Field(default="", max_length=4000)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value):
        normalized = value.strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,62}", normalized):
            raise ValueError("模型名称只能使用 2-63 位小写英文字母、数字、点、下划线或连字符")
        return normalized


CREATIVE_TASKS = {
    "screenplay": "将素材改编为可拍摄的短剧剧本。输出场次、地点、时间、人物、动作、对白和必要的转场，不擅自改变核心剧情。",
    "character": "整理人物设定。输出姓名、身份、性格、外貌、服装、能力、人物关系、当前剧情阶段状态，区分原文事实与合理推测。",
    "plot": "分析剧情。输出事件顺序、因果关系、冲突、伏笔、人物动机和待确认信息，不凭空补写原文没有的事实。",
    "storyboard": "生成可执行分镜。逐镜输出镜号、景别、机位/运镜、时长、画面内容、人物动作、对白/音效和连续性要求。",
    "image_prompt": "为每个主要镜头生成独立图片提示词。明确主体、动作、场景、构图、光线、镜头语言和风格；保持角色外貌、服装、道具与场景连续一致。",
}


def _ollama_request(path: str, payload: dict | None = None, timeout: float = 2.0):
    """Call a fixed localhost Ollama endpoint without honoring external proxies."""
    if path not in {"/api/tags", "/api/generate", "/api/show", "/api/create", "/api/chat"}:
        raise ValueError("Unsupported local Ollama endpoint")
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(
        OLLAMA_BASE_URL + path,
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
        method="POST" if data is not None else "GET",
    )
    try:
        with OPENER.open(request, timeout=timeout) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise HTTPException(502, "本地引擎返回数据过大，已停止读取")
        return json.loads(raw.decode("utf-8"))
    except HTTPError as exc:
        # Preserve Ollama's actionable validation message instead of hiding it
        # behind a generic HTTP status. The response is bounded and never logs
        # prompts, model files, or environment secrets.
        try:
            raw_detail = exc.read(4096).decode("utf-8", errors="replace").strip()
        except Exception:
            raw_detail = ""
        try:
            parsed_detail = json.loads(raw_detail) if raw_detail else None
        except json.JSONDecodeError:
            parsed_detail = None
        if isinstance(parsed_detail, dict):
            detail = parsed_detail.get("error") or parsed_detail.get("message")
        else:
            detail = raw_detail
        safe_detail = str(detail or "未提供具体原因").replace("\r", " ").replace("\n", " ")[:500]
        raise HTTPException(502, f"本地 Ollama 拒绝了请求（HTTP {exc.code}）：{safe_detail}") from exc
    except (URLError, TimeoutError, OSError):
        raise HTTPException(503, "未连接到本机 Ollama。请先自行启动 Ollama，再刷新检测。")
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HTTPException(502, "本地 Ollama 返回了无法识别的数据")


def _is_cloud_model_name(name: str) -> bool:
    """Reject Ollama-hosted model aliases so local workflows cannot incur cloud usage."""
    normalized = name.strip().lower()
    return normalized.endswith("-cloud") or normalized.endswith(":cloud")


def _installed_models():
    payload = _ollama_request("/api/tags")
    models = payload.get("models", []) if isinstance(payload, dict) else []
    result = []
    for item in models:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("model")
        if isinstance(name, str) and MODEL_NAME_PATTERN.fullmatch(name) and not _is_cloud_model_name(name):
            result.append({
                "name": name,
                "size_bytes": item.get("size") if isinstance(item.get("size"), int) else None,
                "modified_at": item.get("modified_at") if isinstance(item.get("modified_at"), str) else None,
            })
    return result


@router.get("/runtime")
def local_ai_runtime():
    """Probe only the local Ollama service and list models already installed."""
    try:
        models = _installed_models()
    except HTTPException as exc:
        if exc.status_code == 503:
            return {
                "engine": "ollama",
                "status": "not_running",
                "base_url": OLLAMA_BASE_URL,
                "models": [],
                "network_used": False,
                "downloaded": False,
                "cloud_fallback": False,
                "message": exc.detail,
            }
        raise
    return {
        "engine": "ollama",
        "status": "ready",
        "base_url": OLLAMA_BASE_URL,
        "models": models,
        "model_count": len(models),
        "network_used": False,
        "downloaded": False,
        "cloud_fallback": False,
        "message": "已连接本机 Ollama；只列出本机已安装模型，不会自动下载。",
    }


@router.post("/chat")
def chat_with_local_model(payload: LocalChatInput):
    """Multi-turn chat with a preinstalled local model only; no cloud aliases or fallback."""
    model = payload.model.strip()
    if not MODEL_NAME_PATTERN.fullmatch(model) or _is_cloud_model_name(model):
        raise HTTPException(400, "仅允许使用本机模型，不能选择 Ollama Cloud 模型。")
    messages = [{"role": item.role, "content": item.content.strip()} for item in payload.messages]
    if not messages[-1]["content"] or messages[-1]["role"] != "user":
        raise HTTPException(422, "最后一条消息必须是非空的用户消息。")
    # Reject oversized requests before probing Ollama; validation must work
    # offline and must not turn a client-side limit into a misleading 503.
    if sum(len(item["content"]) for item in messages) > 24000:
        raise HTTPException(413, "当前对话上下文过长，请新建对话或删减较早消息。")
    installed = {item["name"] for item in _installed_models()}
    if model not in installed:
        raise HTTPException(404, "所选模型未安装在本机；聊天不会自动下载或切换云端模型。")
    system = (
        "你是 AI 漫剧制作大师的本地中文助手。使用简体中文，回答清楚、具体、可执行。"
        "帮助用户进行小说分析、人物设定、剧本、分镜和提示词创作。"
        "区分原文事实、推断和待确认事项；不要谎称已保存文件或生成图片、视频。"
        "你只能回答和规划，不得声称已经执行未实际完成的操作。"
    )
    result = _ollama_request(
        "/api/chat",
        {
            "model": model,
            "messages": [{"role": "system", "content": system}, *messages],
            "stream": False,
            "options": {"num_predict": 1200, "temperature": 0.4, "num_ctx": 8192},
        },
        timeout=180.0,
    )
    message = result.get("message") if isinstance(result, dict) else None
    response = message.get("content") if isinstance(message, dict) else None
    if not isinstance(response, str) or not response.strip():
        raise HTTPException(502, "本机模型没有返回有效回答，请检查模型状态和电脑资源。")
    return {
        "status": "success",
        "engine": "ollama",
        "model": model,
        "response": response.strip()[:16000],
        "local_only": True,
        "network_used": False,
        "cloud_fallback": False,
        "downloaded": False,
        "saved_automatically": False,
    }


@router.post("/test")
def test_local_model(payload: LocalModelTestInput):
    """Run a short, explicit smoke test against a model already installed locally."""
    model = payload.model.strip()
    if not MODEL_NAME_PATTERN.fullmatch(model):
        raise HTTPException(400, "模型名称格式无效")
    installed = {item["name"] for item in _installed_models()}
    if model not in installed:
        raise HTTPException(404, "所选模型未安装在本机；为避免下载和费用，本程序不会自动拉取模型。")
    result = _ollama_request(
        "/api/generate",
        {
            "model": model,
            "prompt": TEST_PROMPT,
            "stream": False,
            "options": {"num_predict": 48},
        },
        timeout=45.0,
    )
    response = result.get("response") if isinstance(result, dict) else None
    if not isinstance(response, str) or not response.strip():
        raise HTTPException(502, "本地模型没有返回有效文本，请检查模型和电脑资源。")
    return {
        "status": "success",
        "engine": "ollama",
        "model": model,
        "response": response.strip()[:4000],
        "network_used": False,
        "downloaded": False,
        "cloud_fallback": False,
        "message": "本地模型测试成功。结果由本机模型生成。",
    }


@router.post("/generate")
def generate_local_creative_content(payload: LocalCreativeInput):
    """Generate creative material using only an already-installed local Ollama model."""
    model = payload.model.strip()
    if not MODEL_NAME_PATTERN.fullmatch(model):
        raise HTTPException(400, "模型名称格式无效")
    installed = {item["name"] for item in _installed_models()}
    if model not in installed:
        raise HTTPException(404, "所选模型未安装在本机；不会自动下载模型。")
    style = payload.style
    system = (
        "你是中文 AI 漫剧制作助手。必须使用简体中文，结构清晰、可直接编辑。"
        "不得声称已保存文件或完成图片/视频生成。"
        "画面风格要求：" + style + "。"
        "任务要求：" + CREATIVE_TASKS[payload.task]
    )
    prompt = (
        "请根据以下用户提供的素材完成任务。若信息不足，请明确标注“待确认”，不要编造为既定事实。\n\n"
        "【素材】\n" + payload.source_text.strip() +
        ("\n\n【额外要求】\n" + payload.extra_requirements.strip() if payload.extra_requirements.strip() else "")
    )
    result = _ollama_request(
        "/api/generate",
        {
            "model": model,
            "system": system,
            "prompt": prompt,
            "stream": False,
            "options": {"num_predict": 2048, "temperature": 0.4},
        },
        timeout=180.0,
    )
    response = result.get("response") if isinstance(result, dict) else None
    if not isinstance(response, str) or not response.strip():
        raise HTTPException(502, "本地模型没有返回有效内容，请检查模型能力和电脑资源。")
    return {
        "status": "success",
        "engine": "ollama",
        "model": model,
        "task": payload.task,
        "style": style,
        "response": response.strip()[:20000],
        "network_used": False,
        "downloaded": False,
        "cloud_fallback": False,
        "saved_automatically": False,
        "message": "本地生成完成。内容尚未自动保存，请检查后手动复制到对应创作模块。",
    }



SPECIALIST_ROLES = {
    "screenwriter": "你是专业中文漫剧编剧。负责忠于原著地改编剧情，输出场次、动作、对白、冲突和节奏。",
    "storyboard_director": "你是专业分镜导演。逐镜输出镜号、时长、景别、机位、运镜、构图、动作、对白、音效和转场。",
    "character_designer": "你是角色设定设计师。建立角色外貌、发型、服装、配色、体型、标志物和状态连续性档案。",
    "prompt_artist": "你是视觉提示词设计师。输出可执行的图像提示词，明确主体、动作、场景、构图、光影和风格。",
    "all_rounder": "你是全流程中文 AI 漫剧制作助手，擅长剧本、人物设定、剧情、分镜和图片提示词。",
}


@router.post("/models/create")
def create_local_specialized_model(payload: LocalSpecializedModelInput):
    """Create a derived Ollama model from an already-installed local base model."""
    base_model = payload.base_model.strip()
    if not MODEL_NAME_PATTERN.fullmatch(base_model):
        raise HTTPException(400, "基础模型名称格式无效")
    installed = {item["name"] for item in _installed_models()}
    if base_model not in installed:
        raise HTTPException(404, "基础模型尚未安装；本功能不会自动下载模型。")
    if payload.name in installed:
        raise HTTPException(409, "这个模型名称已经存在，请换一个名称，避免覆盖现有模型。")
    system_prompt = (
        "你是 AI 漫剧制作大师的本地专用模型。必须使用简体中文，输出结构清晰、可编辑。"
        "严格区分原文事实、合理推断和待确认内容，不得编造原著事实。"
        "角色外貌、发型、服装、配色、伤势、道具和场景必须跨镜头保持一致。"
        "不能声称已生成图片、视频或保存文件。"
        f"\n工作身份：{SPECIALIST_ROLES[payload.role]}"
        f"\n默认视觉风格：{payload.style}。"
        + (f"\n用户补充规则：{payload.instructions.strip()}" if payload.instructions.strip() else "")
    )
    # Ollama's current /api/create contract accepts the base model via
    # "from" and the system prompt/parameters as separate fields. Sending only
    # a legacy Modelfile string can be rejected by newer Ollama versions with
    # "neither from or files was specified".
    result = _ollama_request(
        "/api/create",
        {
            "model": payload.name,
            "from": base_model,
            "system": system_prompt,
            "parameters": {"temperature": 0.35, "num_ctx": 8192},
            "stream": False,
        },
        timeout=600.0,
    )
    if not isinstance(result, dict) or result.get("status") != "success":
        raise HTTPException(502, "Ollama 未确认专用模型创建成功；请刷新模型列表核实。")
    return {
        "status": "success",
        "model": payload.name,
        "base_model": base_model,
        "role": payload.role,
        "style": payload.style,
        "network_used": False,
        "downloaded": False,
        "cloud_fallback": False,
        "overwritten": False,
        "message": "本地专用模型创建成功。它复用已安装基础模型，不会下载基础模型或调用云端服务。",
    }

def _bounded_continuity(items, max_chars: int = 8000):
    """Keep confirmed continuity context bounded before it reaches a local model."""
    continuity = []
    used = 0
    truncated = False
    for item in items:
        data = item.data if isinstance(item.data, dict) else {}
        if data.get("source_stale"):
            continue
        entry = {"category": item.category, "name": item.name, "description": item.description, "data": data}
        encoded = json.dumps(entry, ensure_ascii=False, default=str)
        if used + len(encoded) > max_chars:
            truncated = True
            break
        continuity.append(entry)
        used += len(encoded)
    return continuity, truncated


@router.get("/novel-context/{novel_id}/{chapter_id}")
def get_local_novel_context(novel_id: int, chapter_id: int):
    """Read a bounded chapter and confirmed continuity notes for local creative workflows."""
    from app.database import SessionLocal
    from app.models.novel import AnalysisItem, Chapter, Novel

    db = SessionLocal()
    try:
        novel = db.query(Novel).filter(Novel.id == novel_id).first()
        if not novel:
            raise HTTPException(404, "小说不存在")
        chapter = db.query(Chapter).filter(
            Chapter.id == chapter_id, Chapter.novel_id == novel_id
        ).first()
        if not chapter:
            raise HTTPException(404, "章节不存在或不属于当前小说")
        confirmed = db.query(AnalysisItem).filter(
            AnalysisItem.novel_id == novel_id, AnalysisItem.confirmed.is_(True)
        ).order_by(AnalysisItem.category, AnalysisItem.name).limit(300).all()
        continuity, continuity_truncated = _bounded_continuity(confirmed)
        original_content = chapter.content or ""
        content = original_content[:12000]
        return {
            "novel_id": novel.id, "novel_title": novel.title,
            "chapter_id": chapter.id, "chapter_number": chapter.chapter_number,
            "chapter_title": chapter.title, "content": content,
            "word_count": chapter.word_count,
            "confirmed_continuity": continuity, "continuity_count": len(continuity),
            "source_text_truncated": len(original_content) > len(content),
            "continuity_truncated": continuity_truncated,
            "network_used": False, "cloud_fallback": False,
            "message": "已读取章节正文及未过期的已确认连续性资料；超长内容会限制长度。",
        }
    finally:
        db.close()


@router.post("/generate-package")
def generate_local_episode_package(payload: LocalEpisodePackageInput):
    """Generate a five-stage episode package with an already-installed local model."""
    model = payload.model.strip()
    if not MODEL_NAME_PATTERN.fullmatch(model):
        raise HTTPException(400, "模型名称格式无效")
    installed = {item["name"] for item in _installed_models()}
    if model not in installed:
        raise HTTPException(404, "所选模型未安装在本机；不会自动下载模型。")
    stages = [
        ("screenplay", "第一步：剧本", CREATIVE_TASKS["screenplay"]),
        ("character", "第二步：人物设定与连续性清单", CREATIVE_TASKS["character"] + " 输出角色外貌、发型、服装、配色、能力、伤势和随身道具固定设定表。"),
        ("plot", "第三步：剧情结构与场景道具清单", CREATIVE_TASKS["plot"] + " 列出场景、时间、道具及状态变化。"),
        ("storyboard", "第四步：逐镜分镜", CREATIVE_TASKS["storyboard"] + " 每镜引用人物固定设定，保持服装、发型、道具和场景空间连续。"),
        ("image_prompt", "第五步：逐镜图片提示词", CREATIVE_TASKS["image_prompt"] + " 每条提示词带有角色固定外观、服装、场景和道具连续性描述，并注明对应镜号。"),
    ]
    outputs = []
    previous = ""
    for key, label, instruction in stages:
        system = ("你是专业中文漫剧编剧、导演和分镜师。使用简体中文；不得擅自改变核心剧情；不足信息标注待确认。"
                  "画面风格：" + payload.style + "。保持人物身份、外貌、发型、服装、配色、场景和道具连续。任务要求：" + instruction)
        prompt = "【原始章节素材】\n" + payload.source_text.strip()
        if payload.extra_requirements.strip():
            prompt += "\n\n【用户额外要求】\n" + payload.extra_requirements.strip()
        if previous:
            prompt += "\n\n【前序阶段成果，必须继承】\n" + previous[-9000:]
        prompt += "\n\n请完成" + label + "，输出结构化、可编辑内容。"
        try:
            result = _ollama_request("/api/generate", {
                "model": model, "system": system, "prompt": prompt, "stream": False,
                "options": {"num_predict": 1400, "temperature": 0.35},
            }, timeout=240.0)
        except HTTPException as exc:
            raise HTTPException(exc.status_code, f"{label}生成失败；已完成{len(outputs)}步，未切换云端。{exc.detail}") from exc
        response = result.get("response") if isinstance(result, dict) else None
        if not isinstance(response, str) or not response.strip():
            raise HTTPException(502, f"{label}没有返回有效内容；已完成{len(outputs)}步，未切换云端。")
        content = response.strip()[:16000]
        outputs.append({"stage": key, "title": label, "content": content})
        previous += "\n\n" + label + "\n" + content
    return {
        "status": "success", "engine": "ollama", "model": model, "style": payload.style,
        "stages": outputs, "stage_count": len(outputs), "network_used": False,
        "downloaded": False, "cloud_fallback": False, "saved_automatically": False,
        "message": "整章创作包已在本机生成。请检查后手动复制保存。",
    }
