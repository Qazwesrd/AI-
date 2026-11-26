"""Read-only discovery for an already installed local Wan/VACE ComfyUI stack.

This endpoint never downloads models, installs nodes, submits workflows, or uses cloud services.
A positive capability scan is not a generation test; actual workflow execution is reported separately.
"""
import json
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from fastapi import APIRouter

router = APIRouter(prefix="/api/local-ai", tags=["本地视频生成"])
COMFYUI_BASE_URL = "http://127.0.0.1:8188"
_OPENER = build_opener(ProxyHandler({}))
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


def _local_object_info(opener=None, timeout=4.0):
    request = Request(COMFYUI_BASE_URL + "/object_info", headers={"Accept": "application/json"})
    client = opener or _OPENER
    try:
        with client.open(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            return {"_error": "ComfyUI 节点清单超过安全读取上限"}
        payload = json.loads(raw.decode("utf-8"))
        return payload if isinstance(payload, dict) else {"_error": "ComfyUI 节点清单格式无效"}
    except HTTPError as exc:
        return {"_error": f"ComfyUI HTTP {exc.code}"}
    except (URLError, TimeoutError, OSError):
        return None
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"_error": "ComfyUI 返回了无法识别的节点清单"}


def _node_choices(node_info):
    choices = []
    inputs = node_info.get("input") if isinstance(node_info, dict) else None
    if not isinstance(inputs, dict):
        return choices
    for section in ("required", "optional"):
        fields = inputs.get(section)
        if not isinstance(fields, dict):
            continue
        for name, spec in fields.items():
            if isinstance(spec, list) and spec and isinstance(spec[0], list):
                values = [str(item)[:240] for item in spec[0][:300] if isinstance(item, str)]
                if values and any(token in name.lower() for token in ("model", "checkpoint", "vae")):
                    choices.extend(values)
    return sorted(set(choices))


def assess_wan_vace_nodes(object_info):
    if not isinstance(object_info, dict) or object_info.get("_error"):
        return {
            "status": "unavailable",
            "node_count": 0,
            "nodes": [],
            "models": [],
            "vace_node_found": False,
            "wan_video_nodes_found": False,
            "generation_ready": False,
            "reason": str(object_info.get("_error", "ComfyUI 未运行")) if isinstance(object_info, dict) else "ComfyUI 未运行",
        }
    names = sorted(
        name for name in object_info
        if isinstance(name, str) and ("vace" in name.lower() or "wanvideo" in name.lower() or "wan_video" in name.lower())
    )
    details = []
    models = set()
    for name in names:
        info = object_info.get(name)
        choices = _node_choices(info)
        models.update(choices)
        details.append({"node_type": name, "model_choices": choices})
    vace_found = any("vace" in name.lower() for name in names)
    wan_found = any("wanvideo" in name.lower() or "wan_video" in name.lower() for name in names)
    # Deliberately do not claim a complete workflow from node names alone.
    return {
        "status": "detected" if names else "missing",
        "node_count": len(names),
        "nodes": details,
        "models": sorted(models),
        "vace_node_found": vace_found,
        "wan_video_nodes_found": wan_found,
        "generation_ready": False,
        "workflow_execution_verified": False,
        "reason": "已发现部分 Wan/VACE 节点；仍需匹配模型文件、节点依赖和完整工作流并完成实际生成验收。" if names else "未发现 Wan/VACE 节点；不会自动下载模型或安装自定义节点。",
    }


@router.get("/video-runtime")
def local_video_runtime():
    """Inspect local ComfyUI Wan/VACE nodes without submitting a generation task."""
    info = _local_object_info()
    result = assess_wan_vace_nodes(info)
    return {
        "engine": "ComfyUI",
        "base_url": COMFYUI_BASE_URL,
        **result,
        "local_only": True,
        "network_used": False,
        "downloaded": False,
        "generation_submitted": False,
        "cloud_fallback": False,
        "message": (
            "本机视频能力检测完成；检测结果不代表已能生成视频。"
            if info is not None and not info.get("_error")
            else "未能读取本机 ComfyUI 节点清单；请确认本地引擎已启动。"
        ),
    }
