"""Read-only discovery for an already installed local Wan/VACE ComfyUI stack.

Discovery and workflow execution stay on the fixed local ComfyUI endpoint; no downloads or cloud fallback are used.
A positive capability scan is not a generation test; actual workflow execution is reported separately.
"""
import json
import re
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/local-ai", tags=["本地视频生成"])
COMFYUI_BASE_URL = "http://127.0.0.1:8188"
_OPENER = build_opener(ProxyHandler({}))
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_WORKFLOW_BYTES = 4 * 1024 * 1024
MAX_WORKFLOW_NODES = 500
PROMPT_ID_PATTERN = re.compile(r"^[0-9a-fA-F-]{32,36}$")
VIDEO_MODEL_FOLDERS = {
    "diffusion_models": "video_model",
    "checkpoints": "video_checkpoint",
    "vae": "video_vae",
    "text_encoders": "video_text_encoder",
    "clip": "video_text_encoder",
}
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


def _local_json(path, opener=None, timeout=4.0):
    # Fixed loopback URL and strict endpoint allow-list; no caller-supplied URL.
    allowed = {"/object_info", "/models"} | {
        "/models/" + folder for folder in VIDEO_MODEL_FOLDERS
    }
    if path not in allowed:
        raise ValueError("Unsupported local ComfyUI endpoint")
    request = Request(COMFYUI_BASE_URL + path, headers={"Accept": "application/json"})
    client = opener or _OPENER
    try:
        with client.open(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            return {"_error": "ComfyUI 响应超过安全读取上限"}
        payload = json.loads(raw.decode("utf-8"))
        return payload if isinstance(payload, (dict, list)) else {"_error": "ComfyUI 返回格式无效"}
    except HTTPError as exc:
        return {"_error": f"ComfyUI HTTP {exc.code}"}
    except (URLError, TimeoutError, OSError):
        return None
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"_error": "ComfyUI 返回了无法识别的数据"}


def _local_object_info(opener=None, timeout=4.0):
    return _local_json("/object_info", opener=opener, timeout=timeout)


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


def _scan_video_model_folders(opener=None):
    """Read actual filenames exposed by ComfyUI's local model-folder API."""
    folders = _local_json("/models", opener=opener)
    if not isinstance(folders, (dict, list)) or (isinstance(folders, dict) and folders.get("_error")):
        return {"items": [], "folders": {}, "available": False}
    folder_names = set(folders.keys()) if isinstance(folders, dict) else {
        item for item in folders if isinstance(item, str)
    }
    items, folder_status = [], {}
    for folder, category in VIDEO_MODEL_FOLDERS.items():
        if folder not in folder_names:
            folder_status[folder] = {"available": False, "count": 0, "reason": "ComfyUI 未公开此模型目录"}
            continue
        result = _local_json("/models/" + folder, opener=opener)
        if not isinstance(result, list):
            folder_status[folder] = {"available": False, "count": 0, "reason": "目录读取失败"}
            continue
        names = sorted({value[:500] for value in result if isinstance(value, str) and value.strip()})
        folder_status[folder] = {"available": True, "count": len(names)}
        items.extend({
            "name": name, "category": category,
            "source": "ComfyUI 本地模型目录", "model_folder": folder,
            "status": "file_listed", "load_tested": False,
        } for name in names)
    return {"items": items, "folders": folder_status, "available": True}


