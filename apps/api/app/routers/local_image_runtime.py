"""Read-only ComfyUI image-model discovery.

Only talks to the fixed loopback ComfyUI API. It does not download, install,
modify, or execute models/workflows.
"""
import json
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from fastapi import APIRouter

from app.routers.system import _get_hardware_profile

router = APIRouter(prefix="/api/local-ai", tags=["本地图片生成"])
COMFYUI_BASE_URL = "http://127.0.0.1:8188"
_OPENER = build_opener(ProxyHandler({}))
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
GIB = 1024 ** 3

# ComfyUI built-in/custom-node model selectors. Each endpoint is fixed and
# allow-listed; the API never accepts an arbitrary URL or node name from users.
MODEL_SELECTORS = {
    "checkpoint": ("CheckpointLoaderSimple", "ckpt_name"),
    "vae": ("VAELoader", "vae_name"),
    "lora": ("LoraLoader", "lora_name"),
    "upscaler": ("UpscaleModelLoader", "model_name"),
    "controlnet": ("ControlNetLoader", "control_net_name"),
    "clip_vision": ("CLIPVisionLoader", "clip_name"),
    "ip_adapter": ("IPAdapterModelLoader", "ipadapter_file"),
}


def _local_json(path: str, opener=None, timeout: float = 2.5):
    allowed = {"/system_stats"} | {
        f"/object_info/{node_type}" for node_type, _ in MODEL_SELECTORS.values()
    }
    if path not in allowed:
        raise ValueError("Unsupported ComfyUI endpoint")
    request = Request(COMFYUI_BASE_URL + path, headers={"Accept": "application/json"})
    client = opener or _OPENER
    try:
        with client.open(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError("本地 ComfyUI 响应超过安全读取上限")
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("本地 ComfyUI 返回格式无效")
        return payload
    except HTTPError as exc:
        return {"_error": f"HTTP {exc.code}"}
    except (URLError, TimeoutError, OSError):
        return None
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"_error": "本地 ComfyUI 返回了无法识别的数据"}


def _selector_choices(object_info, input_name):
    if not isinstance(object_info, dict) or object_info.get("_error"):
        return []
    # A single-node /object_info response is keyed by the node class.
    node_info = next(
        (value for value in object_info.values() if isinstance(value, dict) and "input" in value),
        None,
    )
    inputs = node_info.get("input") if isinstance(node_info, dict) else None
    if not isinstance(inputs, dict):
        return []
    for section in ("required", "optional"):
        fields = inputs.get(section)
        spec = fields.get(input_name) if isinstance(fields, dict) else None
        if isinstance(spec, list) and spec and isinstance(spec[0], list):
            return sorted({
                str(value)[:240] for value in spec[0]
                if isinstance(value, str) and value.strip()
            })[:500]
    return []


def _scan_comfy_models(opener=None):
    inventory = []
    node_status = {}
    for category, (node_type, input_name) in MODEL_SELECTORS.items():
        result = _local_json(f"/object_info/{node_type}", opener=opener)
        if result is None:
            node_status[node_type] = {"available": False, "reason": "ComfyUI 未响应"}
            continue
        if result.get("_error"):
            node_status[node_type] = {"available": False, "reason": result["_error"]}
            continue
        node_info = result.get(node_type)
        if not isinstance(node_info, dict):
            node_status[node_type] = {"available": False, "reason": "节点未安装或未注册"}
            continue
        choices = _selector_choices(result, input_name)
        node_status[node_type] = {
            "available": True,
            "model_count": len(choices),
            "models": choices,
        }
        inventory.extend({
            "name": name,
            "category": category,
            "source": "ComfyUI",
            "selector_node": node_type,
            "status": "listed",
            "load_tested": False,
        } for name in choices)
    # A file may be exposed by multiple nodes; keep each category association
    # but avoid duplicate records for the same selector and filename.
    unique = {}
    for item in inventory:
        unique[(item["category"], item["name"])] = item
    items = sorted(unique.values(), key=lambda item: (item["category"], item["name"].lower()))
    counts = {}
    for item in items:
        counts[item["category"]] = counts.get(item["category"], 0) + 1
    return {
        "items": items,
        "total": len(items),
        "counts_by_category": counts,
        "selectors": node_status,
        "scan_scope": "ComfyUI 已注册节点向本机 API 暴露的模型下拉列表",
        "note": "列出表示 ComfyUI 节点能看到该模型，不代表权重已成功加载或推理通过；load_tested=false。",
    }


def _checkpoint_names(object_info):
    # Kept for existing callers and tests.
    if not isinstance(object_info, dict) or object_info.get("_error"):
        return []
    return _selector_choices(object_info, "ckpt_name")


