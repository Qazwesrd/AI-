"""Explicit, loopback-only smoke tests for an already installed Ollama runtime.

No model pulls, cloud fallback, arbitrary endpoints, or automatic process startup.
"""
import json
import re
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Literal

router = APIRouter(prefix="/api/local-ai", tags=["本地 AI 引擎"])
OLLAMA_BASE_URL = "http://127.0.0.1:11434"
MODEL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
TEST_PROMPT = "请用简体中文写一句话，说明你已成功在本机运行。"
OPENER = build_opener(ProxyHandler({}))


class LocalModelTestInput(BaseModel):
    model: str = Field(min_length=1, max_length=128)


class LocalCreativeInput(BaseModel):
    model: str = Field(min_length=1, max_length=128)
    task: Literal["screenplay", "character", "plot", "storyboard", "image_prompt"] = "screenplay"
    style: Literal["国漫", "仿真人", "3D漫", "2D漫"] = "国漫"
    source_text: str = Field(min_length=1, max_length=12000)
    extra_requirements: str = Field(default="", max_length=3000)


CREATIVE_TASKS = {
    "screenplay": "将素材改编为可拍摄的短剧剧本。输出场次、地点、时间、人物、动作、对白和必要的转场，不擅自改变核心剧情。",
    "character": "整理人物设定。输出姓名、身份、性格、外貌、服装、能力、人物关系、当前剧情阶段状态，区分原文事实与合理推测。",
    "plot": "分析剧情。输出事件顺序、因果关系、冲突、伏笔、人物动机和待确认信息，不凭空补写原文没有的事实。",
    "storyboard": "生成可执行分镜。逐镜输出镜号、景别、机位/运镜、时长、画面内容、人物动作、对白/音效和连续性要求。",
    "image_prompt": "为每个主要镜头生成独立图片提示词。明确主体、动作、场景、构图、光线、镜头语言和风格；保持角色外貌、服装、道具与场景连续一致。",
}


def _ollama_request(path: str, payload: dict | None = None, timeout: float = 2.0):
    """Call a fixed localhost Ollama endpoint without honoring external proxies."""
    if path not in {"/api/tags", "/api/generate"}:
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
        raise HTTPException(502, f"本地 Ollama 返回错误：HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError):
        raise HTTPException(503, "未连接到本机 Ollama。请先自行启动 Ollama，再刷新检测。")
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HTTPException(502, "本地 Ollama 返回了无法识别的数据")


def _installed_models():
    payload = _ollama_request("/api/tags")
    models = payload.get("models", []) if isinstance(payload, dict) else []
    result = []
    for item in models:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("model")
        if isinstance(name, str) and MODEL_NAME_PATTERN.fullmatch(name):
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
