"""Read-only preflight planner for a future bundled ComfyUI installer.

This endpoint does not download, install, launch, or modify anything. It reports
hardware and storage constraints before the user chooses an installation plan.
"""
import json
import os
import platform
import re
import shutil
import subprocess
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
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



def recommend_official_asset(vendor: str, gpu_name: str | None = None, architecture: str | None = None):
    """Return a conservative asset filename suggestion; unknown hardware stays manual."""
    arch = (architecture or "").lower().replace("-", "").replace("_", "")
    if arch not in {"amd64", "x8664", "x64"}:
        return None
    if vendor == "NVIDIA":
        label = (gpu_name or "").lower()
        # Official guidance uses cu126 for GTX 10-series and older.
        match = re.search(r"(?:gtx|geforce)\s*([0-9]{3,4})", label)
        if match and int(match.group(1)) <= 1099:
            return "ComfyUI_windows_portable_nvidia_cu126.7z"
        return "ComfyUI_windows_portable_nvidia.7z"
    if vendor == "AMD":
        return "ComfyUI_windows_portable_amd.7z"
    if vendor == "Intel":
        return "ComfyUI_windows_portable_intel.7z"
    return None

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
        if not isinstance(url, str):
            continue
        parsed_url = urlsplit(url)
        expected_path = f"/Comfy-Org/ComfyUI/releases/download/{payload['tag_name']}/{name}"
        if (
            parsed_url.scheme != "https"
            or parsed_url.hostname != "github.com"
            or parsed_url.port is not None
            or parsed_url.username is not None
            or parsed_url.password is not None
            or parsed_url.path != expected_path
            or parsed_url.query
            or parsed_url.fragment
        ):
            continue
        if not isinstance(size_bytes, int) or isinstance(size_bytes, bool) or size_bytes <= 0:
            continue
        digest = asset.get("digest")
        if not (
            isinstance(digest, str)
            and re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest)
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


def classify_gpu_vendor(name: str | None, pnp_device_id: str | None = None) -> str:
    """Classify a display adapter conservatively; never infer VRAM from AdapterRAM."""
    device_id = (pnp_device_id or "").upper()
    if "VEN_10DE" in device_id:
        return "NVIDIA"
    if "VEN_1002" in device_id or "VEN_1022" in device_id:
        return "AMD"
    if "VEN_8086" in device_id:
        return "Intel"
    label = (name or "").lower()
    if "nvidia" in label or "geforce" in label or "quadro" in label:
        return "NVIDIA"
    if "amd" in label or "radeon" in label:
        return "AMD"
    if "intel" in label or "arc graphics" in label:
        return "Intel"
    return "unknown"


def parse_windows_video_adapters(payload):
    """Parse fixed PowerShell CIM output without trusting reported AdapterRAM."""
    if isinstance(payload, dict):
        entries = [payload]
    elif isinstance(payload, list):
        entries = payload
    else:
        return []
    adapters = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get("Name")
        pnp_id = entry.get("PNPDeviceID")
        if not isinstance(name, str) or not name.strip():
            continue
        adapters.append({
            "name": name.strip()[:200],
            "vendor": classify_gpu_vendor(name, pnp_id if isinstance(pnp_id, str) else None),
            "vram_bytes": None,
            "recommended_asset": recommend_official_asset(classify_gpu_vendor(name, pnp_id if isinstance(pnp_id, str) else None), name, platform.machine()),
            "vram_note": "Windows CIM 的 AdapterRAM 字段可能被 32 位截断，因此不作为显存依据；显存需由对应厂商工具单独验证。",
        })
    return adapters