def assess_image_hardware(hardware: dict):
    memory = hardware.get("memory") or {}
    gpu = hardware.get("gpu") or {}
    ram = memory.get("total_bytes")
    vram = gpu.get("vram_bytes")
    reasons = []
    if ram is None:
        reasons.append("无法读取系统内存，建议先人工确认内存容量。")
    elif ram < 16 * GIB:
        reasons.append("系统内存低于 16 GiB，大型图片工作流可能出现内存不足。")
    else:
        reasons.append("系统内存达到 16 GiB 基础建议；复杂工作流仍可能需要更多内存。")

    if vram is None:
        vendor = str(gpu.get("vendor") or "").upper()
        gpu_status = gpu.get("status")
        if gpu.get("shared_memory"):
            level = "integrated_shared_memory"
            reasons.append(
                f"已识别到 {vendor or '集成'} 集成显卡，但未确认独立显存；它可能与系统内存共享。"
                "不能把共享内存容量当作独立显存，也不能仅凭设备名称确认 ComfyUI 可用 GPU 后端。"
            )
        elif gpu_status == "detected" and vendor in {"AMD", "INTEL"}:
            level = "detected_no_vram"
            reasons.append(
                f"已识别到 {vendor} 显卡，但当前没有可靠的独立显存数据。"
                "请以 ComfyUI /system_stats 实际设备列表和一次成功推理为准。"
            )
        else:
            level = "unknown"
            reasons.append("未读取到可靠的显存信息；可能使用 AMD/Intel 显卡、CPU 或未安装驱动，不能据此判断没有 GPU。")
    elif vram < 4 * GIB:
        level = "limited"
        reasons.append("显存低于 4 GiB，现代扩散模型可能无法顺畅运行；建议先使用轻量模型并降低分辨率。")
    elif vram < 8 * GIB:
        level = "basic"
        reasons.append("显存为 4–8 GiB，建议从轻量/低显存工作流开始，SDXL 等大型工作流可能受限。")
    elif vram < 12 * GIB:
        level = "capable"
        reasons.append("显存为 8–12 GiB，可尝试更多常见工作流，但模型、分辨率和批量大小仍会影响结果。")
    else:
        level = "strong"
        reasons.append("显存达到 12 GiB 以上，具备尝试较复杂工作流的条件；仍需实际测试。")

    return {
        "ram_bytes": ram,
        "vram_bytes": vram,
        "gpu_vendor": gpu.get("vendor"),
        "gpu_name": gpu.get("name"),
        "gpu_shared_memory": bool(gpu.get("shared_memory")),
        "gpu_level": level,
        "reasons": reasons,
        "note": "以上是保守的初步建议，不是硬件兼容保证。实际速度和可运行模型取决于显卡类型、驱动、量化方式、模型架构、分辨率和工作流。",
    }


@router.get("/image-runtime")
def local_image_runtime():
    """Discover ComfyUI and enumerate its visible image-related model selectors."""
    stats = _local_json("/system_stats")
    hardware = assess_image_hardware(_get_hardware_profile())
    if stats is None:
        return {
            "engine": "comfyui",
            "status": "not_running",
            "base_url": COMFYUI_BASE_URL,
            "checkpoint_count": 0,
            "checkpoints": [],
            "model_inventory": {"items": [], "total": 0, "counts_by_category": {}, "selectors": {}},
            "hardware": hardware,
            "network_used": False,
            "downloaded": False,
            "generation_submitted": False,
            "cloud_fallback": False,
            "message": "未检测到本机 ComfyUI（127.0.0.1:8188）。本次只做检测，不会自动安装、启动或下载模型。",
        }
    if stats.get("_error"):
        return {
            "engine": "comfyui", "status": "error", "base_url": COMFYUI_BASE_URL,
            "checkpoint_count": 0, "checkpoints": [],
            "model_inventory": {"items": [], "total": 0, "counts_by_category": {}, "selectors": {}},
            "hardware": hardware,
            "network_used": False, "downloaded": False,
            "generation_submitted": False, "cloud_fallback": False,
            "message": "本机 ComfyUI 返回异常：" + str(stats["_error"]),
        }

    inventory = _scan_comfy_models()
    checkpoints = [
        item["name"] for item in inventory["items"]
        if item["category"] == "checkpoint"
    ]
    # ComfyUI /system_stats returns devices as a top-level sibling of system.
    # Reading system.devices silently reported zero devices on real installations.
    devices = stats.get("devices") if isinstance(stats.get("devices"), list) else []
    return {
        "engine": "comfyui",
        "status": "ready",
        "base_url": COMFYUI_BASE_URL,
        "checkpoint_count": len(checkpoints),
        "checkpoints": checkpoints,
        "model_inventory": inventory,
        "device_count": len(devices),
        "hardware": hardware,
        "network_used": False,
        "downloaded": False,
        "generation_submitted": False,
        "cloud_fallback": False,
        "message": "已连接本机 ComfyUI，并扫描其已注册节点可见的图片相关模型。",
        "limitations": "节点下拉列表可见不代表权重已成功加载；需在对应工作流中完成实际推理才能标记为可用。",
    }
