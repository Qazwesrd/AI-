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
from app.models.story import StoryProject, EpisodePlan, StoryboardShot, StoryboardMediaLink, StoryboardMediaRevision
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
    shot_id: int | None = Field(default=None, ge=1)


class StoryboardImageReuseInput(BaseModel):
    media_file_id: int = Field(ge=1)


class StoryboardImageReplaceInput(BaseModel):
    media_file_id: int = Field(ge=1)


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


@router.get("/targets")
def list_storyboard_image_targets(db: Session = Depends(get_db)):
    """Return project/episode/shot choices for optional local image association."""
    projects = db.query(StoryProject).order_by(StoryProject.created_at.desc()).all()
    result = []
    for project in projects:
        episodes = db.query(EpisodePlan).filter(
            EpisodePlan.project_id == project.id
        ).order_by(EpisodePlan.sort_order, EpisodePlan.episode_number).all()
        episode_rows = []
        for episode in episodes:
            shots = db.query(StoryboardShot).filter(
                StoryboardShot.project_id == project.id,
                StoryboardShot.episode_id == episode.id,
            ).order_by(StoryboardShot.sort_order, StoryboardShot.shot_number).all()
            episode_rows.append({
                "id": episode.id,
                "episode_number": episode.episode_number,
                "title": episode.title,
                "shots": [{
                    "id": shot.id,
                    "shot_number": shot.shot_number,
                    "scene_name": shot.scene_name,
                    "visual_description": (shot.visual_description or "")[:240],
                } for shot in shots],
            })
        result.append({
            "id": project.id,
            "name": project.name,
            "adaptation_style": project.adaptation_style,
            "episodes": episode_rows,
        })
    return {"projects": result}


@router.get("/shots/{shot_id}/media")
def list_shot_media(shot_id: int, db: Session = Depends(get_db)):
    shot = db.query(StoryboardShot).filter(StoryboardShot.id == shot_id).first()
    if shot is None:
        raise HTTPException(404, "分镜镜头不存在")
    links = db.query(StoryboardMediaLink).filter(
        StoryboardMediaLink.shot_id == shot.id,
        StoryboardMediaLink.project_id == shot.project_id,
        StoryboardMediaLink.episode_id == shot.episode_id,
    ).all()
    result = []
    for link in links:
        media = db.query(MediaFile).filter(
            MediaFile.id == link.media_file_id,
            MediaFile.category == "image",
        ).first()
        if media is None:
            continue
        result.append({
            "link_id": link.id,
            "media_file_id": media.id,
            "name": media.name,
            "mime_type": media.mime_type,
            "file_size": media.file_size,
            "download_url": f"/api/media/files/{media.id}/download",
            "created_at": link.created_at.isoformat() if link.created_at else None,
        })
    return {"shot_id": shot.id, "images": result}




def _replace_shot_media(db: Session, shot_id: int, link_id: int, media_file_id: int):
    shot = db.query(StoryboardShot).filter(StoryboardShot.id == shot_id).first()
    if shot is None:
        raise HTTPException(404, "分镜镜头不存在")
    link = db.query(StoryboardMediaLink).filter(
        StoryboardMediaLink.id == link_id,
        StoryboardMediaLink.shot_id == shot.id,
        StoryboardMediaLink.project_id == shot.project_id,
        StoryboardMediaLink.episode_id == shot.episode_id,
    ).first()
    if link is None:
        raise HTTPException(404, "该图片关联不存在，或不属于当前分镜")
    replacement = db.query(MediaFile).filter(
        MediaFile.id == media_file_id,
        MediaFile.category == "image",
    ).first()
    if replacement is None:
        raise HTTPException(404, "素材库中不存在该图片")
    if link.media_file_id == replacement.id:
        return {"success": True, "unchanged": True, "media_file_id": replacement.id}
    duplicate = db.query(StoryboardMediaLink).filter(
        StoryboardMediaLink.shot_id == shot.id,
        StoryboardMediaLink.media_file_id == replacement.id,
    ).first()
    if duplicate is not None and duplicate.id != link.id:
        raise HTTPException(409, "该图片已经关联到当前分镜，请直接使用已有图片")
    revisions = db.query(StoryboardMediaRevision).filter(
        StoryboardMediaRevision.link_id == link.id,
    ).order_by(StoryboardMediaRevision.version_number.desc()).all()
    next_version = max((item.version_number for item in revisions), default=0) + 1
    if not revisions:
        db.add(StoryboardMediaRevision(shot_id=shot.id, link_id=link.id, media_file_id=link.media_file_id, version_number=1))
        next_version = 2
    db.add(StoryboardMediaRevision(shot_id=shot.id, link_id=link.id, media_file_id=replacement.id, version_number=next_version))
    previous_id = link.media_file_id
    link.media_file_id = replacement.id
    db.commit()
    return {"success": True, "replaced": True, "link_id": link.id, "media_file_id": replacement.id,
            "version_number": next_version, "previous_media_file_id": previous_id}


