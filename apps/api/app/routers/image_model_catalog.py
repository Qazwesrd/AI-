"""Curated, read-only image checkpoint catalog for the local ComfyUI workflow.

Entries are pinned to an upstream revision and include a publisher-provided SHA-256.
This module never contacts the network, downloads weights, or installs models.
"""
from fastapi import APIRouter

router = APIRouter(prefix="/api/local-ai/images/model-catalog", tags=["本地图片模型清单"])

# The upstream file page reports a rounded display size (2.13 GB), so size_bytes
# intentionally remains null. The SHA-256 and revision are taken from the
# Hugging Face file page, not calculated locally or inferred from file size.
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
            "尚未取得并核验精确文件字节数（Content-Length）。",
            "尚未在本机完成下载后 SHA-256 复核。",
            "CreativeML OpenRAIL-M 商用及内容限制尚待逐项审核。",
            "尚未在目标 CPU-only 设备上完成 ComfyUI 实机性能测试。",
        ],
    }
]


@router.get("")
def list_image_model_catalog():
    """Return pinned model metadata only; never download or install weights."""
    return {
        "status": "preview_only",
        "models": [dict(item) for item in _CATALOG],
        "count": len(_CATALOG),
        "network_used": False,
        "download_started": False,
        "installation_started": False,
        "download_available": False,
        "install_available": False,
        "note": "这是经来源整理的预览清单。下载和安装功能均未开放。候选条目明确标记 download_eligible=false、install_eligible=false；必须先取得精确 Content-Length、完成本机 SHA-256 复核、审核许可证并通过目标设备实机测试。",
    }