def assess_wan_vace_nodes(object_info):
    if not isinstance(object_info, dict) or object_info.get("_error"):
        return {
            "status": "unavailable",
            "node_count": 0,
            "nodes": [],
            "models": [],
            "model_inventory": [],
            "model_inventory_count": 0,
            "model_folders": {},
            "model_folder_scan_available": False,
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
    disk_inventory = _scan_video_model_folders()
    seen = {(item["category"], item["name"]) for item in model_inventory}
    for item in disk_inventory["items"]:
        key = (item["category"], item["name"])
        if key not in seen:
            model_inventory.append(item)
            seen.add(key)
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
        "model_folders": disk_inventory["folders"],
        "model_folder_scan_available": disk_inventory["available"],
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


class LocalVideoWorkflowInput(BaseModel):
    workflow: dict = Field(min_length=1)


def validate_local_video_workflow(workflow, object_info):
    """Validate an API-format ComfyUI graph against the live local node schema."""
    errors = []
    if not isinstance(workflow, dict) or not workflow:
        return {"valid": False, "errors": ["工作流必须是非空的 API 格式 JSON 对象。"], "node_count": 0}
    if len(workflow) > MAX_WORKFLOW_NODES:
        return {"valid": False, "errors": [f"工作流节点超过安全上限 {MAX_WORKFLOW_NODES}。"], "node_count": len(workflow)}
    try:
        encoded = json.dumps(workflow, ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError):
        return {"valid": False, "errors": ["工作流包含无法序列化的数据。"], "node_count": len(workflow)}
    if len(encoded) > MAX_WORKFLOW_BYTES:
        return {"valid": False, "errors": ["工作流 JSON 超过 4 MB 安全上限。"], "node_count": len(workflow)}
    if not isinstance(object_info, dict) or object_info.get("_error"):
        return {"valid": False, "errors": ["无法读取本机 ComfyUI 节点信息，请先启动引擎。"], "node_count": len(workflow)}
    node_ids = {str(key) for key in workflow}
    has_wan = False
    has_video_output = False
    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            errors.append(f"节点 {node_id} 格式无效。")
            continue
        class_type = node.get("class_type")
        inputs = node.get("inputs")
        if not isinstance(class_type, str) or not isinstance(inputs, dict):
            errors.append(f"节点 {node_id} 缺少 class_type 或 inputs。")
            continue
        if "wanvideo" in class_type.lower() or "wan_video" in class_type.lower() or "vace" in class_type.lower():
            has_wan = True
        if "videocombine" in class_type.lower() or "savevideo" in class_type.lower():
            has_video_output = True
        schema_info = object_info.get(class_type)
        if not isinstance(schema_info, dict):
            errors.append(f"节点 {node_id}：本机未安装节点 {class_type}。")
            continue
        schema = _node_input_schema(schema_info)
        known_inputs = {**schema["required"], **schema["optional"]}
        for required_name in schema["required"]:
            if required_name not in inputs:
                errors.append(f"节点 {node_id}（{class_type}）缺少必填参数 {required_name}。")
        for input_name, value in inputs.items():
            if input_name not in known_inputs:
                errors.append(f"节点 {node_id}（{class_type}）存在未知参数 {input_name}。")
                continue
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], (str, int)):
                if str(value[0]) not in node_ids:
                    errors.append(f"节点 {node_id} 的参数 {input_name} 引用了不存在的节点 {value[0]}。")
                continue
            choices = known_inputs[input_name].get("choices", [])
            if choices and value not in choices:
                errors.append(f"节点 {node_id} 的参数 {input_name} 值 {value!r} 不在本机可用选项中。")
    if not has_wan:
        errors.append("工作流中没有发现 Wan/VACE 视频模型节点。")
    if not has_video_output:
        errors.append("工作流中没有发现视频输出节点（VHS_VideoCombine 或 SaveVideo）。")
    return {
        "valid": not errors, "errors": errors[:100], "node_count": len(workflow),
        "has_wan_video_nodes": has_wan, "has_video_output_node": has_video_output,
        "local_only": True, "cloud_fallback": False,
    }