@router.get("/library/images")
def list_reusable_library_images(db: Session = Depends(get_db)):
    images = db.query(MediaFile).filter(MediaFile.category == "image").order_by(MediaFile.created_at.desc()).limit(200).all()
    return {"images": [{"id": item.id, "name": item.name, "mime_type": item.mime_type, "file_size": item.file_size,
                        "download_url": f"/api/media/files/{item.id}/download"} for item in images]}


@router.post("/shots/{shot_id}/media/reuse")
def reuse_library_image_for_shot(shot_id: int, data: StoryboardImageReuseInput, db: Session = Depends(get_db)):
    media = db.query(MediaFile).filter(MediaFile.id == data.media_file_id, MediaFile.category == "image").first()
    if media is None:
        raise HTTPException(404, "素材库中不存在该图片")
    link, existed = _attach_media_to_shot(db, media, shot_id)
    db.commit()
    return {"success": True, "already_linked": existed, "link_id": link.id, "media_file_id": media.id, "shot_id": shot_id}


@router.put("/shots/{shot_id}/media/{link_id}")
def replace_shot_media(shot_id: int, link_id: int, data: StoryboardImageReplaceInput, db: Session = Depends(get_db)):
    return _replace_shot_media(db, shot_id, link_id, data.media_file_id)


@router.get("/shots/{shot_id}/media/{link_id}/versions")
def list_shot_media_versions(shot_id: int, link_id: int, db: Session = Depends(get_db)):
    shot = db.query(StoryboardShot).filter(StoryboardShot.id == shot_id).first()
    if shot is None:
        raise HTTPException(404, "分镜镜头不存在")
    link = db.query(StoryboardMediaLink).filter(StoryboardMediaLink.id == link_id, StoryboardMediaLink.shot_id == shot.id,
        StoryboardMediaLink.project_id == shot.project_id, StoryboardMediaLink.episode_id == shot.episode_id).first()
    if link is None:
        raise HTTPException(404, "该图片关联不存在，或不属于当前分镜")
    rows = db.query(StoryboardMediaRevision).filter(StoryboardMediaRevision.link_id == link.id).order_by(StoryboardMediaRevision.version_number.desc()).all()
    if not rows:
        rows = [type("CurrentRevision", (), {"version_number": 1, "media_file_id": link.media_file_id, "created_at": link.created_at})()]
    elif not any(row.media_file_id == link.media_file_id for row in rows):
        rows.append(type("CurrentRevision", (), {"version_number": max(row.version_number for row in rows) + 1, "media_file_id": link.media_file_id, "created_at": link.created_at})())
    result = []
    current_version = max(row.version_number for row in rows)
    for row in sorted(rows, key=lambda item: item.version_number, reverse=True):
        media = db.query(MediaFile).filter(MediaFile.id == row.media_file_id, MediaFile.category == "image").first()
        if media:
            result.append({"version_number": row.version_number, "media_file_id": media.id, "name": media.name,
                "file_size": media.file_size, "download_url": f"/api/media/files/{media.id}/download",
                "current": row.version_number == current_version})
    return {"shot_id": shot.id, "link_id": link.id, "versions": result}


@router.post("/shots/{shot_id}/media/{link_id}/restore/{version_number}")
def restore_shot_media_version(shot_id: int, link_id: int, version_number: int, db: Session = Depends(get_db)):
    shot = db.query(StoryboardShot).filter(StoryboardShot.id == shot_id).first()
    if shot is None:
        raise HTTPException(404, "分镜镜头不存在")
    link = db.query(StoryboardMediaLink).filter(StoryboardMediaLink.id == link_id, StoryboardMediaLink.shot_id == shot.id,
        StoryboardMediaLink.project_id == shot.project_id, StoryboardMediaLink.episode_id == shot.episode_id).first()
    if link is None:
        raise HTTPException(404, "该图片关联不存在，或不属于当前分镜")
    revision = db.query(StoryboardMediaRevision).filter(StoryboardMediaRevision.link_id == link.id,
        StoryboardMediaRevision.version_number == version_number).first()
    if revision is None:
        raise HTTPException(404, "找不到指定图片版本")
    return _replace_shot_media(db, shot_id, link_id, revision.media_file_id)

