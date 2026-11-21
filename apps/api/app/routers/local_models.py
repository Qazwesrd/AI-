import json
import os
import re
import tempfile
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, HttpUrl

from app.database import STORAGE_DIR
from app.routers.system import _get_hardware_profile, _scan_local_model_files

router = APIRouter(prefix="/api/local-models", tags=["本地模型库"])

MODEL_DIR = Path(STORAGE_DIR) / "models"
CATALOG_PATH = MODEL_DIR / ".model-library.json"
SAFE_MODEL_NAME = re.compile(r"^[A-Za-z0-9_.()\-\u4e00-\u9fff ]{1,240}$")
LICENSE_REVIEW_VALUES = {
    "unverified",
    "checked_by_user",
    "commercial_permission_confirmed",
    "noncommercial_only",
    "restricted",
}


class ModelReviewInput(BaseModel):
    filename: str = Field(min_length=1, max_length=240)
    license_review: Literal[
        "unverified",
        "checked_by_user",
        "commercial_permission_confirmed",
        "noncommercial_only",
        "restricted",
    ] = "unverified"
    license_name: str = Field(default="", max_length=160)
    license_url: HttpUrl | None = None
    notes: str = Field(default="", max_length=2000)


def _read_catalog():
    if not CATALOG_PATH.is_file():
        return {}
    try:
        payload = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        raise HTTPException(500, "本地模型资料文件无法读取，请先备份后检查 .model-library.json")


def _write_catalog(catalog):
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".model-library-", suffix=".tmp", dir=str(MODEL_DIR))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(catalog, output, ensure_ascii=False, indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, CATALOG_PATH)
    finally:
        try:
            if os.path.exists(temporary):
                os.unlink(temporary)
        except OSError:
            pass


def _compatibility(file_size, hardware):
    memory = hardware.get("memory", {})
    gpu = hardware.get("gpu", {})
    ram_bytes = memory.get("total_bytes")
    vram_bytes = gpu.get("vram_bytes")
    # This is only a conservative first-pass estimate, not a runtime test.
    cpu_possible = bool(ram_bytes and ram_bytes >= file_size * 1.5)
    gpu_possible = bool(vram_bytes and vram_bytes >= file_size * 1.1)
    if gpu_possible:
        result = "gpu_candidate"
        label = "显存容量初步匹配"
    elif cpu_possible:
        result = "cpu_candidate"
        label = "内存容量初步匹配，CPU 运行可能较慢"
    elif ram_bytes:
        result = "insufficient_memory"
        label = "内存可能不足，不建议直接加载"
    else:
        result = "unknown"
        label = "无法判断，请先完成硬件检测"
    return {
        "status": result,
        "label": label,
        "file_size_bytes": file_size,
        "ram_bytes": ram_bytes,
        "vram_bytes": vram_bytes,
        "note": "仅按模型文件大小与系统报告的内存/显存做粗略初筛；实际加载还受量化格式、上下文长度、运行时和系统占用影响。文件大小不等于实际运行内存需求。",
    }


def _catalog_items():
    scanned = _scan_local_model_files()
    catalog = _read_catalog()
    hardware = _get_hardware_profile()
    items = []
    for model in scanned.get("files", []):
        review = catalog.get(model["name"], {})
        items.append({
            **model,
            "license_review": review.get("license_review", "unverified"),
            "license_name": review.get("license_name", ""),
            "license_url": review.get("license_url"),
            "notes": review.get("notes", ""),
            "license_notice": "许可证状态由用户自行填写，软件未核验其真实性、完整性或商用授权。",
            "compatibility": _compatibility(model["size_bytes"], hardware),
        })
    return {
        "items": items,
        "total": scanned.get("count", 0),
        "directory": scanned.get("directory"),
        "hardware": hardware,
        "network_used": False,
        "downloaded": False,
        "note": "此页面只管理本机已存在的模型文件和用户填写的许可证备注；不会下载、启动、删除或修改模型文件。",
    }


@router.get("")
def list_local_models():
    return _catalog_items()


@router.put("/review")
def save_model_review(payload: ModelReviewInput):
    filename = payload.filename.strip()
    if not SAFE_MODEL_NAME.fullmatch(filename) or Path(filename).name != filename:
        raise HTTPException(400, "模型文件名无效")
    scanned = _scan_local_model_files()
    known = {item["name"] for item in scanned.get("files", [])}
    if filename not in known:
        raise HTTPException(404, "模型文件不存在于应用模型目录；请先刷新模型库")
    catalog = _read_catalog()
    catalog[filename] = {
        "license_review": payload.license_review,
        "license_name": payload.license_name.strip(),
        "license_url": str(payload.license_url) if payload.license_url else None,
        "notes": payload.notes.strip(),
    }
    _write_catalog(catalog)
    return next(item for item in _catalog_items()["items"] if item["name"] == filename)