def _comfy_request(path, payload=None, timeout=15.0):
    if path != "/prompt" and not re.fullmatch(r"/history/[0-9a-fA-F-]{32,36}", path):
        raise ValueError("Unsupported local ComfyUI workflow endpoint")
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(
        COMFYUI_BASE_URL + path, data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
        method="POST" if data is not None else "GET",
    )
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise HTTPException(502, "ComfyUI 返回数据超过安全读取上限。")
        parsed = json.loads(raw.decode("utf-8"))
        if not isinstance(parsed, dict):
            raise HTTPException(502, "ComfyUI 返回格式无效。")
        return parsed
    except HTTPError as exc:
        try:
            detail = exc.read(4096).decode("utf-8", errors="replace")
        except Exception:
            detail = ""
        raise HTTPException(502, f"本地 ComfyUI 请求失败（HTTP {exc.code}）：{detail[:500]}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise HTTPException(503, "本机 ComfyUI 未连接；未切换云端服务。") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(502, "ComfyUI 返回了无法识别的数据。") from exc


def build_video_template_capabilities(object_info):
    """Discover installed Wan/VACE building blocks without guessing workflow wiring."""
    if not isinstance(object_info, dict) or object_info.get("_error"):
        return {"available": False, "roles": {}, "nodes": [], "message": "本机 ComfyUI 节点信息不可用。"}
    role_terms = {
        "model_loader": ("model loader", "model_loader", "load model"),
        "text_encoder": ("text encoder", "t5", "umt5", "cliptextencode"),
        "text_encode": ("text encode", "textencode", "prompt"),
        "sampler": ("sampler",),
        "vae_loader": ("vae loader", "load vae"),
        "decode": ("decode", "vae decode"),
        "image_input": ("loadimage", "load image", "image loader"),
        "video_input": ("loadvideo", "load video", "video loader"),
        "vace": ("vace",),
        "video_output": ("videocombine", "savevideo", "video combine"),
    }
    roles = {}
    discovered = []
    for class_name, info in object_info.items():
        if not isinstance(class_name, str) or not isinstance(info, dict):
            continue
        lowered = class_name.lower().replace("_", " ")
        schema = _node_input_schema(info)
        matching = [
            role for role, terms in role_terms.items()
            if any(term in lowered for term in terms)
        ]
        if not matching:
            continue
        entry = {
            "class_type": class_name,
            "roles": matching,
            "input_schema": schema,
        }
        discovered.append(entry)
        for role in matching:
            roles.setdefault(role, []).append(class_name)
    for role in roles:
        roles[role] = sorted(roles[role])[:30]
    return {
        "available": bool(discovered),
        "roles": roles,
        "nodes": discovered[:120],
        "message": (
            "已读取本机节点输入和模型选项。下一步可据此构建模板；当前只做能力发现，不会生成猜测性的工作流。"
            if discovered else "未发现可用于构建视频工作流的 Wan/VACE 节点。"
        ),
        "template_generated": False,
        "generation_ready": False,
        "local_only": True,
        "cloud_fallback": False,
    }


@router.get("/video-workflow/template-capabilities")
def video_workflow_template_capabilities():
    """Return live node roles and inputs for safe, version-aware template construction."""
    info = _local_object_info()
    if not isinstance(info, dict) or info.get("_error"):
        raise HTTPException(503, "无法读取本机 ComfyUI 节点信息；未生成工作流。")
    return build_video_template_capabilities(info)


@router.post("/video-workflow/validate")
def validate_video_workflow_endpoint(data: LocalVideoWorkflowInput):
    object_info = _local_object_info()
    result = validate_local_video_workflow(data.workflow, object_info)
    if not result["valid"]:
        raise HTTPException(422, detail={"message": "本地视频工作流未通过预检。", **result})
    return {**result, "message": "工作流结构、节点和模型选项通过本机预检；尚未执行生成。"}


@router.post("/video-workflow/submit")
def submit_video_workflow_endpoint(data: LocalVideoWorkflowInput):
    object_info = _local_object_info()
    result = validate_local_video_workflow(data.workflow, object_info)
    if not result["valid"]:
        raise HTTPException(422, detail={"message": "本地视频工作流未通过预检，未提交任务。", **result})
    client_id = str(uuid.uuid4())
    queued = _comfy_request("/prompt", {
        "prompt": data.workflow,
        "client_id": client_id,
    }, timeout=30.0)
    prompt_id = queued.get("prompt_id")
    if not isinstance(prompt_id, str) or not PROMPT_ID_PATTERN.fullmatch(prompt_id):
        raise HTTPException(502, "ComfyUI 未返回有效 prompt_id；请检查本机队列状态。")
    return {
        "prompt_id": prompt_id,
        "number": queued.get("number"),
        "status": "queued",
        "node_errors": queued.get("node_errors", {}),
        "local_only": True, "cloud_fallback": False,
        "message": "任务已提交到本机 ComfyUI 队列。实际生成是否成功以任务状态和视频输出为准。",
    }


@router.get("/video-workflow/{prompt_id}")
def get_video_workflow_status(prompt_id: str):
    if not PROMPT_ID_PATTERN.fullmatch(prompt_id):
        raise HTTPException(400, "任务编号格式无效。")
    history = _comfy_request("/history/" + prompt_id, timeout=15.0)
    item = history.get(prompt_id)
    if not isinstance(item, dict):
        return {"prompt_id": prompt_id, "status": "queued_or_running", "completed": False, "message": "本机队列尚未返回完成记录。"}
    status_data = item.get("status") if isinstance(item.get("status"), dict) else {}
    outputs = item.get("outputs") if isinstance(item.get("outputs"), dict) else {}
    videos = []
    for node_id, output in outputs.items():
        if not isinstance(output, dict):
            continue
        for key in ("videos", "gifs"):
            values = output.get(key)
            if isinstance(values, list):
                for value in values[:50]:
                    if isinstance(value, dict):
                        videos.append({
                            "node_id": node_id, "filename": value.get("filename"),
                            "subfolder": value.get("subfolder", ""),
                            "type": value.get("type", "output"),
                        })
    completed = bool(status_data.get("completed"))
    status_messages = status_data.get("messages") if isinstance(status_data.get("messages"), list) else []
    return {
        "prompt_id": prompt_id,
        "status": "completed" if completed and videos else "completed_without_video" if completed else "running_or_failed",
        "completed": completed, "videos": videos,
        "status_messages": status_messages[-30:],
        "local_only": True, "cloud_fallback": False,
        "message": "已完成并发现视频输出。" if completed and videos else "任务已结束但未发现标准视频输出，请查看 ComfyUI 工作流输出。" if completed else "任务仍在运行或执行失败，请稍后刷新并查看本机 ComfyUI 队列。",
    }


@router.get("/video-workflow/{prompt_id}/preview")
def preview_video_workflow_output(prompt_id: str, output_index: int = 0):
    """Stream a completed video output from the fixed local ComfyUI /view endpoint."""
    if not PROMPT_ID_PATTERN.fullmatch(prompt_id):
        raise HTTPException(400, "任务编号格式无效。")
    if output_index < 0 or output_index > 49:
        raise HTTPException(400, "视频输出序号超出范围。")
    history = _comfy_request("/history/" + prompt_id, timeout=15.0)
    item = history.get(prompt_id)
    if not isinstance(item, dict):
        raise HTTPException(404, "本地任务尚无历史记录。")
    outputs = item.get("outputs") if isinstance(item.get("outputs"), dict) else {}
    videos = []
    for node_id, output in outputs.items():
        if not isinstance(output, dict):
            continue
        for key in ("videos", "gifs"):
            values = output.get(key)
            if isinstance(values, list):
                for value in values[:50]:
                    if isinstance(value, dict) and isinstance(value.get("filename"), str):
                        videos.append(value)
    if output_index >= len(videos):
        raise HTTPException(404, "该任务没有对应的视频输出。")
    selected = videos[output_index]
    filename = selected["filename"]
    subfolder = selected.get("subfolder", "")
    media_type = selected.get("type", "output")
    if len(filename) > 500 or len(subfolder) > 500 or media_type not in ("output", "temp"):
        raise HTTPException(422, "ComfyUI 视频输出元数据无效。")
    query = urlencode({"filename": filename, "subfolder": subfolder, "type": media_type})
    request = Request(COMFYUI_BASE_URL + "/view?" + query, headers={"Accept": "video/*"})
    try:
        response = _OPENER.open(request, timeout=30.0)
    except HTTPError as exc:
        raise HTTPException(502, f"读取本机视频输出失败（HTTP {exc.code}）。") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise HTTPException(503, "无法读取本机 ComfyUI 视频输出。") from exc

    content_type = response.headers.get("Content-Type", "video/mp4")
    if not (content_type.startswith("video/") or content_type == "image/gif"):
        response.close()
        raise HTTPException(415, "ComfyUI 返回的文件不是受支持的视频格式。")

    def stream():
        total = 0
        try:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > 512 * 1024 * 1024:
                    break
                yield chunk
        finally:
            response.close()

    headers = {"Content-Disposition": "inline", "Cache-Control": "no-store"}
    return StreamingResponse(stream(), media_type=content_type, headers=headers)


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