def _remove_media_link_from_shot(db: Session, shot_id: int, link_id: int):
    shot = db.query(StoryboardShot).filter(StoryboardShot.id == shot_id).first()
    if shot is None:
        raise HTTPException(404, "分镜镜头不存在")
    link = db.query(StoryboardMediaLink).filter(
        StoryboardMediaLink.id == link_id,
        StoryboardMediaLink.shot_id == shot.id,
        StoryboardMediaLink.project_id == shot.project_id,
        StoryboardMediaLink.episode_id == shot.episode_id,
    ).first()
    if link is None:
        raise HTTPException(404, "该图片关联不存在，或不属于当前分镜")
    db.delete(link)
    db.commit()
    return {"success": True, "unlinked": True, "media_file_id": link.media_file_id}


@router.delete("/shots/{shot_id}/media/{link_id}")
def unlink_shot_media(shot_id: int, link_id: int, db: Session = Depends(get_db)):
    """Remove only the storyboard association; keep the media file in the library."""
    return _remove_media_link_from_shot(db, shot_id, link_id)


def _attach_media_to_shot(db: Session, media: MediaFile, shot_id: int):
    shot = db.query(StoryboardShot).filter(StoryboardShot.id == shot_id).first()
    if shot is None:
        raise HTTPException(404, "所选分镜镜头不存在")
    project = db.query(StoryProject).filter(StoryProject.id == shot.project_id).first()
    episode = db.query(EpisodePlan).filter(EpisodePlan.id == shot.episode_id).first()
    if project is None or episode is None or episode.project_id != project.id:
        raise HTTPException(409, "分镜所属项目或分集关系异常，无法关联图片")
    existing = db.query(StoryboardMediaLink).filter(
        StoryboardMediaLink.shot_id == shot.id,
        StoryboardMediaLink.media_file_id == media.id,
    ).first()
    if existing:
        return existing, True
    link = StoryboardMediaLink(
        project_id=project.id,
        episode_id=episode.id,
        shot_id=shot.id,
        media_file_id=media.id,
    )
    db.add(link)
    return link, False


