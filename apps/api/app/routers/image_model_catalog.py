"""Curated image checkpoint catalog and safe, metadata-only download preflight.

The preflight makes one HTTPS HEAD request to a pinned Hugging Face URL. It
never follows redirects, downloads bytes, reserves disk blocks, or installs
weights. Download remains disabled until all independent gates pass.
"""
import shutil
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/local-ai/images/model-catalog", tags=["本地图片模型清单"])

GIB = 1024 ** 3
DISK_SAFETY_MARGIN_BYTES = GIB
MAX_MODEL_BYTES = 20 * GIB
_METADATA_TIMEOUT_SECONDS = 12


_CATALOG = [
    {
        "id": "comfy-org-sd15-fp16",
        "name": "Stable Diffusion 1.5 FP16（基础模型）",
        "publisher": "Comfy-Org",
        "repository": "Comfy-Org/stable-diffusion-1.5",
        "revision": "4fddeb7",
        "filename": "v1-5-pruned-emaonly-fp16.safetensors",
        "format": "safetensors",
        "size_bytes": None,
        "size_label": "约 2.13 GB（上游页面显示值，非精确字节数）",
        "sha256": "e9476a13728cd75d8279f6ec8bad753a66a1957ca375a1464dc63b37db6e3916",
        "source_url": "https://huggingface.co/Comfy-Org/stable-diffusion-1.5/blob/4fddeb7/v1-5-pruned-emaonly-fp16.safetensors",
        "download_url": "https://huggingface.co/Comfy-Org/stable-diffusion-1.5/resolve/4fddeb7/v1-5-pruned-emaonly-fp16.safetensors",
        "license": "CreativeML OpenRAIL-M",
        "license_url": "https://huggingface.co/Comfy-Org/stable-diffusion-1.5/blob/4fddeb7/LICENSE",
        "license_status": "review_required",
        "license_note": "OpenRAIL-M 含使用限制；不能据此直接承诺所有商业用途均获许可，发布作品前需阅读许可证及模型卡。",
        "hardware_status": "cpu_not_recommended",
        "hardware_note": "当前设备按 CPU 模式运行。该模型可作为 ComfyUI SD1.x 检查候选，但 CPU 生图预计较慢；文件大小不等于运行内存需求，必须先做小分辨率实机测试。",
        "recommended_directory": "ComfyUI/models/checkpoints",
        "sha256_source": "Hugging Face 文件页显示的 SHA-256",
        "sha256_verified": False,
        "catalog_status": "preview_only",
        "download_eligible": False,
        "install_eligible": False,
        "eligibility_blockers": [
            "尚未在本机完成下载后 SHA-256 复核。",
            "CreativeML OpenRAIL-M 商用及内容限制尚待逐项审核。",
            "尚未在目标 CPU-only 设备上完成 ComfyUI 实机性能测试。",
        ],
    }
]


def list_image_model_catalog():
    """Return pinned model metadata only."""
    return {
        "status": "preview_only",
        "models": [dict(item) for item in _CATALOG],
        "count": len(_CATALOG),
        "network_used": False,
        "download_started": False,
        "installation_started": False,
        "download_available": False,
        "install_available": False,
        "note": "清单只展示候选模型。下载前必须完成精确文件大小预检、SHA-256 本机复核、许可证审核和目标设备实机测试。",
    }


@router.get("")
def get_image_model_catalog():
    return list_image_model_catalog()