def detect_windows_video_adapters():
    """Read display-adapter names locally on Windows; no network, install, or mutation."""
    if os.name != "nt":
        return {"status": "unsupported_platform", "adapters": [], "network_used": False}
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if not powershell:
        return {"status": "unavailable", "adapters": [], "network_used": False}
    command = (
        "Get-CimInstance -ClassName Win32_VideoController | "
        "Select-Object Name,PNPDeviceID | ConvertTo-Json -Compress"
    )
    try:
        result = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, text=True, timeout=5, check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return {"status": "unavailable", "adapters": [], "network_used": False}
        parsed = json.loads(result.stdout)
        adapters = parse_windows_video_adapters(parsed)
        return {
            "status": "detected" if adapters else "not_detected",
            "adapters": adapters,
            "network_used": False,
        }
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return {"status": "unavailable", "adapters": [], "network_used": False}

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
        "gpu_adapters": detect_windows_video_adapters(),
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


# The following limits are guardrails for a future installer, not an active
# downloader. No endpoint currently invokes these checks or transfers files.
MAX_ENGINE_ARCHIVE_BYTES = 12 * GIB
MAX_ARCHIVE_ENTRIES = 100_000
MAX_ARCHIVE_MEMBER_BYTES = 20 * GIB
MAX_ARCHIVE_TOTAL_UNPACKED_BYTES = 80 * GIB


def validate_official_asset_for_install(asset: dict, release_tag: str):
    """Fail closed unless an asset has an exact official URL, size, and SHA-256."""
    if not isinstance(asset, dict) or not isinstance(release_tag, str) or not release_tag:
        raise ValueError("安装包信息无效")
    name = asset.get("name")
    if name not in ALLOWED_ASSET_NAMES:
        raise ValueError("安装包名称不在官方允许列表中")
    url = asset.get("download_url") or asset.get("browser_download_url")
    if not isinstance(url, str):
        raise ValueError("缺少官方安装包下载地址")
    parsed = urlsplit(url)
    expected_path = f"/Comfy-Org/ComfyUI/releases/download/{release_tag}/{name}"
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or parsed.port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != expected_path
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("下载地址不是匹配版本的 Comfy-Org 官方 Release 资产")
    size_bytes = asset.get("size_bytes", asset.get("size"))
    if not isinstance(size_bytes, int) or isinstance(size_bytes, bool) or not 0 < size_bytes <= MAX_ENGINE_ARCHIVE_BYTES:
        raise ValueError("安装包大小缺失或超出安全上限")
    digest = asset.get("sha256")
    if digest is None and isinstance(asset.get("digest"), str) and asset["digest"].startswith("sha256:"):
        digest = asset["digest"][7:]
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
        raise ValueError("官方资产缺少有效 SHA-256，已停止自动安装")
    return {"name": name, "url": url, "size_bytes": size_bytes, "sha256": digest.lower()}


def validate_download_response(status_code: int, headers: dict, expected_size: int,
                                resume_offset: int = 0, expected_etag: str | None = None):
    """Validate HTTP response metadata before a future downloader writes bytes."""
    if not isinstance(headers, dict) or not isinstance(expected_size, int):
        raise ValueError("下载响应信息无效")
    normalized = {str(key).lower(): str(value).strip() for key, value in headers.items()}
    if "content-encoding" in normalized and normalized["content-encoding"].lower() not in {"", "identity"}:
        raise ValueError("服务器返回了非 identity 编码，不能安全校验文件长度")
    if resume_offset < 0 or resume_offset >= expected_size:
        if resume_offset != 0 or expected_size <= 0:
            raise ValueError("断点续传位置无效")
    if resume_offset == 0:
        if status_code != 200:
            raise ValueError("首次下载必须返回 HTTP 200")
        expected_length = expected_size
    else:
        if status_code != 206:
            raise ValueError("断点续传必须返回 HTTP 206")
        content_range = normalized.get("content-range", "")
        expected_range = f"bytes {resume_offset}-{expected_size - 1}/{expected_size}"
        if content_range != expected_range:
            raise ValueError("服务器返回的 Content-Range 与本地断点不一致")
        etag = normalized.get("etag")
        if not expected_etag or not etag or etag != expected_etag:
            raise ValueError("断点续传的 ETag 不匹配，必须重新下载")
        expected_length = expected_size - resume_offset
    content_length = normalized.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) != expected_length:
                raise ValueError("服务器声明的文件长度与官方资产大小不一致")
        except (TypeError, ValueError) as exc:
            if isinstance(exc, ValueError) and str(exc).startswith("服务器声明"):
                raise
            raise ValueError("服务器 Content-Length 无效") from exc
    return {"expected_response_bytes": expected_length, "resume_allowed": resume_offset > 0}


