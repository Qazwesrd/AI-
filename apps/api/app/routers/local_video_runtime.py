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

from app.routers.local_ai_runtime import _installed_models, _ollama_request

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
    allowed = {"/object_info", "/models", "/system_stats"} | {
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


class LocalVideoAutoBuildInput(BaseModel):
    prompt: str = Field(min_length=3, max_length=6000)
    mode: str = Field(default="text_to_video", pattern="^(text_to_video|image_to_video|vace)$")
    width: int = Field(default=832, ge=256, le=2048)
    height: int = Field(default=480, ge=256, le=2048)
    frames: int = Field(default=33, ge=1, le=241)
    fps: int = Field(default=16, ge=1, le=60)
    steps: int = Field(default=20, ge=1, le=100)


class LocalVideoTemplateNode(BaseModel):
    node_id: str = Field(min_length=1, max_length=64)
    class_type: str = Field(min_length=1, max_length=200)
    values: dict = Field(default_factory=dict)
    connections: dict = Field(default_factory=dict)


class LocalVideoTemplateBuildInput(BaseModel):
    nodes: list[LocalVideoTemplateNode] = Field(min_length=1, max_length=MAX_WORKFLOW_NODES)


def build_explicit_video_workflow(nodes, object_info):
    """Build an API prompt only from explicit user-selected nodes, values and links."""
    errors = []
    workflow = {}
    by_id = {}
    for node in nodes:
        if node.node_id in by_id:
            errors.append(f"节点编号重复：{node.node_id}")
            continue
        by_id[node.node_id] = node
    for node in nodes:
        info = object_info.get(node.class_type) if isinstance(object_info, dict) else None
        if not isinstance(info, dict):
            errors.append(f"节点 {node.node_id}：本机未安装节点 {node.class_type}。")
            continue
        schema = _node_input_schema(info)
        known = {**schema["required"], **schema["optional"]}
        unknown = (set(node.values) | set(node.connections)) - set(known)
        if unknown:
            errors.append(f"节点 {node.node_id} 存在未知输入：{', '.join(sorted(unknown))}。")
        overlap = set(node.values) & set(node.connections)
        if overlap:
            errors.append(f"节点 {node.node_id} 的输入不能同时指定常量和连线：{', '.join(sorted(overlap))}。")
        inputs = dict(node.values)
        for input_name, connection in node.connections.items():
            if input_name not in known:
                continue
            if not isinstance(connection, dict):
                errors.append(f"节点 {node.node_id} 的连线 {input_name} 格式无效。")
                continue
            source_id = connection.get("node_id")
            output_index = connection.get("output_index")
            source = by_id.get(str(source_id))
            if source is None:
                errors.append(f"节点 {node.node_id} 的输入 {input_name} 指向未声明节点 {source_id}。")
                continue
            source_info = object_info.get(source.class_type)
            outputs = _node_output_schema(source_info) if isinstance(source_info, dict) else []
            if not isinstance(output_index, int) or isinstance(output_index, bool) or output_index < 0 or output_index >= len(outputs):
                errors.append(f"节点 {node.node_id} 的输入 {input_name} 指定了无效输出端口。")
                continue
            target_type = known[input_name].get("type")
            source_type = outputs[output_index].get("type")
            wildcard = {"*", "ANY", "ANY_TYPE"}
            if target_type and source_type and target_type not in wildcard and source_type not in wildcard and target_type != source_type:
                errors.append(f"节点 {node.node_id}.{input_name} 类型 {target_type} 与 {source.class_type}[{output_index}] 类型 {source_type} 不匹配。")
                continue
            inputs[input_name] = [str(source_id), output_index]
        for required_name in schema["required"]:
            if required_name not in inputs:
                errors.append(f"节点 {node.node_id}（{node.class_type}）缺少必填输入 {required_name}。")
        for input_name, value in node.values.items():
            choices = known.get(input_name, {}).get("choices", [])
            if choices and value not in choices:
                errors.append(f"节点 {node.node_id}.{input_name} 的值不在本机可用选项中。")
        workflow[node.node_id] = {"class_type": node.class_type, "inputs": inputs}
    if errors:
        return {"valid": False, "errors": errors[:100], "workflow": None}
    validation = validate_local_video_workflow(workflow, object_info)
    if not validation["valid"]:
        return {"valid": False, "errors": validation["errors"], "workflow": workflow, "validation": validation}
    return {"valid": True, "errors": [], "workflow": workflow, "validation": validation}


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
    has_wan_processing_node = False
    has_video_output = False
    has_video_output_connection = False
    graph_edges = []
    model_loader_ids = set()
    processing_ids = set()
    video_output_ids = set()
    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            errors.append(f"节点 {node_id} 格式无效。")
            continue
        class_type = node.get("class_type")
        inputs = node.get("inputs")
        if not isinstance(class_type, str) or not isinstance(inputs, dict):
            errors.append(f"节点 {node_id} 缺少 class_type 或 inputs。")
            continue
        class_name_lower = class_type.lower()
        is_wan_node = (
            "wanvideo" in class_name_lower or "wan_video" in class_name_lower or
            "vace" in class_name_lower
        )
        if is_wan_node:
            has_wan = True
        is_model_loader = is_wan_node and (
            "model" in class_name_lower and ("load" in class_name_lower or "loader" in class_name_lower)
        )
        is_processing_node = is_wan_node and (
            "sampler" in class_name_lower or "vace" in class_name_lower
        )
        if is_model_loader:
            model_loader_ids.add(str(node_id))
        if is_processing_node:
            has_wan_processing_node = True
            processing_ids.add(str(node_id))
        is_video_output_node = "videocombine" in class_name_lower or "savevideo" in class_name_lower
        if is_video_output_node:
            has_video_output = True
            video_output_ids.add(str(node_id))
        schema_info = object_info.get(class_type)
        if not isinstance(schema_info, dict):
            errors.append(f"节点 {node_id}：本机未安装节点 {class_type}。")
            continue
        schema = _node_input_schema(schema_info)
        known_inputs = {**schema["required"], **schema["optional"]}
        if is_video_output_node:
            for output_input_name, output_input_value in inputs.items():
                output_input_type = known_inputs.get(output_input_name, {}).get("type")
                if (
                    output_input_type in {"IMAGE", "VIDEO", "FRAMES", "VHS_FILENAMES"} and
                    isinstance(output_input_value, list) and len(output_input_value) == 2
                ):
                    has_video_output_connection = True
        for required_name in schema["required"]:
            if required_name not in inputs:
                errors.append(f"节点 {node_id}（{class_type}）缺少必填参数 {required_name}。")
        for input_name, value in inputs.items():
            if input_name not in known_inputs:
                errors.append(f"节点 {node_id}（{class_type}）存在未知参数 {input_name}。")
                continue
            if (
                isinstance(value, list) and len(value) == 2 and
                isinstance(value[0], (str, int)) and not isinstance(value[0], bool)
            ):
                source_id = str(value[0])
                output_index = value[1]
                if source_id not in node_ids:
                    errors.append(f"节点 {node_id} 的参数 {input_name} 引用了不存在的节点 {value[0]}。")
                    continue
                if source_id == str(node_id):
                    errors.append(f"节点 {node_id} 的参数 {input_name} 不允许连接自身。")
                    continue
                if not isinstance(output_index, int) or isinstance(output_index, bool) or output_index < 0:
                    errors.append(f"节点 {node_id} 的参数 {input_name} 输出端口序号无效。")
                    continue
                source_node = workflow.get(source_id)
                source_class = source_node.get("class_type") if isinstance(source_node, dict) else None
                source_info = object_info.get(source_class) if isinstance(source_class, str) else None
                if not isinstance(source_info, dict):
                    errors.append(f"节点 {node_id} 的参数 {input_name} 来源节点类型无法验证。")
                    continue
                source_outputs = _node_output_schema(source_info)
                if output_index >= len(source_outputs):
                    errors.append(
                        f"节点 {node_id} 的参数 {input_name} 引用了 {source_class} 不存在的输出端口 {output_index}。"
                    )
                    continue
                input_type = known_inputs[input_name].get("type")
                output_type = source_outputs[output_index].get("type")
                wildcard_types = {"*", "ANY", "ANY_TYPE"}
                if (
                    input_type and output_type and
                    input_type not in wildcard_types and output_type not in wildcard_types and
                    input_type != output_type
                ):
                    errors.append(
                        f"节点 {node_id} 的参数 {input_name} 类型 {input_type} "
                        f"与来源节点 {source_id} 输出类型 {output_type} 不匹配。"
                    )
                graph_edges.append((source_id, str(node_id)))
                continue
            choices = known_inputs[input_name].get("choices", [])
            if choices and value not in choices:
                errors.append(f"节点 {node_id} 的参数 {input_name} 值 {value!r} 不在本机可用选项中。")
    if not has_wan:
        errors.append("工作流中没有发现 Wan/VACE 视频模型节点。")
    if not has_wan_processing_node:
        errors.append("工作流中没有发现 Wan 视频采样或 VACE 处理节点。")
    if not has_video_output:
        errors.append("工作流中没有发现视频输出节点（VHS_VideoCombine 或 SaveVideo）。")
    elif not has_video_output_connection:
        errors.append("视频输出节点没有连接图像帧/视频输入，无法确认存在可导出的视频内容。")

    # Presence checks alone are insufficient: prove that at least one local
    # Wan model loader reaches a Wan sampler/VACE processor and then a video
    # output through the submitted graph's actual links.
    adjacency = {}
    for source_id, target_id in graph_edges:
        adjacency.setdefault(source_id, set()).add(target_id)

    def _reachable(start_ids, target_ids):
        pending = list(start_ids)
        visited = set()
        while pending:
            current = pending.pop()
            if current in target_ids:
                return True
            if current in visited:
                continue
            visited.add(current)
            pending.extend(adjacency.get(current, ()))
        return False

    model_to_processing = _reachable(model_loader_ids, processing_ids)
    processing_to_output = _reachable(processing_ids, video_output_ids)
    has_connected_generation_chain = model_to_processing and processing_to_output
    if has_wan and has_wan_processing_node and has_video_output and not has_connected_generation_chain:
        errors.append("Wan 模型加载、采样/VACE 处理和视频输出未形成连续连通链路。")

    return {
        "valid": not errors, "errors": errors[:100], "node_count": len(workflow),
        "has_wan_video_nodes": has_wan, "has_wan_processing_node": has_wan_processing_node,
        "has_video_output_node": has_video_output,
        "has_video_output_connection": has_video_output_connection,
        "has_connected_generation_chain": has_connected_generation_chain,
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


def _node_output_schema(node_info):
    """Expose ComfyUI output socket names and types for type-safe graph planning."""
    if not isinstance(node_info, dict):
        return []
    output_types = node_info.get("output")
    output_names = node_info.get("output_name")
    if not isinstance(output_types, list):
        return []
    result = []
    for index, output_type in enumerate(output_types[:120]):
        if not isinstance(output_type, str):
            continue
        name = output_names[index] if isinstance(output_names, list) and index < len(output_names) else f"output_{index}"
        result.append({"index": index, "name": str(name)[:120], "type": output_type[:120]})
    return result


def build_video_template_capabilities(object_info):
    """Discover installed Wan/VACE building blocks without guessing workflow wiring."""
    if not isinstance(object_info, dict) or object_info.get("_error"):
        return {"available": False, "roles": {}, "nodes": [], "message": "本机 ComfyUI 节点信息不可用。"}

    # ComfyUI custom node class names commonly use CamelCase without spaces.
    role_terms = {
        "model_loader": ("modelloader", "loadmodel", "model_loader"),
        "text_encoder": ("textencoder", "umt5", "t5encoder", "cliptextencode"),
        "text_encode": ("textencode", "encodeprompt", "promptencode"),
        "sampler": ("sampler",),
        "vae_loader": ("vaeloader", "loadvae"),
        "decode": ("decode", "vaedecode"),
        "image_input": ("loadimage", "imageloader", "imageinput"),
        "video_input": ("loadvideo", "videoloader", "videoinput"),
        "vace": ("vace",),
        "video_output": ("videocombine", "savevideo", "videooutput"),
    }
    roles = {}
    discovered = []
    video_specific_nodes = []
    for class_name, info in object_info.items():
        if not isinstance(class_name, str) or not isinstance(info, dict):
            continue
        compact = re.sub(r"[^a-z0-9]", "", class_name.lower())
        matching = [
            role for role, terms in role_terms.items()
            if any(re.sub(r"[^a-z0-9]", "", term.lower()) in compact for term in terms)
        ]
        if not matching:
            continue
        if "wanvideo" in compact or "wan" in compact or "vace" in compact:
            video_specific_nodes.append(class_name)
        discovered.append({
            "class_type": class_name,
            "roles": matching,
            "input_schema": _node_input_schema(info),
            "output_schema": _node_output_schema(info),
        })
        for role in matching:
            roles.setdefault(role, []).append(class_name)
    for role in roles:
        roles[role] = sorted(roles[role])[:30]

    # Build a bounded type-compatibility map. This is a planning aid only:
    # exact socket type matches do not prove semantic compatibility or a valid
    # Wan graph, so no links are emitted into an executable prompt here.
    connection_candidates = []
    if video_specific_nodes:
        output_index = {}
        for node in discovered:
            for output in node["output_schema"]:
                output_index.setdefault(output["type"], []).append({
                    "class_type": node["class_type"],
                    "output_index": output["index"],
                    "output_name": output["name"],
                })
        for node in discovered:
            for section in ("required", "optional"):
                for input_name, input_info in node["input_schema"][section].items():
                    socket_type = input_info.get("type")
                    if not socket_type or socket_type == "COMBO" or input_info.get("choices"):
                        continue
                    sources = [
                        source for source in output_index.get(socket_type, [])
                        if source["class_type"] != node["class_type"]
                    ]
                    if sources:
                        connection_candidates.append({
                            "target_class": node["class_type"],
                            "input": input_name,
                            "input_type": socket_type,
                            "sources": sources[:20],
                        })
                    if len(connection_candidates) >= 500:
                        break
                if len(connection_candidates) >= 500:
                    break
            if len(connection_candidates) >= 500:
                break

    # Report role coverage separately from executable-template readiness.
    # These are discovery hints, not a claim that the sockets can be wired.
    t2v_role_groups = {
        "model_loader": ["model_loader"],
        "text_conditioning": ["text_encoder", "text_encode"],
        "sampler": ["sampler"],
        "decode": ["decode"],
        "video_output": ["video_output"],
    }
    t2v_missing_roles = [
        group for group, alternatives in t2v_role_groups.items()
        if not any(roles.get(role) for role in alternatives)
    ]
    vace_role_groups = {
        **t2v_role_groups,
        "vace_processing": ["vace"],
        "source_media_input": ["image_input", "video_input"],
    }
    vace_missing_roles = [
        group for group, alternatives in vace_role_groups.items()
        if not any(roles.get(role) for role in alternatives)
    ]

    # Generic KSampler/SaveImage nodes alone must not imply video capability.
    available = bool(video_specific_nodes and discovered)
    return {
        "available": available,
        "roles": roles if available else {},
        "template_assessment": {
            "wan_t2v_role_coverage": {
                "missing_roles": t2v_missing_roles if available else list(t2v_role_groups),
                "all_role_groups_found": available and not t2v_missing_roles,
            },
            "vace_role_coverage": {
                "missing_roles": vace_missing_roles if available else list(vace_role_groups),
                "all_role_groups_found": available and not vace_missing_roles,
            },
            "note": "角色齐全仅表示发现了候选节点，不代表连线语义、模型兼容性或执行成功。",
        },
        "nodes": discovered[:120] if available else [],
        "connection_candidates": connection_candidates if available else [],
        "connection_candidate_count": len(connection_candidates) if available else 0,
        "video_specific_nodes": sorted(video_specific_nodes)[:60],
        "message": (
            "已读取本机 Wan/VACE 节点输入和模型选项。当前只完成能力发现，尚未生成可执行模板。"
            if available else "未发现 Wan/VACE 专用节点；通用采样器或图片节点不能证明本机具备视频生成能力。"
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


@router.post("/video-workflow/build")
def build_video_workflow_endpoint(data: LocalVideoTemplateBuildInput):
    """Build an API workflow from explicit local node/input/link selections; never infer links."""
    object_info = _local_object_info()
    if not isinstance(object_info, dict) or object_info.get("_error"):
        raise HTTPException(503, "无法读取本机 ComfyUI 节点信息；未生成工作流。")
    result = build_explicit_video_workflow(data.nodes, object_info)
    if not result["valid"]:
        raise HTTPException(422, detail={"message": "模板构建未通过本机节点结构校验。", **result})
    return {
        **result,
        "message": "已根据明确指定的节点、参数和连线生成 API 工作流；尚未提交生成任务。",
        "local_only": True,
        "cloud_fallback": False,
    }


def _extract_auto_workflow_json(value):
    content = str(value or "").strip()
    content = re.sub(r"^\x60\x60\x60(?:json)?\s*", "", content)
    content = re.sub(r"\s*\x60\x60\x60$", "", content)
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        start = content.find("{")
        end = content.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("本地模型没有返回 JSON 工作流节点列表。")
        parsed = json.loads(content[start:end + 1])
    if not isinstance(parsed, dict) or not isinstance(parsed.get("nodes"), list):
        raise ValueError("本地模型返回格式错误：必须包含 nodes 数组。")
    return parsed


def _apply_video_request_values(nodes, object_info, data):
    """Inject the user's prompt and numeric targets into matching live node inputs."""
    prompt_inputs = 0
    numeric_values = {
        "width": data.width, "height": data.height,
        "length": data.frames, "frames": data.frames,
        "numframes": data.frames, "framecount": data.frames,
        "fps": data.fps, "framerate": data.fps,
        "steps": data.steps, "numsteps": data.steps,
    }
    for node in nodes:
        info = object_info.get(node.class_type, {})
        schema = _node_input_schema(info)
        known = {**schema["required"], **schema["optional"]}
        for input_name, field in known.items():
            compact = re.sub(r"[^a-z0-9]", "", input_name.lower())
            input_type = field.get("type")
            if (
                input_type == "STRING"
                and "negative" not in compact
                and ("prompt" in compact or compact in {"text", "positive"})
                and input_name not in node.connections
            ):
                node.values[input_name] = data.prompt.strip()
                prompt_inputs += 1
            elif input_type in {"INT", "FLOAT"} and input_name not in node.connections:
                if compact in numeric_values:
                    node.values[input_name] = numeric_values[compact]
    if prompt_inputs == 0:
        raise HTTPException(422, "本机 Wan/VACE 节点中没有可安全写入视频提示词的文本输入；未生成工作流。")
    return prompt_inputs


def _auto_video_node_catalog(object_info):
    catalog = []
    for class_name, info in object_info.items():
        if not isinstance(class_name, str) or not isinstance(info, dict):
            continue
        compact = re.sub(r"[^a-z0-9]", "", class_name.lower())
        if not any(token in compact for token in ("wanvideo", "wan_video", "vace", "videocombine", "savevideo", "loadimage", "loadvideo")):
            continue
        catalog.append({
            "class_type": class_name,
            "input_schema": _node_input_schema(info),
            "output_schema": _node_output_schema(info),
        })
    return sorted(catalog, key=lambda item: item["class_type"])[:100]


@router.post("/video-workflow/auto-build")
def auto_build_local_video_workflow(data: LocalVideoAutoBuildInput):
    """Ask an already-installed local Ollama model to draft a Wan/VACE graph, then validate every node/link locally."""
    object_info = _local_object_info()
    if not isinstance(object_info, dict) or object_info.get("_error"):
        raise HTTPException(503, "本机 ComfyUI 节点信息不可用；未生成工作流。")
    catalog = _auto_video_node_catalog(object_info)
    wan_nodes = [item for item in catalog if any(token in item["class_type"].lower() for token in ("wanvideo", "wan_video", "vace"))]
    output_nodes = [item for item in catalog if any(token in item["class_type"].lower() for token in ("videocombine", "savevideo"))]
    if not wan_nodes or not output_nodes:
        raise HTTPException(409, detail={
            "message": "本机尚未具备完整的 Wan/VACE 节点基础，无法自动配置。",
            "missing": (["Wan/VACE 模型与处理节点"] if not wan_nodes else []) + (["视频输出节点"] if not output_nodes else []),
            "cloud_fallback": False,
        })
    try:
        installed = _installed_models()
    except HTTPException as exc:
        raise HTTPException(503, "本机 Ollama 未连接；自动配置不会切换云端。") from exc
    if not installed:
        raise HTTPException(404, "本机 Ollama 没有已安装的文本模型；不会自动下载模型。")
    model = installed[0]["name"]
    system_prompt = (
        "你是 ComfyUI Wan/VACE 工作流工程师。只能使用用户提供的本机节点清单和接口信息，"
        "不得虚构节点名、输入名、输出端口或模型文件。必须生成可被 ComfyUI API 接受的节点配置。"
        "输出纯 JSON，根对象必须包含 nodes 数组。每个 nodes 元素必须包含 node_id、class_type、values、connections 字段；"
        "connections 的每个输入映射必须包含 node_id 和 output_index。每个节点必填输入必须通过 values 或 connections 提供。"
        "连接必须按输出类型与输入类型匹配。模型加载节点必须连到 Wan sampler/VACE 处理节点，"
        "处理节点必须连到 VHS_VideoCombine/SaveVideo 视频输出节点。"
        "COMBO 输入只能使用清单中的 choices；有 default 时优先使用 default。将目标宽度、高度、帧数、FPS 和步数写入名称对应的数值输入，不能只把它们留在说明文字里。"
        "文本提示词必须写入实际文本输入，不要把用户提示词写到文件路径字段。"
        "没有可用模型选择、所选模式必需的输入或关键节点时，不要猜测，返回 nodes 为空数组；纯文本生成模式不应强行要求参考图。"
    )
    user_prompt = (
        "任务模式：" + data.mode + "\n"
        "视频提示词：" + data.prompt.strip() + "\n"
        f"目标参数：宽度 {data.width}，高度 {data.height}，帧数 {data.frames}，FPS {data.fps}，采样步数 {data.steps}。\n"
        "本机节点目录（只能使用这些节点）：\n"
        + json.dumps(catalog, ensure_ascii=False, separators=(",", ":"))
    )
    try:
        response = _ollama_request("/api/generate", {
            "model": model,
            "system": system_prompt,
            "prompt": user_prompt,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.1, "num_predict": 4096},
        }, timeout=600.0)
        specification = _extract_auto_workflow_json(response.get("response") if isinstance(response, dict) else "")
        if not specification["nodes"]:
            raise HTTPException(422, "本机模型判断当前节点信息不足以可靠构建 Wan/VACE 工作流；未生成或提交任务。")
        nodes = [LocalVideoTemplateNode.model_validate(item) for item in specification["nodes"]]
        prompt_input_count = _apply_video_request_values(nodes, object_info, data)
        result = build_explicit_video_workflow(nodes, object_info)
        if not result["valid"]:
            raise HTTPException(422, detail={
                "message": "AI 已生成工作流草稿，但本机结构预检未通过；没有提交生成任务。",
                "errors": result["errors"],
                "workflow": result.get("workflow"),
            })
        return {
            **result,
            "model": model,
            "mode": data.mode,
            "prompt_inputs_configured": prompt_input_count,
            "generation_ready": False,
            "workflow_execution_verified": False,
            "message": "本机 Ollama 已自动配置工作流并通过 ComfyUI 节点、参数、连接和视频输出结构预检。尚未实际加载模型或生成视频；请先检查草稿，再手动提交本地队列。",
            "local_only": True,
            "network_used": False,
            "cloud_fallback": False,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(422, "本机 AI 自动配置失败：" + str(exc)[:1200]) from exc


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
    status_text = str(status_data.get("status_str", "")).lower()
    status_messages = status_data.get("messages") if isinstance(status_data.get("messages"), list) else []
    execution_failed = status_text in ("error", "failed", "interrupted") or any(
        isinstance(message, (list, tuple)) and len(message) > 1 and
        isinstance(message[1], dict) and message[1].get("exception_type")
        for message in status_messages[-30:]
    )
    if execution_failed:
        task_status = "failed"
        message = "本地 ComfyUI 报告任务执行失败；请查看错误详情并检查模型、节点依赖和显存。"
    elif completed and videos:
        task_status = "completed"
        message = "已完成并发现视频输出。"
    elif completed:
        task_status = "completed_without_video"
        message = "任务已结束但未发现标准视频输出，请检查工作流是否连接视频保存节点。"
    else:
        task_status = "running"
        message = "本地任务仍在队列中或执行中；可稍后刷新状态。"
    return {
        "prompt_id": prompt_id,
        "status": task_status,
        "completed": completed,
        "failed": execution_failed,
        "videos": videos,
        "status_messages": status_messages[-30:],
        "local_only": True, "cloud_fallback": False,
        "message": message,
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
    if (
        not filename or len(filename) > 500 or
        "/" in filename or "\\\\" in filename or
        any(ord(char) < 32 for char in filename) or
        len(subfolder) > 500 or
        any(ord(char) < 32 for char in subfolder) or
        ":" in subfolder or
        any(part in (".", "..") for part in re.split(r"[\\\\/]", subfolder) if part) or
        subfolder.startswith(("/", "\\\\")) or
        media_type not in ("output", "temp")
    ):
        raise HTTPException(422, "ComfyUI 视频输出路径或元数据无效。")
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
    content_length = response.headers.get("Content-Length")
    if content_length:
        try:
            declared_size = int(content_length)
        except ValueError:
            response.close()
            raise HTTPException(502, "ComfyUI 视频输出长度信息无效。")
        if declared_size < 0 or declared_size > 512 * 1024 * 1024:
            response.close()
            raise HTTPException(413, "视频预览超过 512 MB 安全上限，请在本地输出目录直接处理。")

    def stream():
        total = 0
        try:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > 512 * 1024 * 1024:
                    # Never silently return a truncated video as if it were complete.
                    raise RuntimeError("ComfyUI 视频预览超过 512 MB 安全上限。")
                yield chunk
        finally:
            response.close()

    headers = {"Content-Disposition": "inline", "Cache-Control": "no-store"}
    return StreamingResponse(stream(), media_type=content_type, headers=headers)



def assess_video_resource_preflight(workflow, diagnostics):
    """Provide conservative, read-only suggestions from measured local resources and workflow settings."""
    numeric_inputs = {}
    for node in workflow.values() if isinstance(workflow, dict) else []:
        inputs = node.get("inputs") if isinstance(node, dict) else None
        if not isinstance(inputs, dict):
            continue
        for key, value in inputs.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if normalized in {"width", "height", "frames", "numframes", "length", "batchsize", "steps"}:
                    numeric_inputs.setdefault(normalized, []).append(value)
    settings = {key: max(values) for key, values in numeric_inputs.items() if values}
    devices = diagnostics.get("devices", []) if isinstance(diagnostics, dict) else []
    devices = [item for item in devices if isinstance(item, dict)]
    warnings = []
    recommendations = []
    if not diagnostics.get("available"):
        warnings.append("无法读取本机运行环境；资源状况未知，不能确认模型可加载。")
        recommendations.append("先启动本机 ComfyUI 并重新检测运行环境，再提交生成。")
    elif not devices:
        warnings.append("ComfyUI 未报告可用加速设备，任务可能使用 CPU，生成速度可能很慢。")
        recommendations.append("首次仅尝试低分辨率、短帧数和 batch=1；不要同时运行其他大型模型。")
    else:
        known = [item for item in devices if isinstance(item.get("vram_free_mb"), (int, float))]
        if not known:
            warnings.append("已发现计算设备，但未报告可用显存；无法估算模型加载余量。")
            recommendations.append("先使用模型作者提供的低显存设置，并保留当前工作流副本。")
        else:
            free_mb = min(item["vram_free_mb"] for item in known)
            if free_mb < 2048:
                warnings.append("当前报告的可用显存低于 2 GB，加载视频模型失败或发生内存溢出的风险较高。")
            elif free_mb < 4096:
                warnings.append("当前报告的可用显存低于 4 GB，视频模型可能无法完整加载。")
            elif free_mb < 8192:
                warnings.append("当前可用显存有限，建议先用低分辨率、短帧数验证工作流。")
            recommendations.append("先将宽、高和帧数分别降低到当前值约一半，batch 保持 1；确认能成功输出后再逐项提高。")
    if settings:
        recommendations.append("本次识别到的工作流参数：" + "、".join(f"{key}={value:g}" for key, value in sorted(settings.items())) + "。建议只在工作流副本中调整。")
    else:
        recommendations.append("未能从当前工作流识别标准分辨率/帧数参数；请按该模型节点的说明手动设置，不自动猜测节点参数。")
    return {
        "available": bool(diagnostics.get("available")),
        "device_count": len(devices),
        "devices": devices,
        "workflow_settings": settings,
        "warnings": warnings,
        "recommendations": recommendations,
        "requires_user_confirmation": bool(warnings),
        "workflow_modified": False,
        "generation_submitted": False,
        "local_only": True,
        "cloud_fallback": False,
    }


@router.post("/video-workflow/preflight")
def preflight_video_workflow_endpoint(data: LocalVideoWorkflowInput):
    """Validate a local video graph and report resource risks before queue submission."""
    object_info = _local_object_info()
    validation = validate_local_video_workflow(data.workflow, object_info)
    if not validation["valid"]:
        raise HTTPException(422, detail={"message": "工作流结构未通过预检，未提交任务。", **validation})
    diagnostics = local_video_runtime_diagnostics()
    assessment = assess_video_resource_preflight(data.workflow, diagnostics)
    return {
        "valid": True,
        "validation": validation,
        "resource_assessment": assessment,
        "message": "工作流结构已通过检查；请先阅读本机资源风险和建议，再决定是否提交。预检不会修改工作流或启动生成。",
    }


@router.get("/video-runtime/diagnostics")
def local_video_runtime_diagnostics():
    """Read-only local ComfyUI runtime and accelerator diagnostics; never downloads or queues work."""
    stats = _local_json("/system_stats")
    if not isinstance(stats, dict) or stats.get("_error"):
        return {
            "available": False,
            "engine": "ComfyUI",
            "reason": "无法读取本机 ComfyUI system_stats；请确认本地引擎已启动。",
            "devices": [],
            "local_only": True,
            "downloaded": False,
            "generation_submitted": False,
            "cloud_fallback": False,
        }
    devices = stats.get("devices") if isinstance(stats.get("devices"), list) else []
    normalized = []
    for device in devices[:16]:
        if not isinstance(device, dict):
            continue
        total = device.get("vram_total")
        free = device.get("vram_free")
        total_mb = round(total / (1024 * 1024)) if isinstance(total, (int, float)) and total >= 0 else None
        free_mb = round(free / (1024 * 1024)) if isinstance(free, (int, float)) and free >= 0 else None
        normalized.append({
            "name": str(device.get("name", "未知设备"))[:240],
            "type": str(device.get("type", "未知类型"))[:80],
            "vram_total_mb": total_mb,
            "vram_free_mb": free_mb,
            "torch_vram_total_mb": round(device.get("torch_vram_total", 0) / (1024 * 1024))
                if isinstance(device.get("torch_vram_total"), (int, float)) and device.get("torch_vram_total") >= 0 else None,
            "torch_vram_free_mb": round(device.get("torch_vram_free", 0) / (1024 * 1024))
                if isinstance(device.get("torch_vram_free"), (int, float)) and device.get("torch_vram_free") >= 0 else None,
        })
    return {
        "available": True,
        "engine": "ComfyUI",
        "system": {
            "os": str((stats.get("system") or {}).get("os", ""))[:120] if isinstance(stats.get("system"), dict) else "",
            "comfyui_version": str(stats.get("version", ""))[:120],
            "pytorch_version": str(stats.get("pytorch_version", ""))[:120],
            "embedded_python": bool(stats.get("embedded_python")),
        },
        "devices": normalized,
        "device_count": len(normalized),
        "assessment": (
            "ComfyUI 未报告可用加速设备；可能使用 CPU，实际性能需以任务运行测试为准。"
            if not normalized else
            "已读取设备和显存信息；这不代表 Wan/VACE 模型兼容或能够成功加载，仍需实际小尺寸测试。"
        ),
        "local_only": True,
        "downloaded": False,
        "generation_submitted": False,
        "cloud_fallback": False,
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
