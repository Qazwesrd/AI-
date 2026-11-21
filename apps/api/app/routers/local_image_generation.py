"""Explicitly confirmed image generation using an already-running local ComfyUI.

All requests are sent to a fixed loopback address. This module never downloads
models, starts ComfyUI, or falls back to a cloud provider.
"""
import json
import os
import re
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.media import MediaFile
from app.services.media_service import STORAGE
from pydantic import BaseModel, Field, field_validator

router = APIRouter(prefix="/api/local-ai/images", tags=["本地图片生成"])
COMFYUI_BASE_URL = "http://127.0.0.1:8188"
class _BlockRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


OPENER = build_opener(ProxyHandler({}), _BlockRedirects())
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_IMAGE_BYTES = 25 * 1024 * 1024
PROMPT_ID_RE = re.compile(r"^[0-9a-fA-F-]{32,36}$")
CHECKPOINT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ /-]{0,239}$")
STYLE_GUIDANCE = {
    "国漫": "Chinese animation visual style, expressive cinematic composition, refined linework, rich traditional-inspired color design",
    "仿真人": "photorealistic live-action film still, natural human proportions, cinematic lighting, realistic materials",
    "3D漫": "high-quality 3D animated film still, detailed 3D character design, cinematic rendering, polished materials",
    "2D漫": "high-quality 2D anime illustration, clean line art, expressive cel shading, carefully composed animation frame",
}


class LocalImageArchiveInput(BaseModel):
    filename: str = Field(min_length=1, max_length=240)
    subfolder: str = Field(default="", max_length=240)
    type: Literal["output"] = "output"


class LocalImageGenerateInput(BaseModel):
    checkpoint: str = Field(min_length=1, max_length=240)
    prompt: str = Field(min_length=3, max_length=6000)
    negative_prompt: str = Field(default="low quality, blurry, distorted anatomy, extra fingers, text, watermark", max_length=3000)
    style: Literal["国漫", "仿真人", "3D漫", "2D漫"] = "国漫"
    width: int = Field(default=768, ge=512, le=1536)
    height: int = Field(default=768, ge=512, le=1536)
    steps: int = Field(default=24, ge=1, le=40)
    cfg: float = Field(default=7.0, ge=1.0, le=12.0)
    seed: int | None = Field(default=None, ge=0, le=4294967295)
    confirm_local_generation: bool = False

    @field_validator("checkpoint")
    @classmethod
    def validate_checkpoint(cls, value):
        value = value.strip()
        if not CHECKPOINT_RE.fullmatch(value) or ".." in value or "//" in value or "\\\\" in value:
            raise ValueError("图片模型名称格式无效")
        return value

    @field_validator("prompt", "negative_prompt")
    @classmethod
    def normalize_prompt(cls, value):
        return value.strip()


def _local_request(path: str, payload: dict | None = None, timeout: float = 8.0):
    allowed = path in {"/system_stats", "/object_info/CheckpointLoaderSimple", "/prompt"}
    allowed = allowed or bool(re.fullmatch(r"/history/[0-9a-fA-F-]{32,36}", path))
    if not allowed:
        raise ValueError("Unsupported local ComfyUI endpoint")
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(
        COMFYUI_BASE_URL + path,
        data=body,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST" if body is not None else "GET",
    )
    try:
        with OPENER.open(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise HTTPException(502, "ComfyUI 返回数据超过安全读取上限")
        result = json.loads(raw.decode("utf-8"))
        if not isinstance(result, dict):
            raise HTTPException(502, "ComfyUI 返回格式无效")
        return result
    except HTTPError as exc:
        detail = exc.read(1200).decode("utf-8", errors="replace")
        raise HTTPException(502, f"本机 ComfyUI 返回 HTTP {exc.code}：{detail}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise HTTPException(503, "未连接到本机 ComfyUI（127.0.0.1:8188）。请先安装并启动本地图片引擎。") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(502, "本机 ComfyUI 返回了无法识别的数据") from exc


def _checkpoint_names(object_info: dict):
    node = object_info.get("CheckpointLoaderSimple")
    if not isinstance(node, dict):
        return []
    inputs = node.get("input")
    required = inputs.get("required") if isinstance(inputs, dict) else None
    names = required.get("ckpt_name") if isinstance(required, dict) else None
    if not isinstance(names, list) or not names or not isinstance(names[0], list):
        return []
    return [name for name in names[0] if isinstance(name, str) and CHECKPOINT_RE.fullmatch(name) and ".." not in name and "//" not in name and "\\\\" not in name]


def build_txt2img_workflow(payload: LocalImageGenerateInput, seed: int):
    positive = payload.prompt + "\nVisual style: " + STYLE_GUIDANCE[payload.style]
    return {
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": payload.checkpoint}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": positive, "clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": payload.negative_prompt, "clip": ["4", 1]}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": payload.width, "height": payload.height, "batch_size": 1}},
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed, "steps": payload.steps, "cfg": payload.cfg,
                "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0,
                "model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["5", 0],
            },
        },
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "AI_Comic_Studio", "images": ["8", 0]}},
    }