def validate_archive_entries(entries):
    """Reject unsafe archive manifests before extraction; does not extract files."""
    if not isinstance(entries, list) or len(entries) > MAX_ARCHIVE_ENTRIES:
        raise ValueError("压缩包文件数量无效或超过安全上限")
    total_size = 0
    checked = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("压缩包目录项格式无效")
        raw_path = entry.get("path")
        size = entry.get("size")
        if not isinstance(raw_path, str) or not raw_path or "\x00" in raw_path:
            raise ValueError("压缩包包含无效路径")
        # Check both path syntaxes because archives may be crafted for Windows.
        windows_path = __import__("pathlib").PureWindowsPath(raw_path)
        normalized = raw_path.replace("\\", "/")
        parts = normalized.split("/")
        if (
            raw_path.startswith(("/", "\\"))
            or windows_path.is_absolute() or bool(windows_path.root)
            or windows_path.drive
            or any(part in {"", ".", ".."} for part in parts)
            or ":" in raw_path
        ):
            raise ValueError("压缩包包含绝对路径、盘符或路径穿越")
        if entry.get("is_symlink") or entry.get("is_hardlink"):
            raise ValueError("压缩包包含不允许的链接文件")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0 or size > MAX_ARCHIVE_MEMBER_BYTES:
            raise ValueError("压缩包单个文件大小无效或超过安全上限")
        total_size += size
        if total_size > MAX_ARCHIVE_TOTAL_UNPACKED_BYTES:
            raise ValueError("压缩包解压总大小超过安全上限")
        checked.append({"path": "/".join(parts), "size": size})
    return checked



def build_comfyui_install_plan(asset: dict, release_tag: str, free_disk_bytes: int | None,
                              install_target: str):
    """Build a reviewed plan only; it never downloads, extracts, or launches files."""
    verified = validate_official_asset_for_install(asset, release_tag)
    if not isinstance(install_target, str) or not install_target:
        raise ValueError("安装目标目录无效")
    # Keep the archive until its digest is verified, then extract into a staging
    # directory. Reserve both archive bytes and conservative engine workspace.
    required_free_bytes = verified["size_bytes"] + ENGINE_RESERVE_BYTES
    if free_disk_bytes is None:
        raise ValueError("无法读取目标磁盘剩余空间，不能生成安装计划")
    if free_disk_bytes < required_free_bytes:
        raise ValueError(
            f"目标磁盘空间不足：需要至少 {required_free_bytes} 字节，当前仅有 {free_disk_bytes} 字节"
        )
    return {
        "status": "plan_ready",
        "version": release_tag,
        "asset_name": verified["name"],
        "download_url": verified["url"],
        "archive_size_bytes": verified["size_bytes"],
        "sha256": verified["sha256"],
        "install_target": install_target,
        "required_free_bytes": required_free_bytes,
        "free_disk_bytes": free_disk_bytes,
        "download_started": False,
        "installation_started": False,
        "network_used": False,
        "note": "安装计划已生成，但尚未下载、解压或启动。真正下载前还需单独确认；下载完成后必须重新计算 SHA-256 并通过校验。",
    }


