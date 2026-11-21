"""Read-only preflight planner for a future bundled ComfyUI installer.

This endpoint does not download, install, launch, or modify anything. It reports
hardware and storage constraints before the user chooses an installation plan.
"""
import shutil

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

GITHUB_RELEASE_API = "https://api.github.com/repos/Comfy-Org/ComfyUI/releases/latest"
MAX_RELEASE_METADATA_BYTES = 5 * 1024 * 1024
OFFICIAL_ASSET_PREFIX = "ComfyUI_windows_portable_"
ALLOWED_ASSET_NAMES = {
    "ComfyUI_windows_portable_nvidia.7z",
    "ComfyUI_windows_portable_nvidia_cu126.7z",
    "ComfyUI_windows_portable_amd.7z",
    "ComfyUI_windows_portable_intel.7z",
}


def parse_official_release(payload: dict):
    if not isinstance(payload, dict) or not isinstance(payload.get("tag_name"), str):
        raise ValueError("GitHub release metadata is invalid")
    assets = payload.get("assets")
    if not isinstance(assets, list):
        raise ValueError("GitHub release asset list is invalid")
    result = []
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = asset.get("name")
        url = asset.get("browser_download_url")
        size_bytes = asset.get("size")
        if name not in ALLOWED_ASSET_NAMES:
            continue
        if not isinstance(url, str) or not url.startswith(
            "https://github.com/Comfy-Org/ComfyUI/releases/download/"
        ):
            continue
        if not isinstance(size_bytes, int) or size_bytes <= 0:
            continue
        digest = asset.get("digest")
        if digest is not None and (
            not isinstance(digest, str) or not digest.startswith("sha256:")
        ):
            digest = None
        result.append({
            "name": name,
            "download_url": url,
            "size_bytes": size_bytes,
            "sha256": digest[7:] if isinstance(digest, str) and digest.startswith("sha256:") else None,
            "digest_available": bool(isinstance(digest, str) and digest.startswith("sha256:")),
        })
    result.sort(key=lambda item: item["name"])
    return {
        "version": payload["tag_name"][:100],
        "published_at": str(payload.get("published_at") or "")[:40],
        "assets": result,
        "source": GITHUB_RELEASE_API,
        "download_started": False,
        "installation_started": False,
        "note": "仅读取 Comfy-Org 官方 GitHub Release 元数据，没有下载或安装文件。若官方未提供 SHA-256 摘要，后续安装器必须停止自动安装并要求可信校验来源。",
    }


def fetch_official_release_catalog(opener=urlopen):
    request = Request(
        GITHUB_RELEASE_API,
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "AI-Comic-Studio-ComfyUI-Installer",
        },
    )
    try:
        with opener(request, timeout=12) as response:
            raw = response.read(MAX_RELEASE_METADATA_BYTES + 1)
        if len(raw) > MAX_RELEASE_METADATA_BYTES:
            raise ValueError("GitHub release metadata exceeds the size limit")
        payload = json.loads(raw.decode("utf-8"))
        return parse_official_release(payload)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise RuntimeError("无法读取 Comfy-Org 官方发布信息，请检查网络后重试。") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("官方发布信息格式无效，已停止读取。") from exc


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

@router.get("/release-catalog")
def comfyui_official_release_catalog():
    """Fetch official release metadata only; never download or install release assets."""
    from fastapi import HTTPException
    try:
        return fetch_official_release_catalog()
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