@router.get("/models")
def list_local_image_models():
    object_info = _local_request("/object_info/CheckpointLoaderSimple")
    checkpoints = _checkpoint_names(object_info)
    return {
        "engine": "ComfyUI",
        "status": "ready",
        "base_url": COMFYUI_BASE_URL,
        "models": checkpoints,
        "model_count": len(checkpoints),
        "local_only": True,
        "network_used": False,
        "downloaded": False,
        "cloud_fallback": False,
    }


@router.post("/generate")
def generate_local_image(payload: LocalImageGenerateInput):
    if not payload.confirm_local_generation:
        raise HTTPException(400, "本地图片生成会占用电脑资源；请勾选确认后再开始。")
    if payload.width % 64 or payload.height % 64:
        raise HTTPException(400, "图片宽度和高度必须是 64 的倍数。")
    object_info = _local_request("/object_info/CheckpointLoaderSimple")
    checkpoints = _checkpoint_names(object_info)
    if payload.checkpoint not in checkpoints:
        raise HTTPException(404, "所选图片模型未安装在本机 ComfyUI 中；本程序不会自动下载模型。")
    seed = payload.seed if payload.seed is not None else uuid.uuid4().int % (2 ** 32)
    workflow = build_txt2img_workflow(payload, seed)
    result = _local_request("/prompt", {"prompt": workflow, "client_id": str(uuid.uuid4())}, timeout=15.0)
    prompt_id = result.get("prompt_id")
    if not isinstance(prompt_id, str) or not PROMPT_ID_RE.fullmatch(prompt_id):
        raise HTTPException(502, "ComfyUI 未返回有效任务编号，无法确认任务已进入队列。")
    return {
        "success": True,
        "engine": "ComfyUI",
        "prompt_id": prompt_id,
        "seed": seed,
        "checkpoint": payload.checkpoint,
        "status": "queued",
        "local_only": True,
        "network_used": False,
        "downloaded": False,
        "paid_api_used": False,
        "cloud_fallback": False,
        "message": "图片任务已提交到本机 ComfyUI 队列。生成速度取决于显卡、显存、模型和分辨率。",
    }


@router.get("/tasks/{prompt_id}")
def get_local_image_task(prompt_id: str):
    if not PROMPT_ID_RE.fullmatch(prompt_id):
        raise HTTPException(400, "任务编号格式无效")
    history = _local_request("/history/" + prompt_id, timeout=5.0)
    item = history.get(prompt_id)
    if not isinstance(item, dict):
        return {"prompt_id": prompt_id, "status": "queued_or_running", "images": [], "local_only": True}
    status_info = item.get("status") if isinstance(item.get("status"), dict) else {}
    outputs = item.get("outputs") if isinstance(item.get("outputs"), dict) else {}
    images = []
    for node_id, output in outputs.items():
        if not isinstance(output, dict):
            continue
        for image in output.get("images", []) if isinstance(output.get("images"), list) else []:
            if not isinstance(image, dict):
                continue
            filename = image.get("filename")
            subfolder = image.get("subfolder", "")
            if isinstance(filename, str) and filename and len(filename) <= 240 and ".." not in filename and "/" not in filename and "\\" not in filename and image.get("type", "output") == "output":
                images.append({
                    "node_id": str(node_id),
                    "filename": filename,
                    "subfolder": subfolder if isinstance(subfolder, str) and len(subfolder) <= 240 else "",
                    "type": image.get("type", "output"),
                })
    completed = status_info.get("completed") is True
    return {
        "prompt_id": prompt_id,
        "status": "completed" if completed else "running",
        "images": images[:20] if completed else [],
        "local_only": True,
        "network_used": False,
        "cloud_fallback": False,
    }


def _safe_output_image(image: dict):
    filename = image.get("filename")
    subfolder = image.get("subfolder", "")
    image_type = image.get("type", "output")
    if (
        not isinstance(filename, str)
        or not re.fullmatch(r"[A-Za-z0-9 _.-]{1,240}", filename)
        or ".." in filename
        or not isinstance(subfolder, str)
        or len(subfolder) > 240
        or (subfolder and (
            subfolder.startswith("/")
            or "\\" in subfolder
            or ":" in subfolder
            or "//" in subfolder
            or any(part in ("", ".", "..") for part in subfolder.split("/"))
            or not re.fullmatch(r"[A-Za-z0-9 _./-]+", subfolder)
        ))
        or image_type != "output"
    ):
        raise HTTPException(400, "ComfyUI 图片输出记录包含不安全的文件名或路径")
    suffix = Path(filename).suffix.lower()
    if suffix not in (".png", ".jpg", ".jpeg", ".webp"):
        raise HTTPException(415, "仅支持归档 PNG、JPEG 或 WebP 图片")
    return filename, subfolder, suffix


