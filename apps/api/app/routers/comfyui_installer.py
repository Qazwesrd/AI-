"""Read-only preflight planner for a future bundled ComfyUI installer.

This endpoint does not download, install, launch, or modify anything. It reports
hardware and storage constraints before the user chooses an installation plan.
"""
import shutil
from pathlib import Path

from fastapi import APIRouter

from app.routers.system import _get_hardware_profile

router = APIRouter(prefix="/api/local-ai/comfyui-installer", tags=["本地图片引擎安装"])

GIB = 1024 ** 3
# Conservative planning reserves, not claims about a specific release archive.
ENGINE_RESERVE_BYTES = 8 * GIB
MODEL_RESERVE_BYTES = 15 * GIB


def assess_comfyui_install(hardware: dict, free_disk_bytes: int | None):
    memory = hardware.get("memory") or {}
    gpu = hardware.get("gpu") or {}
    ram = memory.get("total_bytes")
    vram = gpu.get("vram_bytes")
    blockers = []
    warnings = []

    if free_disk_bytes is None:
        warnings.append("无法读取安装目标磁盘的剩余空间，请在安装前手动确认。")
    elif free_disk_bytes < ENGINE_RESERVE_BYTES:
        blockers.append("可用空间不足 8 GiB，暂不建议安装图片生成引擎。")
    elif free_disk_bytes < ENGINE_RESERVE_BYTES + MODEL_RESERVE_BYTES:
        warnings.append("空间可安装基础引擎，但不足以同时预留一个大型基础模型；建议选择轻量模型或更换磁盘。")

    if ram is None:
        warnings.append("无法读取系统内存，需人工确认硬件条件。")
    elif ram < 16 * GIB:
        warnings.append("系统内存低于 16 GiB，建议使用轻量模型、较低分辨率和较小批量。")

    if vram is None:
        gpu_profile = "unknown"
        warnings.append("未读取到 NVIDIA 显存数据；不代表没有 AMD、Intel 或其他 GPU。")
    elif vram < 4 * GIB:
        gpu_profile = "low"
        warnings.append("显存低于 4 GiB，现代扩散模型可能受限，建议先做兼容性实测。")
    elif vram < 8 * GIB:
        gpu_profile = "entry"
        warnings.append("显存为 4–8 GiB，优先考虑轻量/低显存模型与较低分辨率。")
    elif vram < 12 * GIB:
        gpu_profile = "mid"
        warnings.append("显存为 8–12 GiB，可尝试常见模型，但仍需按具体模型实测。")
    else:
        gpu_profile = "high"
        warnings.append("显存达到 12 GiB 以上，可尝试更复杂工作流；不构成性能保证。")

    return {
        "status": "blocked" if blockers else ("needs_manual_check" if ram is None or free_disk_bytes is None else "review"),
        "can_install_engine": not blockers,
        "hardware_profile": gpu_profile,
        "ram_bytes": ram,
        "vram_bytes": vram,
        "free_disk_bytes": free_disk_bytes,
        "engine_disk_reserve_bytes": ENGINE_RESERVE_BYTES,
        "optional_model_disk_reserve_bytes": MODEL_RESERVE_BYTES,
        "recommended_total_reserve_bytes": ENGINE_RESERVE_BYTES + MODEL_RESERVE_BYTES,
        "blockers": blockers,
        "warnings": warnings,
        "download_started": False,
        "installation_started": False,
        "network_used": False,
        "cloud_fallback": False,
        "note": "磁盘预留值是安装规划缓冲，不是已选定安装包或模型的精确下载大小。正式安装前必须展示具体版本、来源、下载量、校验值和目标磁盘空间，并再次取得用户确认。",
    }


@router.get("/readiness")
def comfyui_install_readiness():
    hardware = _get_hardware_profile()
    home = Path.home()
    disk_root = home
    while not disk_root.exists() and disk_root != disk_root.parent:
        disk_root = disk_root.parent
    try:
        free_disk = shutil.disk_usage(disk_root).free
    except OSError:
        free_disk = None
    result = assess_comfyui_install(hardware, free_disk)
    result.update({
        "engine": "ComfyUI",
        "install_target": str(home / "AIComicStudio" / "ComfyUI"),
        "hardware": hardware,
        "read_only": True,
    })
    return result