@router.post("/install-plan")
def comfyui_install_plan(payload: dict):
    """Create a disk-checked install plan after an explicit user confirmation."""
    from fastapi import HTTPException
    if not isinstance(payload, dict) or payload.get("confirm_plan") is not True:
        raise HTTPException(status_code=400, detail="需要先明确确认生成安装计划")
    asset_name = payload.get("asset_name")
    if not isinstance(asset_name, str) or asset_name not in ALLOWED_ASSET_NAMES:
        raise HTTPException(status_code=400, detail="请选择官方列表中的 Windows 安装包")
    try:
        catalog = fetch_official_release_catalog()
        asset = next((item for item in catalog["assets"] if item["name"] == asset_name), None)
        if asset is None:
            raise ValueError("官方最新版本中没有该安装包，或缺少可信 SHA-256")
        home = Path.home()
        disk_root = home
        while not disk_root.exists() and disk_root != disk_root.parent:
            disk_root = disk_root.parent
        free_disk = shutil.disk_usage(disk_root).free
        return build_comfyui_install_plan(
            asset, catalog["version"], free_disk,
            str(home / "AIComicStudio" / "ComfyUI"),
        )
    except (RuntimeError, ValueError, OSError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


DOWNLOAD_CHUNK_BYTES = 1024 * 1024
ALLOWED_DOWNLOAD_HOSTS = {"github.com", "release-assets.githubusercontent.com"}


class DownloadCancelled(Exception):
    """Raised when the user cancels a local asset download."""


def validate_download_destination(destination, install_root):
    """Ensure the archive is written only inside the dedicated ComfyUI staging area."""
    from pathlib import Path
    root = Path(install_root).resolve()
    target = Path(destination).resolve()
    if target == root or root not in target.parents:
        raise ValueError("下载目标必须位于 ComfyUI 安装暂存目录内")
    if target.suffix.lower() != ".7z" or target.name not in ALLOWED_ASSET_NAMES:
        raise ValueError("下载目标文件名不在官方安装包允许列表中")
    return target


def download_verified_official_asset(asset, release_tag, install_root, destination,
                                     cancel_event=None, progress_callback=None,
                                     opener=urlopen):
    """Stream a verified official archive to a staging file and atomically publish it.

    This helper is deliberately not wired to an API endpoint yet. Callers must
    obtain a separate explicit user confirmation before invoking it.
    """
    import hashlib
    import threading
    from pathlib import Path
    from urllib.error import HTTPError, URLError
    from urllib.request import HTTPRedirectHandler, build_opener

    verified = validate_official_asset_for_install(asset, release_tag)
    root = Path(install_root).resolve()
    target = validate_download_destination(destination, root)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    if partial.exists():
        raise ValueError("检测到未完成的下载文件；当前版本尚不支持续传，请先在界面中清理后重试")
    if target.exists():
        raise ValueError("目标安装包已存在，请先执行完整性检查或清理旧文件")

    class OfficialRedirectOnly(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            parsed = urlsplit(newurl)
            if (
                parsed.scheme != "https"
                or parsed.hostname not in ALLOWED_DOWNLOAD_HOSTS
                or parsed.username is not None
                or parsed.password is not None
                or parsed.port is not None
            ):
                raise ValueError("下载重定向目标不在官方允许域名内")
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    if opener is urlopen:
        safe_opener = build_opener(OfficialRedirectOnly())
        open_response = lambda request, timeout: safe_opener.open(request, timeout=timeout)
    else:
        open_response = opener

    request = Request(
        verified["url"],
        headers={
            "Accept-Encoding": "identity",
            "User-Agent": "AI-Comic-Studio-ComfyUI-Installer",
        },
    )
    cancel_event = cancel_event or threading.Event()
    digest = hashlib.sha256()
    received = 0
    try:
        with open_response(request, timeout=30) as response:
            final_url = response.geturl()
            final = urlsplit(final_url)
            if final.scheme != "https" or final.hostname not in ALLOWED_DOWNLOAD_HOSTS:
                raise ValueError("最终下载地址不是允许的官方 HTTPS 资源地址")
            metadata = validate_download_response(
                getattr(response, "status", response.getcode()),
                dict(response.headers.items()),
                verified["size_bytes"],
            )
            with partial.open("xb") as output:
                while True:
                    if cancel_event.is_set():
                        raise DownloadCancelled("用户已取消 ComfyUI 下载")
                    chunk = response.read(DOWNLOAD_CHUNK_BYTES)
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > verified["size_bytes"]:
                        raise ValueError("下载数据超过官方声明的文件大小")
                    output.write(chunk)
                    digest.update(chunk)
                    if progress_callback:
                        progress_callback({
                            "status": "downloading",
                            "received_bytes": received,
                            "total_bytes": verified["size_bytes"],
                            "percent": min(99, int(received * 100 / verified["size_bytes"])),
                        })
                output.flush()
                os.fsync(output.fileno())
        if received != verified["size_bytes"] or received != metadata["expected_response_bytes"]:
            raise ValueError("下载文件长度与官方声明不一致")
        if digest.hexdigest().lower() != verified["sha256"]:
            raise ValueError("下载文件 SHA-256 校验失败，已拒绝保留安装包")
        if cancel_event.is_set():
            raise DownloadCancelled("用户已取消 ComfyUI 下载")
        os.replace(partial, target)
        if progress_callback:
            progress_callback({
                "status": "verified",
                "received_bytes": received,
                "total_bytes": verified["size_bytes"],
                "percent": 100,
            })
        return {"path": str(target), "size_bytes": received, "sha256": digest.hexdigest()}
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, DownloadCancelled):
        try:
            partial.unlink(missing_ok=True)
        except OSError:
            pass
        raise


# Download task metadata is persisted locally so users can inspect the result
# after restarting the desktop application. In-flight transfers are marked
# interrupted on restart; they are never silently resumed or restarted.
import threading
import uuid

_DOWNLOAD_TASKS = {}
_DOWNLOAD_TASKS_LOCK = threading.Lock()
_DOWNLOAD_THREADS = {}
_TERMINAL_DOWNLOAD_STATES = {"verified", "failed", "cancelled", "interrupted"}


def _download_state_file():
    return Path.home() / "AIComicStudio" / "ComfyUI" / ".installer_state" / "download_tasks.json"


def _persist_download_tasks_locked():
    """Atomically persist bounded task metadata; never persist credentials or events."""
    import tempfile
    state_file = _download_state_file()
    state_dir = state_file.parent
    state_dir.mkdir(parents=True, exist_ok=True)
    if state_dir.is_symlink() or state_file.is_symlink():
        raise OSError("下载任务状态目录不能是符号链接")
    records = []
    for task in list(_DOWNLOAD_TASKS.values())[-200:]:
        records.append({
            "task_id": task["task_id"],
            "status": task["status"],
            "asset_name": task["asset_name"],
            "version": task["version"],
            "received_bytes": task["received_bytes"],
            "total_bytes": task["total_bytes"],
            "percent": task["percent"],
            "error": task.get("error"),
        })
    temp_path = state_file.with_name(state_file.name + ".tmp")
    if temp_path.is_symlink():
        raise OSError("下载任务临时状态文件不能是符号链接")
    payload = json.dumps({"schema_version": 1, "tasks": records}, ensure_ascii=False)
    try:
        with temp_path.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, state_file)
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _restore_download_tasks():
    """Restore completed history and mark nonterminal tasks interrupted."""
    state_file = _download_state_file()
    try:
        if state_file.is_symlink() or not state_file.is_file():
            return
        if state_file.stat().st_size > 1024 * 1024:
            return
        payload = json.loads(state_file.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            return
        records = payload.get("tasks")
        if not isinstance(records, list):
            return
        restored = {}
        for record in records[-200:]:
            if not isinstance(record, dict):
                continue
            task_id = record.get("task_id")
            asset_name = record.get("asset_name")
            version = record.get("version")
            status = record.get("status")
            if (
                not isinstance(task_id, str) or not re.fullmatch(r"[0-9a-f]{32}", task_id)
                or asset_name not in ALLOWED_ASSET_NAMES
                or not isinstance(version, str) or len(version) > 100
                or status not in {"queued", "downloading", "cancelling", *_TERMINAL_DOWNLOAD_STATES}
            ):
                continue
            total = record.get("total_bytes")
            received = record.get("received_bytes")
            percent = record.get("percent")
            if (
                not isinstance(total, int) or isinstance(total, bool) or total <= 0
                or not isinstance(received, int) or isinstance(received, bool)
                or not 0 <= received <= total
                or not isinstance(percent, int) or isinstance(percent, bool)
                or not 0 <= percent <= 100
            ):
                continue
            restored_status = status
            error = record.get("error")
            if status not in _TERMINAL_DOWNLOAD_STATES:
                restored_status = "interrupted"
                error = "应用重启时下载未完成；为避免损坏文件，没有自动续传。"
            restored[task_id] = {
                "task_id": task_id,
                "status": restored_status,
                "asset_name": asset_name,
                "version": version,
                "received_bytes": received,
                "total_bytes": total,
                "percent": percent if restored_status == "verified" else min(percent, 99),
                "error": str(error)[:500] if error else None,
                "cancel_event": threading.Event(),
            }
        with _DOWNLOAD_TASKS_LOCK:
            _DOWNLOAD_TASKS.update(restored)
            if restored:
                _persist_download_tasks_locked()
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        # A corrupt state file must not prevent the application from starting.
        return


def _public_download_task(task):
    return {
        "task_id": task["task_id"],
        "status": task["status"],
        "asset_name": task["asset_name"],
        "version": task["version"],
        "received_bytes": task["received_bytes"],
        "total_bytes": task["total_bytes"],
        "percent": task["percent"],
        "error": task.get("error"),
        "verified": task["status"] == "verified",
        "installation_started": False,
    }


def _run_download_task(task_id, asset, version, install_root):
    from pathlib import Path
    with _DOWNLOAD_TASKS_LOCK:
        task = _DOWNLOAD_TASKS.get(task_id)
        if task is None:
            return
        cancel_event = task["cancel_event"]
        if cancel_event.is_set():
            task["status"] = "cancelled"
            _persist_download_tasks_locked()
            _DOWNLOAD_THREADS.pop(task_id, None)
            return
        task["status"] = "downloading"
        _persist_download_tasks_locked()

    last_persisted_percent = -1

    def update(progress):
        nonlocal last_persisted_percent
        with _DOWNLOAD_TASKS_LOCK:
            current = _DOWNLOAD_TASKS.get(task_id)
            if current is None:
                return
            current["received_bytes"] = progress.get("received_bytes", current["received_bytes"])
            current["total_bytes"] = progress.get("total_bytes", current["total_bytes"])
            current["percent"] = progress.get("percent", current["percent"])
            if progress.get("status") == "verified":
                current["status"] = "verified"
            if current["percent"] != last_persisted_percent or progress.get("status") == "verified":
                _persist_download_tasks_locked()
                last_persisted_percent = current["percent"]

    try:
        destination = Path(install_root) / "downloads" / asset["name"]
        result = download_verified_official_asset(
            asset, version, install_root, destination,
            cancel_event=cancel_event, progress_callback=update,
        )
        with _DOWNLOAD_TASKS_LOCK:
            current = _DOWNLOAD_TASKS.get(task_id)
            if current is not None:
                current["status"] = "verified"
                current["received_bytes"] = result["size_bytes"]
                current["total_bytes"] = result["size_bytes"]
                current["percent"] = 100
                _persist_download_tasks_locked()
    except DownloadCancelled:
        with _DOWNLOAD_TASKS_LOCK:
            if task_id in _DOWNLOAD_TASKS:
                _DOWNLOAD_TASKS[task_id]["status"] = "cancelled"
                _persist_download_tasks_locked()
    except Exception as exc:
        with _DOWNLOAD_TASKS_LOCK:
            if task_id in _DOWNLOAD_TASKS:
                _DOWNLOAD_TASKS[task_id]["status"] = "failed"
                _DOWNLOAD_TASKS[task_id]["error"] = str(exc)[:500]
                _persist_download_tasks_locked()
    finally:
        with _DOWNLOAD_TASKS_LOCK:
            _DOWNLOAD_THREADS.pop(task_id, None)