def _download_comfyui_output(filename: str, subfolder: str):
    query = urlencode({"filename": filename, "subfolder": subfolder, "type": "output"})
    request = Request(
        COMFYUI_BASE_URL + "/view?" + query,
        headers={"Accept": "image/png,image/jpeg,image/webp"},
        method="GET",
    )
    try:
        with OPENER.open(request, timeout=20.0) as response:
            raw = response.read(MAX_IMAGE_BYTES + 1)
    except HTTPError as exc:
        raise HTTPException(502, f"读取本机 ComfyUI 图片失败（HTTP {exc.code}）") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise HTTPException(503, "无法从本机 ComfyUI 读取图片；请确认本地图片引擎仍在运行。") from exc
    if not raw:
        raise HTTPException(502, "ComfyUI 返回了空图片")
    if len(raw) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "单张图片超过 25 MiB 安全归档上限")
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return raw, "image/png", ".png"
    if raw.startswith(b"\xff\xd8\xff"):
        return raw, "image/jpeg", ".jpg"
    if len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return raw, "image/webp", ".webp"
    raise HTTPException(415, "返回内容不是有效的 PNG、JPEG 或 WebP 图片")


@router.post("/tasks/{prompt_id}/archive")
def archive_local_image_task_output(
    prompt_id: str,
    data: LocalImageArchiveInput,
    db: Session = Depends(get_db),
):
    """Archive one explicitly selected completed ComfyUI output into the local media library."""
    if not PROMPT_ID_RE.fullmatch(prompt_id):
        raise HTTPException(400, "任务编号格式无效")
    selected_filename, selected_subfolder, _ = _safe_output_image(data.model_dump())
    history = _local_request("/history/" + prompt_id, timeout=5.0)
    item = history.get(prompt_id)
    if not isinstance(item, dict):
        raise HTTPException(409, "图片任务尚未完成，暂时没有可归档的输出")
    status_info = item.get("status") if isinstance(item.get("status"), dict) else {}
    if status_info.get("completed") is not True:
        raise HTTPException(409, "ComfyUI 尚未明确报告任务完成，暂时不能归档")
    outputs = item.get("outputs") if isinstance(item.get("outputs"), dict) else {}
    valid_images = []
    for output in outputs.values():
        if isinstance(output, dict) and isinstance(output.get("images"), list):
            valid_images.extend(image for image in output["images"] if isinstance(image, dict))
    matched = False
    for image in valid_images:
        try:
            current = _safe_output_image(image)
        except HTTPException:
            continue
        if current[:2] == (selected_filename, selected_subfolder):
            matched = True
            break
    if not matched:
        raise HTTPException(404, "所选图片不属于该任务的已完成输出，无法归档")
    existing = db.query(MediaFile).filter(MediaFile.category == "image").order_by(MediaFile.id.desc()).limit(1000).all()
    for media in existing:
        meta = media.metadata_json if isinstance(media.metadata_json, dict) else {}
        if (
            meta.get("comfyui_prompt_id") == prompt_id
            and meta.get("comfyui_filename") == selected_filename
            and meta.get("comfyui_subfolder", "") == selected_subfolder
        ):
            return {
                "success": True, "already_archived": True, "media_file_id": media.id,
                "name": media.name, "path": media.file_path,
                "message": "这张图片已经归档到素材库，没有重复保存。",
            }
    raw, mime_type, suffix = _download_comfyui_output(selected_filename, selected_subfolder)
    folder = STORAGE / "images"
    folder.mkdir(parents=True, exist_ok=True)
    stored_name = uuid.uuid4().hex + suffix
    destination = folder / stored_name
    temporary = folder / (stored_name + ".uploading")
    try:
        temporary.write_bytes(raw)
        os.replace(temporary, destination)
        media = MediaFile(
            name=selected_filename,
            category="image",
            file_path=str(destination),
            mime_type=mime_type,
            file_size=len(raw),
            metadata_json={
                "description": "由本机 ComfyUI 生成并经用户确认归档",
                "source": "local_comfyui",
                "comfyui_prompt_id": prompt_id,
                "comfyui_filename": selected_filename,
                "comfyui_subfolder": selected_subfolder,
                "stored_name": stored_name,
            },
        )
        db.add(media)
        db.commit()
        db.refresh(media)
        return {
            "success": True, "already_archived": False, "media_file_id": media.id,
            "name": media.name, "category": media.category, "mime_type": media.mime_type,
            "size": media.file_size, "path": media.file_path,
            "url": f"/api/media/files/{media.id}/download",
            "message": "图片已保存到本地素材库。",
        }
    except Exception:
        db.rollback()
        destination.unlink(missing_ok=True)
        temporary.unlink(missing_ok=True)
        raise
