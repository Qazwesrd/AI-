"""Read-only hardware readiness assessment for the bundled Qwen3 4B workflow."""
import os
import platform
import re
import shutil
from pathlib import Path

from fastapi import APIRouter

from app.routers.system import _get_hardware_profile
from app.routers.local_model_installer import MODEL_NAME, REQUIRED_FREE_BYTES, _model_directory, _ollama_models

router = APIRouter(prefix="/api/local-ai", tags=["本地 AI 硬件适配"])

MODEL_BYTES_ESTIMATE = 2_500_000_000
GIB = 1024 ** 3


def _windows_compatibility(system_name: str, version: str):
    if system_name != "Windows":
        return {
            "supported": None,
            "status": "not_windows",
            "message": "当前自动安装流程仅支持 Windows 桌面版；硬件信息仍可供参考。",
        }
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", version or "")
    if not match:
        return {"supported": None, "status": "unknown", "message": "无法读取 Windows 版本，建议先更新系统。"}
    major, minor, build = map(int, match.groups())
    supported = major > 10 or (major == 10 and minor == 0 and build >= 19045)
    return {
        "supported": supported,
        "status": "supported" if supported else "update_required",
        "build": build,
        "message": "系统版本符合 Ollama Windows 10 22H2+ 要求。" if supported else "建议升级到 Windows 10 22H2（Build 19045）或更新版本后再安装 Ollama。",
    }


def assess_qwen3_4b(hardware: dict, free_disk_bytes: int | None, system_name: str, version: str):
    memory = hardware.get("memory", {})
    gpu = hardware.get("gpu", {})
    ram = memory.get("total_bytes")
    vram = gpu.get("vram_bytes")
    cpu = hardware.get("cpu", {})
    cores = cpu.get("logical_cores")
    os_check = _windows_compatibility(system_name, version)

    reasons = []
    blockers = []
    if os_check["status"] == "update_required":
        blockers.append(os_check["message"])
    elif os_check["status"] == "unknown":
        reasons.append(os_check["message"])

    if free_disk_bytes is None:
        reasons.append("无法读取模型存储位置的剩余空间，请手动确认至少 6 GiB 可用。")
    elif free_disk_bytes < REQUIRED_FREE_BYTES:
        blockers.append("模型存储位置可用空间不足 6 GiB。")

    if ram is None:
        reasons.append("无法读取物理内存，不能可靠判断模型是否适合。")
    elif ram < 8 * GIB:
        blockers.append("系统内存低于 8 GiB，不建议安装并运行 Qwen3 4B。")
    elif ram < 16 * GIB:
        reasons.append("系统内存达到基础建议值，但多任务或较长上下文可能吃紧。")
    else:
        reasons.append("系统内存达到较舒适的建议范围。")

    gpu_vendor = gpu.get("vendor")
    gpu_name = gpu.get("name")
    if vram is not None and gpu_vendor == "NVIDIA":
        if vram >= 6 * GIB:
            reasons.append("检测到 NVIDIA 显存达到 6 GiB，可尝试 GPU 加速；实际还取决于驱动和 Ollama 支持，尚未进行推理基准测试。")
        elif vram >= 4 * GIB:
            reasons.append("检测到 NVIDIA 显卡，但显存较紧张，模型可能部分或全部使用系统内存。")
        else:
            reasons.append("检测到的 NVIDIA 显存不足 4 GiB，不建议依赖 GPU 完整加载模型。")
    elif gpu_vendor in {"AMD", "Intel"}:
        reasons.append(
            f"检测到 {gpu_vendor} 显示适配器（{gpu_name or '型号未知'}）；"
            "当前安装流程没有验证该设备的 Ollama GPU 后端，按 CPU 推理方案评估，不承诺 GPU 加速。"
        )
    else:
        reasons.append("未能读取 NVIDIA 显卡信息；这不代表电脑没有其他品牌显卡，先按 CPU/内存方案评估。")

    if cores is not None and cores < 4:
        reasons.append("逻辑处理器少于 4 个，CPU 推理体验可能较慢。")

    if blockers:
        status = "not_recommended"
        label = "暂不建议安装"
    elif ram is None or free_disk_bytes is None or os_check["status"] == "unknown":
        status = "needs_manual_check"
        label = "需要人工确认"
    elif reasons and ram < 16 * GIB:
        status = "basic"
        label = "可尝试基础运行"
    else:
        status = "recommended"
        label = "硬件条件较合适"

    return {
        "model": MODEL_NAME,
        "status": status,
        "label": label,
        "estimated_model_download_bytes": MODEL_BYTES_ESTIMATE,
        "required_free_disk_bytes": REQUIRED_FREE_BYTES,
        "free_disk_bytes": free_disk_bytes,
        "ram_bytes": ram,
        "vram_bytes": vram,
        "logical_cores": cores,
        "operating_system": os_check,
        "reasons": reasons,
        "blockers": blockers,
        "network_used": False,
        "recommended_inference_mode": (
            "gpu_candidate" if gpu_vendor == "NVIDIA" and vram is not None and vram >= 6 * GIB else "cpu"
        ),
        "gpu_acceleration_verified": False,
        "no_stress_test": True,
        "note": "这是安装前的静态初筛，不是实际推理性能测试或运行保证。模型文件大小不等于实际内存需求；量化、上下文长度、系统占用、GPU 驱动和显存共享都会影响结果。",
    }


@router.get("/hardware-readiness")
def local_model_hardware_readiness():
    hardware = _get_hardware_profile()
    model_dir = _model_directory()
    disk_root = model_dir
    while not disk_root.exists() and disk_root != disk_root.parent:
        disk_root = disk_root.parent
    try:
        free_disk = shutil.disk_usage(disk_root).free
    except OSError:
        free_disk = None

    system_name = platform.system()
    version = platform.version()
    assessment = assess_qwen3_4b(hardware, free_disk, system_name, version)
    models = _ollama_models()
    assessment.update({
        "hardware": hardware,
        "model_directory": str(model_dir),
        "ollama_running": models is not None,
        "ollama_model_installed": (
            any(isinstance(item, dict) and item.get("name") == MODEL_NAME for item in models)
            if models is not None else False
        ),
        "network_used": False,
        "automatic_download": False,
        "cloud_fallback": False,
    })
    return assessment