@router.post("/tasks/{prompt_id}/archive")
def archive_local_image_task_output(
    prompt_id: str,
    data: LocalImageArchiveInput,
    db: Session = Depends(get_db),
):
    """Archive one explicitly selected completed ComfyUI output into the local media library."""
    if not PROMPT_ID_RE.fullmatch(prompt_id):
        raise HTTPException(400, "任务编号格式无效")
    selected_filename, selected_subfolder, _ = _safe_output_image(data.model_dump(exclude={"shot_id"}))
    if data.shot_id is not None:
        target_shot = db.query(StoryboardShot).filter(StoryboardShot.id == data.shot_id).first()
        if target_shot is None:
            raise HTTPException(404, "所选分镜镜头不存在，请刷新项目和分镜列表")
        target_project = db.query(StoryProject).filter(StoryProject.id == target_shot.project_id).first()
        target_episode = db.query(EpisodePlan).filter(EpisodePlan.id == target_shot.episode_id).first()
        if target_project is None or target_episode is None or target_episode.project_id != target_project.id:
            raise HTTPException(409, "所选分镜的项目/分集关系异常，无法归档关联")
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
            if data.shot_id is not None:
                _attach_media_to_shot(db, media, data.shot_id)
                db.commit()
            return {
                "success": True, "already_archived": True, "media_file_id": media.id,
                "name": media.name, "path": media.file_path,
                "linked_to_shot": data.shot_id is not None, "shot_id": data.shot_id,
                "message": "图片已存在于素材库" + ("，并已关联到所选分镜。" if data.shot_id is not None else "，没有重复保存。"),
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
        db.flush()
        if data.shot_id is not None:
            _attach_media_to_shot(db, media, data.shot_id)
        db.commit()
        db.refresh(media)
        return {
            "success": True, "already_archived": False, "media_file_id": media.id,
            "name": media.name, "category": media.category, "mime_type": media.mime_type,
            "size": media.file_size, "path": media.file_path,
            "url": f"/api/media/files/{media.id}/download",
            "linked_to_shot": data.shot_id is not None, "shot_id": data.shot_id,
            "message": "图片已保存到本地素材库" + ("，并已关联到所选分镜。" if data.shot_id is not None else "。"),
        }
    except Exception:
        db.rollback()
        destination.unlink(missing_ok=True)
        temporary.unlink(missing_ok=True)
        raise


class StoryboardConsistencyInput(BaseModel):
    visual_style: str = Field(default="", max_length=1200)
    negative_prompt: str = Field(default="", max_length=2000)
    character_profiles: dict[str, str] = Field(default_factory=dict)
    scene_profiles: dict[str, str] = Field(default_factory=dict)
    prop_profiles: dict[str, str] = Field(default_factory=dict)
    costume_profiles: dict[str, dict[str, str]] = Field(default_factory=dict)
    shot_costume_variants: dict[str, dict[str, str]] = Field(default_factory=dict)
    character_reference_images: dict[str, int] = Field(default_factory=dict)
    scene_reference_images: dict[str, int] = Field(default_factory=dict)
    prop_reference_images: dict[str, int] = Field(default_factory=dict)

    @field_validator("visual_style", "negative_prompt")
    @classmethod
    def clean_consistency_text(cls, value):
        return value.strip()

    @field_validator("character_profiles", "scene_profiles", "prop_profiles")
    @classmethod
    def validate_profiles(cls, value):
        if len(value) > 100:
            raise ValueError("每类一致性档案最多保存 100 项")
        cleaned = {}
        for key, description in value.items():
            if not str(key).isdigit() or int(key) < 1:
                raise ValueError("素材编号必须为正整数")
            if not isinstance(description, str) or len(description.strip()) > 1200:
                raise ValueError("每条外观描述最多 1200 个字符")
            cleaned[str(int(key))] = description.strip()
        return cleaned

    @field_validator("costume_profiles")
    @classmethod
    def validate_costumes(cls, value):
        if len(value) > 100:
            raise ValueError("最多为 100 个角色保存圣衣/服装档案")
        cleaned = {}
        for character_id, variants in value.items():
            if not str(character_id).isdigit() or int(character_id) < 1 or not isinstance(variants, dict) or len(variants) > 30:
                raise ValueError("圣衣档案角色编号或版本列表无效")
            cleaned[str(int(character_id))] = {}
            for variant_id, description in variants.items():
                if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", str(variant_id)):
                    raise ValueError("圣衣/服装版本编号仅允许字母、数字、下划线和连字符")
                if not isinstance(description, str) or not description.strip() or len(description.strip()) > 1200:
                    raise ValueError("每条圣衣/服装描述必须为 1 至 1200 个字符")
                cleaned[str(int(character_id))][str(variant_id)] = description.strip()
        return cleaned

    @field_validator("shot_costume_variants")
    @classmethod
    def validate_shot_costumes(cls, value):
        if len(value) > 200:
            raise ValueError("最多为 200 个分镜指定服装版本")
        cleaned = {}
        for shot_id, assignments in value.items():
            if not str(shot_id).isdigit() or int(shot_id) < 1 or not isinstance(assignments, dict) or len(assignments) > 50:
                raise ValueError("分镜服装指定数据无效")
            normalized = {}
            for character_id, variant_id in assignments.items():
                if not str(character_id).isdigit() or int(character_id) < 1 or not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", str(variant_id)):
                    raise ValueError("分镜角色或服装版本编号无效")
                normalized[str(int(character_id))] = str(variant_id)
            cleaned[str(int(shot_id))] = normalized
        return cleaned

    @field_validator("character_reference_images", "scene_reference_images", "prop_reference_images")
    @classmethod
    def validate_reference_image_ids(cls, value):
        if len(value) > 100:
            raise ValueError("每类最多绑定 100 张参考图片")
        cleaned = {}
        for asset_id, media_id in value.items():
            if not str(asset_id).isdigit() or int(asset_id) < 1 or not isinstance(media_id, int) or media_id < 1:
                raise ValueError("参考图片素材编号无效")
            cleaned[str(int(asset_id))] = media_id
        return cleaned


class StoryboardConsistencyPreflightInput(BaseModel):
    shot_ids: list[int] = Field(default_factory=list, max_length=200)


def _get_visual_consistency(project: StoryProject):
    settings = project.settings if isinstance(project.settings, dict) else {}
    profile = settings.get("visual_consistency", {})
    return profile if isinstance(profile, dict) else {}


def _consistency_prompt_for_shot(shot: StoryboardShot, profile: dict):
    sections = []
    style = str(profile.get("visual_style", "")).strip()
    if style:
        sections.append("【全项目统一画风】" + style)
    negative = str(profile.get("negative_prompt", "")).strip()
    if negative:
        sections.append("【全项目统一避免项】" + negative)
    characters = profile.get("character_profiles", {})
    if isinstance(characters, dict):
        for asset_id in shot.character_ids if isinstance(shot.character_ids, list) else []:
            description = characters.get(str(asset_id), "")
            if description:
                sections.append(f"【角色外观锁定 #{asset_id}】{description}")
    scenes = profile.get("scene_profiles", {})
    if isinstance(scenes, dict) and shot.scene_asset_id:
        description = scenes.get(str(shot.scene_asset_id), "")
        if description:
            sections.append(f"【场景外观锁定 #{shot.scene_asset_id}】{description}")
    props = profile.get("prop_profiles", {})
    if isinstance(props, dict):
        for asset_id in shot.prop_ids if isinstance(shot.prop_ids, list) else []:
            description = props.get(str(asset_id), "")
            if description:
                sections.append(f"【道具外观锁定 #{asset_id}】{description}")
    costume_profiles = profile.get("costume_profiles", {})
    shot_variants = profile.get("shot_costume_variants", {}).get(str(shot.id), {})
    if isinstance(costume_profiles, dict) and isinstance(shot_variants, dict):
        for character_id in shot.character_ids if isinstance(shot.character_ids, list) else []:
            variant_id = shot_variants.get(str(character_id))
            description = costume_profiles.get(str(character_id), {}).get(variant_id, "") if variant_id else ""
            if description:
                sections.append(f"【角色 #{character_id} 本镜头指定圣衣/服装版本 {variant_id}，不得与其他版本混淆】{description}")
    reference_groups = [
        ("character_reference_images", shot.character_ids if isinstance(shot.character_ids, list) else [], "角色"),
        ("scene_reference_images", [shot.scene_asset_id] if shot.scene_asset_id else [], "场景"),
        ("prop_reference_images", shot.prop_ids if isinstance(shot.prop_ids, list) else [], "道具"),
    ]
    for field_name, asset_ids, label in reference_groups:
        refs = profile.get(field_name, {})
        if isinstance(refs, dict):
            for asset_id in asset_ids:
                media_id = refs.get(str(asset_id))
                if media_id:
                    sections.append(f"【{label}参考图已绑定本地素材库图片 #{media_id}；生成前请在本地素材库查看并作为视觉参考】")
    if not sections:
        return ""
    return "\\n".join(sections)


def _consistency_preflight(project: StoryProject, shots: list[StoryboardShot], profile: dict):
    results = []
    missing = {"characters": set(), "scenes": set(), "props": set()}
    for shot in shots:
        character_ids = shot.character_ids if isinstance(shot.character_ids, list) else []
        prop_ids = shot.prop_ids if isinstance(shot.prop_ids, list) else []
        character_profiles = profile.get("character_profiles", {})
        scene_profiles = profile.get("scene_profiles", {})
        prop_profiles = profile.get("prop_profiles", {})
        for asset_id in character_ids:
            if str(asset_id) not in character_profiles:
                missing["characters"].add(str(asset_id))
        if shot.scene_asset_id and str(shot.scene_asset_id) not in scene_profiles:
            missing["scenes"].add(str(shot.scene_asset_id))
        for asset_id in prop_ids:
            if str(asset_id) not in prop_profiles:
                missing["props"].add(str(asset_id))
        consistency_prompt = _consistency_prompt_for_shot(shot, profile)
        original = (shot.image_prompt or "").strip()
        preview = original
        if consistency_prompt:
            preview = (original + "\\n\\n" if original else "") + consistency_prompt
        results.append({
            "shot_id": shot.id,
            "shot_number": shot.shot_number,
            "scene_name": shot.scene_name,
            "character_count": len(character_ids),
            "has_scene_asset": bool(shot.scene_asset_id),
            "prop_count": len(prop_ids),
            "consistency_rules_applied": bool(consistency_prompt),
            "prompt_preview": preview,
        })
    missing_clean = {key: sorted(values, key=lambda item: int(item)) for key, values in missing.items()}
    missing_count = sum(len(values) for values in missing_clean.values())
    return {
        "project_id": project.id,
        "shot_count": len(shots),
        "ready": missing_count == 0,
        "missing_profiles": missing_clean,
        "missing_profile_count": missing_count,
        "shots": results,
        "message": "所有已引用角色、场景和道具均有统一外观档案。" if missing_count == 0 else "部分角色、场景或道具缺少统一外观档案；补齐后可获得更稳定的跨分镜一致性。",
        "local_only": True,
        "cloud_api_used": False,
    }


@router.get("/projects/{project_id}/consistency")
def get_storyboard_consistency(project_id: int, db: Session = Depends(get_db)):
    project = db.query(StoryProject).filter(StoryProject.id == project_id).first()
    if project is None:
        raise HTTPException(404, "漫剧项目不存在")
    return {"project_id": project.id, "profile": _get_visual_consistency(project), "local_only": True}


@router.put("/projects/{project_id}/consistency")
def save_storyboard_consistency(project_id: int, data: StoryboardConsistencyInput, db: Session = Depends(get_db)):
    project = db.query(StoryProject).filter(StoryProject.id == project_id).first()
    if project is None:
        raise HTTPException(404, "漫剧项目不存在")
    reference_ids = set(data.character_reference_images.values()) | set(data.scene_reference_images.values()) | set(data.prop_reference_images.values())
    if reference_ids:
        valid_ids = {row.id for row in db.query(MediaFile.id).filter(MediaFile.id.in_(reference_ids), MediaFile.category == "image").all()}
        if valid_ids != reference_ids:
            raise HTTPException(404, "参考图中包含素材库不存在的图片，请重新选择")
    settings = dict(project.settings) if isinstance(project.settings, dict) else {}
    settings["visual_consistency"] = data.model_dump()
    project.settings = settings
    db.commit()
    return {"success": True, "project_id": project.id, "profile": data.model_dump(), "local_only": True}


@router.post("/projects/{project_id}/consistency/preflight")
def preflight_storyboard_consistency(project_id: int, data: StoryboardConsistencyPreflightInput, db: Session = Depends(get_db)):
    project = db.query(StoryProject).filter(StoryProject.id == project_id).first()
    if project is None:
        raise HTTPException(404, "漫剧项目不存在")
    query = db.query(StoryboardShot).filter(StoryboardShot.project_id == project.id)
    if data.shot_ids:
        query = query.filter(StoryboardShot.id.in_(data.shot_ids))
    shots = query.order_by(StoryboardShot.sort_order, StoryboardShot.shot_number).limit(200).all()
    if data.shot_ids and {shot.id for shot in shots} != set(data.shot_ids):
        raise HTTPException(404, "所选分镜中包含不存在或不属于当前项目的镜头")
    if not shots:
        raise HTTPException(404, "当前项目没有可检查的分镜")
    return _consistency_preflight(project, shots, _get_visual_consistency(project))


@router.post("/projects/{project_id}/consistency/apply")
def apply_storyboard_consistency(project_id: int, data: StoryboardConsistencyPreflightInput, db: Session = Depends(get_db)):
    project = db.query(StoryProject).filter(StoryProject.id == project_id).first()
    if project is None:
        raise HTTPException(404, "漫剧项目不存在")
    query = db.query(StoryboardShot).filter(StoryboardShot.project_id == project.id)
    if data.shot_ids:
        query = query.filter(StoryboardShot.id.in_(data.shot_ids))
    shots = query.order_by(StoryboardShot.sort_order, StoryboardShot.shot_number).limit(200).all()
    if data.shot_ids and {shot.id for shot in shots} != set(data.shot_ids):
        raise HTTPException(404, "所选分镜中包含不存在或不属于当前项目的镜头")
    if not shots:
        raise HTTPException(404, "当前项目没有可应用的分镜")
    profile = _get_visual_consistency(project)
    preflight = _consistency_preflight(project, shots, profile)
    if preflight["missing_profile_count"]:
        raise HTTPException(409, "存在未配置统一外观的角色、场景或道具；请先补齐档案再应用。")
    updated = 0
    for shot in shots:
        rules = _consistency_prompt_for_shot(shot, profile)
        if not rules:
            continue
        original = (shot.image_prompt or "").strip()
        marker_start = "【项目一致性规则开始】"
        marker_end = "【项目一致性规则结束】"
        if marker_start in original and marker_end in original:
            original = original.split(marker_start, 1)[0].rstrip()
        shot.image_prompt = (original + "\\n\\n" if original else "") + marker_start + "\\n" + rules + "\\n" + marker_end
        updated += 1
    db.commit()
    return {"success": True, "updated_shot_count": updated, "preflight": preflight, "local_only": True, "cloud_api_used": False}
