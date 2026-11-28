"""Read-only local WanVideo/VACE runtime inspection and workflow preflight.

This module only queries the already-running ComfyUI loopback API. It never
installs models, starts processes, submits workflows, or uses cloud fallback.
"""
import json
import re
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from fastapi import APIRouter

router = APIRouter(prefix="/api/local-ai/video-runtime", tags=["本地视频运行时"])
COMFYUI_BASE_URL = "http://127.0.0.1:8188"
_OPENER = build_opener(ProxyHandler({}))
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_WORKFLOW_NODES = 300
MODEL_FOLDERS = {
    "diffusion_models": "video_model",
    "vae": "video_vae",
    "text_encoders": "video_text_encoder",
    "loras": "video_lora",
}
REQUIRED_VACE_WORKFLOW_NODES = {
    "model_loader": "WanVideoModelLoader",
    "vace_encode": "WanVideoVACEEncode",
    "sampler": "WanVideoSampler",
    "decode": "WanVideoDecode",
}
VIDEO_OUTPUT_NODES = {"VHS_VideoCombine", "SaveAnimatedWEBP", "SaveAnimatedPNG", "SaveVideo"}
WAN_NODE_PREFIXES = ("WanVideo",)
VACE_NODE_MARKERS = ("VACE",)


def _local_json(path: str, opener=None, timeout: float = 4.0):
    """GET a small, allow-listed JSON response from the local ComfyUI server."""
    allowed = path in {"/object_info", "/models"}
    allowed = allowed or bool(re.fullmatch(r"/models/[a-zA-Z0-9_-]{1,80}", path))
    if not allowed:
        raise ValueError("Unsupported ComfyUI endpoint")
    request = Request(COMFYUI_BASE_URL + path, headers={"Accept": "application/json"})
    try:
        with (opener or _OPENER).open(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError("本地 ComfyUI 响应超过安全读取上限")
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, (dict, list)) else {"_error": "invalid response"}
    except HTTPError as exc:
        return {"_error": f"HTTP {exc.code}"}
    except (URLError, TimeoutError, OSError):
        return None
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"_error": "invalid JSON"}


def _input_schema(node_info):
    result = {}
    inputs = node_info.get("input") if isinstance(node_info, dict) else None
    if not isinstance(inputs, dict):
        return result
    for section in ("required", "optional"):
        fields = inputs.get(section)
        if not isinstance(fields, dict):
            continue
        for name, spec in fields.items():
            entry = {"required": section == "required"}
            if isinstance(spec, list) and spec:
                first = spec[0]
                if isinstance(first, list):
                    entry["type"] = "COMBO"
                    entry["choices"] = [str(item) for item in first[:500]]
                elif isinstance(first, str):
                    entry["type"] = first
                elif isinstance(first, (int, float, bool)):
                    entry["type"] = type(first).__name__.upper()
                if len(spec) > 1 and isinstance(spec[1], dict):
                    entry["options"] = {
                        str(k): v for k, v in list(spec[1].items())[:30]
                        if isinstance(v, (str, int, float, bool, type(None)))
                    }
            result[str(name)] = entry
    return result


def _model_names(object_info):
    names = []
    if not isinstance(object_info, dict) or object_info.get("_error"):
        return names
    for node_type, node in object_info.items():
        if not isinstance(node, dict):
            continue
        schema = _input_schema(node)
        for input_name, spec in schema.items():
            if spec.get("type") == "COMBO" and input_name.lower() in {
                "model", "model_name", "vae_name", "text_encoder", "clip_name", "filename"
            }:
                names.extend(x for x in spec.get("choices", []) if x.lower().endswith((".safetensors", ".ckpt", ".pt", ".gguf")))
    return list(dict.fromkeys(names))[:1000]


def assess_wan_vace_nodes(object_info):
    if object_info is None:
        return {
            "status": "unavailable", "generation_ready": False,
            "workflow_node_set_complete": False, "missing_required_nodes": list(REQUIRED_VACE_WORKFLOW_NODES.values()),
            "required_nodes": {name: False for name in REQUIRED_VACE_WORKFLOW_NODES.values()},
            "nodes": [], "node_count": 0, "models": [], "model_inventory": [],
            "vace_node_found": False, "wan_video_nodes_found": False,
            "workflow_execution_verified": False,
            "message": "未连接本机 ComfyUI；仅支持本地检测，不会自动启动或切换云端。",
        }
    if not isinstance(object_info, dict):
        object_info = {}
    relevant = []
    for name, info in object_info.items():
        if not isinstance(name, str) or not name.startswith(WAN_NODE_PREFIXES) or not isinstance(info, dict):
            continue
        relevant.append({
            "node_type": name,
            "input_schema": {
                "required": {k: v for k, v in _input_schema(info).items() if v.get("required")},
                "optional": {k: v for k, v in _input_schema(info).items() if not v.get("required")},
            },
        })
    found = {item["node_type"] for item in relevant}
    required = {name: node in found for name, node in REQUIRED_VACE_WORKFLOW_NODES.items()}
    missing = [node for node in REQUIRED_VACE_WORKFLOW_NODES.values() if node not in found]
    vace_found = any(any(marker in name.upper() for marker in VACE_NODE_MARKERS) for name in found)
    models = _model_names(object_info)
    inventory = []
    for model in models:
        low = model.lower()
        category = "video_model" if "wan" in low and ("diffusion" in low or "vace" in low or "t2v" in low or "i2v" in low) else (
            "video_vae" if "vae" in low else "video_text_encoder" if "umt5" in low or "text" in low else "video_model"
        )
        inventory.append({"name": model, "category": category, "load_tested": False})
    complete = not missing
    return {
        "status": "detected" if found else "missing",
        "generation_ready": False,
        "workflow_node_set_complete": complete,
        "missing_required_nodes": missing,
        "required_nodes": required,
        "nodes": relevant,
        "node_count": len(relevant),
        "models": models,
        "model_inventory": inventory,
        "vace_node_found": vace_found,
        "wan_video_nodes_found": bool(found),
        "workflow_execution_verified": False,
        "message": "已检测到部分 WanVideo/VACE 节点；节点存在不代表模型已加载或视频生成已验证。" if found else "未检测到 WanVideo 节点。",
    }