class _NoRedirectHandler(HTTPRedirectHandler):
    """Do not follow Hub redirects; signed CDN URLs are not needed for HEAD.""" 
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _parse_positive_int(value):
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _head_pinned_file_metadata(url, opener=None):
    # The catalog is static and pinned; never accept a URL from the request.
    if not isinstance(url, str) or not url.startswith(
        "https://huggingface.co/Comfy-Org/stable-diffusion-1.5/resolve/4fddeb7/"
    ):
        raise ValueError("模型来源地址不在固定白名单中")
    client = opener or build_opener(_NoRedirectHandler())
    headers = {"Accept-Encoding": "identity", "User-Agent": "AIComicStudio-model-preflight/1.0"}
    request = Request(url, method="HEAD", headers=headers)
    try:
        response = client.open(request, timeout=_METADATA_TIMEOUT_SECONDS)
    except HTTPError as exc:
        # Hugging Face LFS metadata is often carried by a 302 response.
        if 300 <= exc.code < 400:
            response = exc
        else:
            raise ValueError(f"模型源站返回 HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise ValueError("无法连接模型官方源站，未开始下载") from exc

    with response:
        response_headers = response.headers
        if response_headers.get("Content-Encoding", "identity").lower() not in {"", "identity"}:
            raise ValueError("源站返回了压缩元数据，无法安全确认原始文件大小")
        size = _parse_positive_int(response_headers.get("X-Linked-Size"))
        if size is None:
            size = _parse_positive_int(response_headers.get("Content-Length"))
        etag_present = bool(response_headers.get("X-Linked-Etag") or response_headers.get("ETag"))
        sha256_header_present = bool(response_headers.get("X-Linked-Sha256"))

    # Some compatible gateways omit size headers on HEAD. A one-byte ranged
    # GET may still expose the exact total in Content-Range. Never read more
    # than one response byte, never follow redirects, and fail closed otherwise.
    if size is None:
        ranged = Request(
            url,
            method="GET",
            headers={**headers, "Range": "bytes=0-0"},
        )
        try:
            ranged_response = client.open(ranged, timeout=_METADATA_TIMEOUT_SECONDS)
        except HTTPError as exc:
            if exc.code == 206:
                ranged_response = exc
            elif 300 <= exc.code < 400:
                ranged_response = exc
            else:
                raise ValueError(f"模型源站范围查询返回 HTTP {exc.code}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise ValueError("无法取得精确文件大小；范围查询失败，未开始下载") from exc
        with ranged_response:
            range_header = ranged_response.headers.get("Content-Range", "")
            match = __import__("re").fullmatch(r"bytes\\s+0-0/(\\d+)", range_header.strip(), flags=__import__("re").IGNORECASE)
            if match:
                size = _parse_positive_int(match.group(1))
            # Consume at most one byte. A server ignoring Range is rejected.
            sample = ranged_response.read(2)
            if getattr(ranged_response, "status", None) != 206 and getattr(ranged_response, "code", None) != 206:
                raise ValueError("源站未遵守单字节范围请求；本次预检失败关闭")
            if len(sample) > 1:
                raise ValueError("范围查询返回数据超过 1 字节；本次预检失败关闭")
    if size is None:
        raise ValueError("源站未提供可验证的精确文件大小；本次预检失败关闭")
    if size > MAX_MODEL_BYTES:
        raise ValueError("文件超过本软件当前允许预检的 20 GiB 上限")
    return {
        "http_status": getattr(response, "status", None) or getattr(response, "code", None),
        "size_bytes": size,
        "etag_present": etag_present,
        "sha256_header_present": sha256_header_present,
    }

def _existing_disk_probe_path(target: Path) -> Path:
    candidate = target
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    if not candidate.exists() or not candidate.is_dir():
        raise OSError("无法定位模型目标磁盘")
    return candidate


def _preflight_disk_space(target: Path, size_bytes: int):
    probe = _existing_disk_probe_path(target)
    usage = shutil.disk_usage(probe)
    required = size_bytes + DISK_SAFETY_MARGIN_BYTES
    return {
        "target_directory": str(target),
        "disk_probe_directory": str(probe),
        "free_bytes": usage.free,
        "required_bytes": required,
        "sufficient": usage.free >= required,
        "safety_margin_bytes": DISK_SAFETY_MARGIN_BYTES,
    }


@router.post("/{model_id}/preflight")
def preflight_image_model_download(model_id: str):
    """Check exact upstream size and local free space; never download."""
    item = next((entry for entry in _CATALOG if entry["id"] == model_id), None)
    if item is None:
        raise HTTPException(status_code=404, detail="清单中不存在该模型")
    target = Path.home() / "AIComicStudio" / "ComfyUI" / "ComfyUI" / "models" / "checkpoints"
    try:
        metadata = _head_pinned_file_metadata(item["download_url"])
        disk = _preflight_disk_space(target, metadata["size_bytes"])
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    blockers = list(item["eligibility_blockers"])
    if not disk["sufficient"]:
        blockers.insert(0, "目标磁盘可用空间不足（已额外预留 1 GiB 安全余量）。")
    return {
        "model_id": model_id,
        "preflight_status": "passed" if disk["sufficient"] else "insufficient_disk_space",
        "upstream_size_bytes": metadata["size_bytes"],
        "upstream_size_gib": round(metadata["size_bytes"] / GIB, 3),
        "etag_present": metadata["etag_present"],
        "publisher_sha256": item["sha256"],
        "publisher_sha256_verified_locally": False,
        "disk": disk,
        "eligibility_blockers": blockers,
        "download_allowed": False,
        "download_started": False,
        "installation_allowed": False,
        "installation_started": False,
        "message": "预检只读取源站文件元数据并检查本地磁盘空间，不会下载、占用磁盘空间或安装模型。",
    }
