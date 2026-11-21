"""Read-only ComfyUI local image runtime discovery.

This module probes only the fixed loopback ComfyUI endpoint. It never downloads
weights, submits prompts, starts processes, or contacts a cloud provider.
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


def _local_json(path: str, opener=None, timeout: float = 2.5):
    if path not in {"/system_stats", "/object_info/CheckpointLoaderSimple"}:
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


def _checkpoint_names(object_info):
    if not isinstance(object_info, dict) or object_info.get("_error"):
        return []
    node = object_info.get("CheckpointLoaderSimple")
    if not isinstance(node, dict):
        return []
    inputs = node.get("input")
    required = inputs.get("required") if isinstance(inputs, dict) else None
    checkpoint = required.get("ckpt_name") if isinstance(required, dict) else None
    if isinstance(checkpoint, list) and checkpoint and isinstance(checkpoint[0], list):
        return [str(name)[:240] for name in checkpoint[0][:200] if isinstance(name, str)]
    return []


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
        level = "unknown"
        reasons.append("未读取到 NVIDIA 显存信息；可能使用其他品牌显卡或 CPU，不能据此判断没有 GPU。")
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
        "gpu_level": level,
        "reasons": reasons,
        "note": "以上是保守的初步建议，不是硬件兼容保证。实际速度和可运行模型取决于显卡类型、驱动、量化方式、模型架构、分辨率和工作流。",
    }


@router.get("/image-runtime")
def local_image_runtime():
    """Discover a locally running ComfyUI instance and already-installed checkpoints."""
    stats = _local_json("/system_stats")
    if stats is None:
        return {
            "engine": "comfyui",
            "status": "not_running",
            "base_url": COMFYUI_BASE_URL,
            "checkpoint_count": 0,
            "checkpoints": [],
            "hardware": assess_image_hardware(_get_hardware_profile()),
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
            "hardware": assess_image_hardware(_get_hardware_profile()),
            "network_used": False, "downloaded": False,
            "generation_submitted": False, "cloud_fallback": False,
            "message": "本机 ComfyUI 返回异常：" + str(stats["_error"]),
        }

    object_info = _local_json("/object_info/CheckpointLoaderSimple")
    checkpoints = _checkpoint_names(object_info)
    devices = []
    system = stats.get("system")
    if isinstance(system, dict):
        devices = system.get("devices") if isinstance(system.get("devices"), list) else []
    return {
        "engine": "comfyui",
        "status": "ready",
        "base_url": COMFYUI_BASE_URL,
        "checkpoint_count": len(checkpoints),
        "checkpoints": checkpoints,
        "device_count": len(devices),
        "hardware": assess_image_hardware(_get_hardware_profile()),
        "network_used": False,
        "downloaded": False,
        "generation_submitted": False,
        "cloud_fallback": False,
        "message": "已连接本机 ComfyUI；仅列出已安装 checkpoint，不会提交生成任务或下载模型。",
        "limitations": "尚未导入生成工作流；检测成功不代表已安装可用模型或已完成图片生成。",
    }
