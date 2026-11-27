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
REQUIRED_VACE_WORKFLOW_NODES = {
    "model_loader": "WanVideoModelLoader",
    "vace_model_selector": "WanVideoVACEModelSelect",
    "vace_encoder": "WanVideoVACEEncode",
    "start_end_frame": "WanVideoVACEStartToEndFrame",
    "sampler": "WanVideoSampler",
    "decoder": "WanVideoDecode",
    "vae_loader": "WanVideoVAELoader",
    "text_encoder": "LoadWanVideoT5TextEncoder",
    "video_output": "VHS_VideoCombine",
}


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


def _node_input_schema(node_info):
    """Expose bounded ComfyUI input names and dropdown choices for workflow preflight."""
    inputs = node_info.get("input") if isinstance(node_info, dict) else None
    if not isinstance(inputs, dict):
        return {"required": {}, "optional": {}}
    result = {"required": {}, "optional": {}}
    for section in ("required", "optional"):
        fields = inputs.get(section)
        if not isinstance(fields, dict):
            continue
        for name, spec in list(fields.items())[:120]:
            entry = {}
            if isinstance(spec, list) and spec:
                if isinstance(spec[0], list):
                    entry["type"] = "COMBO"
                    entry["choices"] = [str(value)[:240] for value in spec[0][:300] if isinstance(value, (str, int, float))]
                elif isinstance(spec[0], str):
                    entry["type"] = spec[0][:80]
                if len(spec) > 1 and isinstance(spec[1], dict):
                    options = spec[1]
                    entry["default"] = options.get("default")
                    entry["min"] = options.get("min")
                    entry["max"] = options.get("max")
            result[section][str(name)[:120]] = entry
    return result


def validate_vace_node_input_requirements(object_info, requirements):
    """Check workflow input names and requested dropdown values against live ComfyUI metadata.

    requirements maps node class names to input names; a string value means the
    input must exist, while a mapping may include an expected dropdown choice.
    """
    missing_nodes = []
    missing_inputs = []
    unavailable_choices = []
    for node_name, fields in requirements.items():
        node_info = object_info.get(node_name) if isinstance(object_info, dict) else None
        if not isinstance(node_info, dict):
            missing_nodes.append(node_name)
            continue
        schema = _node_input_schema(node_info)
        available = {
            name: details
            for section in ("required", "optional")
            for name, details in schema[section].items()
        }
        for input_name, expectation in fields.items():
            if input_name not in available:
                missing_inputs.append({"node": node_name, "input": input_name})
                continue
            expected_choice = expectation.get("choice") if isinstance(expectation, dict) else None
            if expected_choice is not None:
                choices = available[input_name].get("choices", [])
                if expected_choice not in choices:
                    unavailable_choices.append({
                        "node": node_name,
                        "input": input_name,
                        "choice": expected_choice,
                        "available_choices": choices,
                    })
    return {
        "valid": not (missing_nodes or missing_inputs or unavailable_choices),
        "missing_nodes": missing_nodes,
        "missing_inputs": missing_inputs,
        "unavailable_choices": unavailable_choices,
    }


def assess_wan_vace_nodes(object_info):
    if not isinstance(object_info, dict) or object_info.get("_error"):
        return {
            "status": "unavailable",
            "node_count": 0,
            "nodes": [],
            "models": [],
            "vace_node_found": False,
            "wan_video_nodes_found": False,
            "workflow_node_set_complete": False,
            "required_nodes": {},
            "missing_required_nodes": list(REQUIRED_VACE_WORKFLOW_NODES.values()),
            "generation_ready": False,
            "reason": str(object_info.get("_error", "ComfyUI 未运行")) if isinstance(object_info, dict) else "ComfyUI 未运行",
        }
    names = sorted(
        name for name in object_info
        if isinstance(name, str) and ("vace" in name.lower() or "wanvideo" in name.lower() or "wan_video" in name.lower())
    )
    details = []
    models = set()
    model_inventory = []
    for name in names:
        info = object_info.get(name)
        choices = _node_choices(info)
        models.update(choices)
        lower_name = name.lower()
        if "vace" in lower_name:
            category = "vace_model"
        elif "vae" in lower_name:
            category = "video_vae"
        elif "textencoder" in lower_name or "text_encoder" in lower_name:
            category = "video_text_encoder"
        elif "model" in lower_name or "wanvideo" in lower_name or "wan_video" in lower_name:
            category = "video_model"
        else:
            category = "video_auxiliary"
        model_inventory.extend({
            "name": choice,
            "category": category,
            "source": "ComfyUI",
            "selector_node": name,
            "status": "listed",
            "load_tested": False,
        } for choice in choices)
        details.append({
            "node_type": name,
            "model_choices": choices,
            "input_schema": _node_input_schema(info),
        })
    vace_found = any("vace" in name.lower() for name in names)
    wan_found = any("wanvideo" in name.lower() or "wan_video" in name.lower() for name in names)
    present = {
        key: node_name in object_info
        for key, node_name in REQUIRED_VACE_WORKFLOW_NODES.items()
    }
    missing = [
        REQUIRED_VACE_WORKFLOW_NODES[key]
        for key, found in present.items() if not found
    ]
    # Even a complete node set is not enough: required model weights, memory,
    # input images and an actual successful prompt execution must also be verified.
    return {
        "status": "detected" if names else "missing",
        "node_count": len(names),
        "nodes": details,
        "models": sorted(models),
        "model_inventory": sorted(model_inventory, key=lambda item: (item["category"], item["name"].lower())),
        "model_inventory_count": len(model_inventory),
        "vace_node_found": vace_found,
        "wan_video_nodes_found": wan_found,
        "workflow_node_set_complete": not missing,
        "required_nodes": present,
        "missing_required_nodes": missing,
        "generation_ready": False,
        "workflow_execution_verified": False,
        "reason": (
            "完整工作流所需节点均已发现；仍需核对模型权重、节点依赖和输入素材，并完成实际生成验收。"
            if not missing else
            "缺少工作流节点：" + "、".join(missing) if names else
            "未发现 Wan/VACE 节点；不会自动下载模型或安装自定义节点。"
        ),
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