def _scan_video_model_folders():
    root = _local_json("/models")
    if not isinstance(root, (dict, list)) or (isinstance(root, dict) and root.get("_error")):
        return {"available": False, "folders": {}, "items": [], "message": "无法读取本机 ComfyUI 模型目录。"}
    if isinstance(root, dict):
        folder_names = root.get("folders") or root.get("models") or list(root.keys())
    else:
        folder_names = root
    if not isinstance(folder_names, list):
        folder_names = []
    folders = {}
    items = []
    for folder, category in MODEL_FOLDERS.items():
        if folder not in folder_names:
            folders[folder] = {"available": False, "count": 0}
            continue
        listing = _local_json("/models/" + folder)
        names = listing if isinstance(listing, list) else (
            listing.get("models", listing.get("files", [])) if isinstance(listing, dict) else []
        )
        if not isinstance(names, list):
            names = []
        clean = [x[:240] for x in names if isinstance(x, str) and len(x) <= 240][:2000]
        folders[folder] = {"available": True, "count": len(clean)}
        items.extend({"name": name, "category": category, "load_tested": False} for name in clean)
    return {"available": True, "folders": folders, "items": items}


def validate_vace_node_input_requirements(object_info, node_inputs):
    errors = {"missing_nodes": [], "missing_inputs": [], "unavailable_choices": []}
    if not isinstance(node_inputs, dict):
        node_inputs = {}
    for node_name, supplied in node_inputs.items():
        if not isinstance(supplied, dict):
            supplied = {}
        info = object_info.get(node_name) if isinstance(object_info, dict) else None
        if not isinstance(info, dict):
            errors["missing_nodes"].append(node_name)
            continue
        schema = _input_schema(info)
        for input_name, value in supplied.items():
            if input_name not in schema:
                errors["missing_inputs"].append({"node": node_name, "input": input_name})
                continue
            choices = schema[input_name].get("choices")
            if choices:
                choice = value.get("choice") if isinstance(value, dict) else value
                if choice is not None and choice not in choices:
                    errors["unavailable_choices"].append({"node": node_name, "input": input_name, "choice": choice})
    valid = not any(errors.values())
    return {"valid": valid, **errors}


def validate_local_video_workflow(workflow, object_info):
    errors = []
    if not isinstance(workflow, dict) or not workflow:
        return {"valid": False, "errors": ["工作流必须是非空对象。"], "node_count": 0,
                "has_wan_video_nodes": False, "has_video_output_node": False}
    if len(workflow) > MAX_WORKFLOW_NODES:
        return {"valid": False, "errors": [f"工作流节点数量超过安全上限（{MAX_WORKFLOW_NODES}）。"],
                "node_count": len(workflow), "has_wan_video_nodes": False, "has_video_output_node": False}
    node_types = set()
    for node_id, node in workflow.items():
        if not isinstance(node, dict) or not isinstance(node.get("class_type"), str) or not isinstance(node.get("inputs", {}), dict):
            errors.append(f"节点 {node_id} 格式无效。")
            continue
        node_type = node["class_type"]
        node_types.add(node_type)
        schema_info = object_info.get(node_type) if isinstance(object_info, dict) else None
        if not isinstance(schema_info, dict):
            errors.append(f"缺少节点：{node_type}")
            continue
        schema = _input_schema(schema_info)
        supplied_inputs = node.get("inputs", {})
        for required_name, required_spec in schema.items():
            if required_spec.get("required") and required_name not in supplied_inputs:
                errors.append(f"节点 {node_id} 缺少必需输入：{required_name}")
        for name, value in supplied_inputs.items():
            if name not in schema:
                errors.append(f"节点 {node_id} 缺少有效输入定义：{name}")
                continue
            choices = schema[name].get("choices")
            if choices and isinstance(value, str) and value not in choices:
                errors.append(f"节点 {node_id} 的模型选项 {value} 不在本机可用选项中。")
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], (str, int)):
                source_id = str(value[0])
                if source_id not in workflow:
                    errors.append(f"节点 {node_id} 引用了不存在的节点 {source_id}。")
    has_wan = any(name.startswith("WanVideo") for name in node_types)
    has_output = bool(node_types & VIDEO_OUTPUT_NODES)
    if not has_output:
        errors.append("工作流缺少视频输出节点。")
    return {"valid": not errors, "errors": errors, "node_count": len(workflow),
            "has_wan_video_nodes": has_wan, "has_video_output_node": has_output}


@router.get("/inspect")
def inspect_local_video_runtime():
    info = _local_json("/object_info")
    assessed = assess_wan_vace_nodes(info)
    inventory = _scan_video_model_folders() if info is not None else {"available": False, "folders": {}, "items": []}
    assessed["model_directory_scan"] = inventory
    assessed["base_url"] = COMFYUI_BASE_URL
    assessed["network_used"] = False
    assessed["cloud_fallback"] = False
    assessed["generation_submitted"] = False
    return assessed
