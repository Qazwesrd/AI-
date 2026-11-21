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
from app.models.media import MediaFile, MediaTask
from app.models.story import StoryProject, EpisodePlan, StoryboardShot, StoryboardMediaLink, StoryboardMediaRevision, StoryAssetLink
from app.models.visual import VisualAsset
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
REFERENCE_NODE_TYPES = ("LoadImage", "CLIPVisionLoader", "IPAdapterUnifiedLoader", "IPAdapter", "IPAdapterAdvanced", "IPAdapterModelLoader", "PrepImageForClipVision")
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






class LocalImageBatchStatusItem(BaseModel):
    shot_id: int = Field(ge=1)
    shot_number: int = Field(ge=1)
    prompt_id: str = Field(min_length=32, max_length=36)

    @field_validator("prompt_id")
    @classmethod
    def validate_prompt_id(cls, value):
        if not PROMPT_ID_RE.fullmatch(value):
            raise ValueError("任务编号格式无效")
        return value


class LocalImageBatchStatusInput(BaseModel):
    tasks: list[LocalImageBatchStatusItem] = Field(min_length=1, max_length=20)

class LocalImageBatchInput(BaseModel):
    project_id: int = Field(ge=1)
    episode_id: int = Field(ge=1)
    shot_ids: list[int] = Field(min_length=1, max_length=20)
    checkpoint: str = Field(min_length=1, max_length=240)
    style: Literal["国漫", "仿真人", "3D漫", "2D漫"] = "国漫"
    width: int = Field(default=768, ge=512, le=1536)
    height: int = Field(default=768, ge=512, le=1536)
    steps: int = Field(default=24, ge=1, le=40)
    cfg: float = Field(default=7.0, ge=1.0, le=12.0)
    confirm_local_generation: bool = False

    @field_validator("shot_ids")
    @classmethod
    def validate_shot_ids(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("分镜编号不能重复")
        return value

    @field_validator("checkpoint")
    @classmethod
    def validate_batch_checkpoint(cls, value):
        value = value.strip()
        if not CHECKPOINT_RE.fullmatch(value) or ".." in value or "//" in value or "\\\\" in value:
            raise ValueError("图片模型名称格式无效")
        return value

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
    allowed = allowed or bool(re.fullmatch(r"/object_info/(?:LoadImage|CLIPVisionLoader|IPAdapterUnifiedLoader|IPAdapter|IPAdapterAdvanced|IPAdapterModelLoader|PrepImageForClipVision)", path))
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




def _node_input_choices(node_info: dict, input_name: str):
    inputs = node_info.get("input") if isinstance(node_info, dict) else None
    for section in ("required", "optional"):
        section_data = inputs.get(section) if isinstance(inputs, dict) else None
        spec = section_data.get(input_name) if isinstance(section_data, dict) else None
        if isinstance(spec, list) and spec and isinstance(spec[0], list):
            return [value for value in spec[0] if isinstance(value, str)]
    return []


@router.get("/reference-capabilities")
def inspect_local_reference_capabilities():
    """Inspect installed ComfyUI nodes without installing or running any workflow."""
    nodes = {}
    for node_type in REFERENCE_NODE_TYPES:
        try:
            info = _local_request("/object_info/" + node_type, timeout=4.0)
        except HTTPException as exc:
            if exc.status_code == 502:
                nodes[node_type] = {"available": False, "reason": "节点不存在或 ComfyUI 未提供节点信息"}
                continue
            raise
        node_info = info.get(node_type)
        available = isinstance(node_info, dict)
        nodes[node_type] = {
            "available": available,
            "input_names": sorted(
                set((node_info.get("input", {}).get("required", {}) if available else {}).keys())
                | set((node_info.get("input", {}).get("optional", {}) if available else {}).keys())
            ),
        } if available else {"available": False, "reason": "未安装该节点"}
        if available and node_type in ("IPAdapterUnifiedLoader", "IPAdapterModelLoader"):
            nodes[node_type]["adapter_models"] = _node_input_choices(node_info, "ipadapter_file")
        if available and node_type == "CLIPVisionLoader":
            nodes[node_type]["clip_vision_models"] = _node_input_choices(node_info, "clip_name")

    has_load_image = nodes.get("LoadImage", {}).get("available", False)
    has_clip_vision = nodes.get("CLIPVisionLoader", {}).get("available", False)
    unified_presets = _node_input_choices(
        _local_request("/object_info/IPAdapterUnifiedLoader"), "preset"
    ) if nodes.get("IPAdapterUnifiedLoader", {}).get("available", False) else []
    adapter_models = nodes.get("IPAdapterModelLoader", {}).get("adapter_models", [])
    unified_model_files = nodes.get("IPAdapterUnifiedLoader", {}).get("adapter_models", [])
    has_unified_adapter = nodes.get("IPAdapterUnifiedLoader", {}).get("available", False) and bool(unified_presets or unified_model_files)
    has_model_loader = nodes.get("IPAdapterModelLoader", {}).get("available", False) and bool(adapter_models)
    has_adapter = has_unified_adapter or has_model_loader
    apply_type = "IPAdapterAdvanced" if nodes.get("IPAdapterAdvanced", {}).get("available", False) else "IPAdapter"
    apply_info = _local_request("/object_info/" + apply_type) if nodes.get(apply_type, {}).get("available", False) else {}
    apply_schema = apply_info.get(apply_type, {})
    apply_required = set((apply_schema.get("input") or {}).get("required", {}).keys()) if isinstance(apply_schema, dict) else set()
    needs_clip_vision = "clip_vision" in apply_required
    clip_models = nodes.get("CLIPVisionLoader", {}).get("clip_vision_models", [])
    apply_inputs = _node_input_names(apply_schema) if isinstance(apply_schema, dict) else set()
    structural_ready = has_load_image and has_adapter and {"model", "ipadapter"}.issubset(apply_inputs)
    model_ready = (has_unified_adapter or has_model_loader) and (not needs_clip_vision or (has_clip_vision and bool(clip_models)))
    return {
        "engine": "ComfyUI",
        "status": "ready" if structural_ready and model_ready else "needs_setup",
        "reference_nodes": nodes,
        "reference_conditioning_nodes_ready": structural_ready,
        "reference_conditioning_models_ready": model_ready,
        "reference_conditioning_ready": structural_ready and model_ready,
        "missing_node_types": [
            name for name, present in (
                ("LoadImage", has_load_image),
                ("IPAdapterUnifiedLoader or IPAdapterModelLoader with available models", has_adapter),
                ("IPAdapter or IPAdapterAdvanced", {"model", "ipadapter"}.issubset(apply_inputs)),
            ) if not present
        ] + (["CLIPVisionLoader"] if needs_clip_vision and not has_clip_vision else []),
        "missing_model_files": [name for name, present in (
            ("IP-Adapter model", bool(unified_presets or unified_model_files or adapter_models)),
            *([( "CLIP Vision model", bool(clip_models))] if needs_clip_vision else []),
        ) if not present],
        "local_only": True,
        "network_used": False,
        "downloaded": False,
        "installed_anything": False,
        "generation_started": False,
        "message": "已检测到参考图条件生图所需节点和模型。" if structural_ready and model_ready else "已完成本机兼容性检查；缺少的节点或模型需要你自行确认安装，本程序不会自动下载。",
    }



def _node_input_names(node_info: dict) -> set[str]:
    inputs = node_info.get("input") if isinstance(node_info, dict) else None
    if not isinstance(inputs, dict):
        return set()
    names = set()
    for section in ("required", "optional"):
        values = inputs.get(section)
        if isinstance(values, dict):
            names.update(values.keys())
    return names


def _first_node_choice(node_info: dict, input_name: str) -> str | None:
    choices = _node_input_choices(node_info, input_name)
    return choices[0] if choices else None


def build_reference_conditioned_workflow(
    payload: LocalImageGenerateInput,
    seed: int,
    object_infos: dict,
    reference_images: list[dict],
) -> dict:
    """Build an IP-Adapter graph from live ComfyUI schemas and confirmed local uploads."""
    if not reference_images or len(reference_images) > 10:
        raise ValueError("参考图数量必须为 1 到 10 张")
    load_info = object_infos.get("LoadImage")
    if not isinstance(load_info, dict) or not _node_input_names(load_info):
        raise ValueError("本机 ComfyUI 缺少可用的 LoadImage 节点")
    apply_type = next(
        (name for name in ("IPAdapterAdvanced", "IPAdapter") if isinstance(object_infos.get(name), dict)),
        None,
    )
    apply_info = object_infos.get(apply_type) if apply_type else None
    if not apply_type or not _node_input_names(apply_info):
        raise ValueError("本机 ComfyUI 缺少可用的 IP-Adapter 应用节点")

    unified_info = object_infos.get("IPAdapterUnifiedLoader")
    model_loader_info = object_infos.get("IPAdapterModelLoader")
    use_unified = isinstance(unified_info, dict) and "model" in _node_input_names(unified_info) and bool(_first_node_choice(unified_info, "preset"))
    if use_unified:
        adapter_info = unified_info
        adapter_type = "IPAdapterUnifiedLoader"
        adapter_inputs = {"model": ["4", 0], "preset": _first_node_choice(unified_info, "preset")}
    else:
        if not isinstance(model_loader_info, dict):
            raise ValueError("缺少 IPAdapterUnifiedLoader 和 IPAdapterModelLoader")
        adapter_model = _first_node_choice(model_loader_info, "ipadapter_file")
        if not adapter_model:
            raise ValueError("本机没有可用的 IP-Adapter 模型文件")
        adapter_info = model_loader_info
        adapter_type = "IPAdapterModelLoader"
        adapter_inputs = {"ipadapter_file": adapter_model}

    apply_names = _node_input_names(apply_info)
    required_names = set((apply_info.get("input") or {}).get("required", {}).keys())
    supported = {"model", "ipadapter", "image", "weight", "weight_type", "combine_embeds", "start_at", "end_at", "embeds_scaling", "clip_vision"}
    if not {"model", "ipadapter", "image", "weight"}.issubset(apply_names):
        raise ValueError("当前 IP-Adapter 节点输入结构不兼容，缺少必要接口")
    if not {"model", "ipadapter", "image", "weight"}.issubset(supported):
        raise ValueError("IP-Adapter 节点不支持必要输入")

    workflow = build_txt2img_workflow(payload, seed)
    workflow["10"] = {"class_type": adapter_type, "inputs": adapter_inputs}
    next_model = ["10", 0]
    next_node_id = 11

    clip_info = object_infos.get("CLIPVisionLoader")
    clip_vision_link = None
    if "clip_vision" in apply_names and isinstance(clip_info, dict):
        clip_name = _first_node_choice(clip_info, "clip_name")
        if clip_name:
            clip_node_id = str(next_node_id)
            next_node_id += 1
            workflow[clip_node_id] = {"class_type": "CLIPVisionLoader", "inputs": {"clip_name": clip_name}}
            clip_vision_link = [clip_node_id, 0]

    for reference in reference_images:
        name = reference.get("name")
        subfolder = reference.get("subfolder", "")
        image_type = reference.get("type", "input")
        if (
            not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9 _.-]{1,240}", name)
            or ".." in name or "/" in name or "\\\\" in name
            or not isinstance(subfolder, str) or len(subfolder) > 240
            or (subfolder and (
                subfolder.startswith("/") or "\\\\" in subfolder or ":" in subfolder
                or "//" in subfolder or any(part in ("", ".", "..") for part in subfolder.split("/"))
                or not re.fullmatch(r"[A-Za-z0-9 _./-]+", subfolder)
            ))
            or image_type != "input"
        ):
            raise ValueError("ComfyUI 参考图文件名或输入目录不安全")
        image_node_id = str(next_node_id)
        apply_node_id = str(next_node_id + 1)
        next_node_id += 2
        workflow[image_node_id] = {
            "class_type": "LoadImage",
            "inputs": {"image": f"{subfolder}/{name}" if subfolder else name},
        }
        apply_inputs = {
            "model": next_model,
            "ipadapter": ["10", 1] if use_unified else ["10", 0],
            "image": [image_node_id, 0],
            "weight": 0.65,
        }
        if "weight_type" in apply_names:
            apply_inputs["weight_type"] = _first_node_choice(apply_info, "weight_type") or "linear"
        if "combine_embeds" in apply_names:
            apply_inputs["combine_embeds"] = _first_node_choice(apply_info, "combine_embeds") or "concat"
        if "start_at" in apply_names:
            apply_inputs["start_at"] = 0.0
        if "end_at" in apply_names:
            apply_inputs["end_at"] = 1.0
        if "embeds_scaling" in apply_names:
            apply_inputs["embeds_scaling"] = _first_node_choice(apply_info, "embeds_scaling") or "V only"
        if "clip_vision" in apply_names:
            if clip_vision_link is None and "clip_vision" in required_names:
                raise ValueError("当前 IP-Adapter 节点要求 CLIP Vision 模型，但本机未检测到可用模型")
            if clip_vision_link is not None:
                apply_inputs["clip_vision"] = clip_vision_link
        workflow[apply_node_id] = {"class_type": apply_type, "inputs": apply_inputs}
        next_model = [apply_node_id, 0]

    workflow["3"]["inputs"]["model"] = next_model
    return workflow



def _prepare_batch_shots(data: LocalImageBatchInput, db: Session):
    shots = db.query(StoryboardShot).filter(
        StoryboardShot.project_id == data.project_id,
        StoryboardShot.episode_id == data.episode_id,
        StoryboardShot.id.in_(data.shot_ids),
    ).all()
    by_id = {shot.id: shot for shot in shots}
    rows = []
    missing = []
    for shot_id in data.shot_ids:
        shot = by_id.get(shot_id)
        if shot is None:
            missing.append({"shot_id": shot_id, "reason": "分镜不存在或不属于所选项目/分集"})
            continue
        prompt = (shot.image_prompt or "").strip()
        if len(prompt) < 3:
            missing.append({"shot_id": shot_id, "shot_number": shot.shot_number, "reason": "该分镜尚未填写图片提示词"})
            continue
        rows.append({
            "shot_id": shot.id,
            "shot_number": shot.shot_number,
            "scene_name": shot.scene_name or "",
            "prompt": prompt[:6000],
        })
    return rows, missing


@router.post("/batch/preflight")
def preflight_local_image_batch(data: LocalImageBatchInput, db: Session = Depends(get_db)):
    rows, missing = _prepare_batch_shots(data, db)
    return {
        "project_id": data.project_id,
        "episode_id": data.episode_id,
        "requested_count": len(data.shot_ids),
        "ready_count": len(rows),
        "missing_count": len(missing),
        "ready_shots": rows,
        "missing_shots": missing,
        "max_batch_size": 20,
        "will_submit_to_local_queue": False,
        "generation_started": False,
        "message": "预检完成。此操作没有提交任务；确认后才会将合格分镜提交到本机 ComfyUI 队列。",
    }


@router.post("/batch/generate")
def generate_local_image_batch(data: LocalImageBatchInput, db: Session = Depends(get_db)):
    if not data.confirm_local_generation:
        raise HTTPException(400, "批量生成会连续占用本机 CPU/GPU；请明确确认后再提交。")
    if data.width % 64 or data.height % 64:
        raise HTTPException(400, "图片宽度和高度必须是 64 的倍数。")
    rows, missing = _prepare_batch_shots(data, db)
    if not rows:
        raise HTTPException(400, "所选分镜没有可生成的图片提示词；请先完成分镜提示词。")
    object_info = _local_request("/object_info/CheckpointLoaderSimple")
    if data.checkpoint not in _checkpoint_names(object_info):
        raise HTTPException(404, "所选图片模型未安装在本机 ComfyUI 中；本程序不会自动下载模型。")
    submitted = []
    failed = []
    for row in rows:
        seed = uuid.uuid4().int % (2 ** 32)
        payload = LocalImageGenerateInput(
            checkpoint=data.checkpoint, prompt=row["prompt"], style=data.style,
            width=data.width, height=data.height, steps=data.steps, cfg=data.cfg,
            seed=seed, confirm_local_generation=True,
        )
        try:
            result = _local_request("/prompt", {
                "prompt": build_txt2img_workflow(payload, seed),
                "client_id": str(uuid.uuid4()),
            }, timeout=15.0)
            prompt_id = result.get("prompt_id")
            if not isinstance(prompt_id, str) or not PROMPT_ID_RE.fullmatch(prompt_id):
                raise HTTPException(502, "ComfyUI 未返回有效任务编号")
            task_record = MediaTask(
                task_type="local_image_batch",
                provider="comfyui-local",
                model=data.checkpoint,
                status="queued",
                prompt=row["prompt"],
                remote_task_id=prompt_id,
                request_data={
                    "project_id": data.project_id,
                    "episode_id": data.episode_id,
                    "shot_id": row["shot_id"],
                    "shot_number": row["shot_number"],
                    "seed": seed,
                    "style": data.style,
                    "checkpoint": data.checkpoint,
                    "width": data.width,
                    "height": data.height,
                    "steps": data.steps,
                    "cfg": data.cfg,
                },
                result_data={"prompt_id": prompt_id, "images": []},
            )
            db.add(task_record)
            db.commit()
            submitted.append({
                "shot_id": row["shot_id"], "shot_number": row["shot_number"],
                "prompt_id": prompt_id, "seed": seed, "status": "queued",
                "history_id": task_record.id,
            })
        except (HTTPException, ValueError) as exc:
            db.rollback()
            failed.append({
                "shot_id": row["shot_id"], "shot_number": row["shot_number"],
                "reason": exc.detail if isinstance(exc, HTTPException) else str(exc),
            })
            break
        except Exception:
            db.rollback()
            failed.append({
                "shot_id": row["shot_id"], "shot_number": row["shot_number"],
                "reason": "任务可能已提交，但本地历史记录保存失败；请检查 ComfyUI 队列，不会自动重试。",
            })
            break
    return {
        "success": bool(submitted),
        "project_id": data.project_id,
        "episode_id": data.episode_id,
        "submitted_count": len(submitted),
        "failed_count": len(failed),
        "skipped_count": len(missing),
        "tasks": submitted,
        "failed_shots": failed,
        "skipped_shots": missing,
        "local_only": True,
        "network_used": False,
        "downloaded": False,
        "paid_api_used": False,
        "cloud_fallback": False,
        "history_persisted": True,
        "message": "已按分镜顺序提交到本机 ComfyUI 队列；任务编号、分镜、模型和生成参数已写入本地历史。请逐项检查状态并确认归档；不会自动重试。",
    }


@router.get("/batch/history")
def list_local_image_batch_history(
    project_id: int,
    episode_id: int,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    if limit < 1 or limit > 200:
        raise HTTPException(400, "历史记录查询数量必须在 1 到 200 之间。")
    project = db.query(StoryProject).filter(StoryProject.id == project_id).first()
    episode = db.query(EpisodePlan).filter(
        EpisodePlan.id == episode_id,
        EpisodePlan.project_id == project_id,
    ).first()
    if project is None or episode is None:
        raise HTTPException(404, "项目或分集不存在，或分集不属于所选项目。")
    records = db.query(MediaTask).filter(
        MediaTask.task_type.in_(("local_image_batch", "local_image_reference")),
        MediaTask.provider == "comfyui-local",
    ).order_by(MediaTask.created_at.desc(), MediaTask.id.desc()).limit(1000).all()
    items = []
    for record in records:
        request_data = record.request_data if isinstance(record.request_data, dict) else {}
        if request_data.get("project_id") != project_id or request_data.get("episode_id") != episode_id:
            continue
        result_data = record.result_data if isinstance(record.result_data, dict) else {}
        items.append({
            "history_id": record.id,
            "shot_id": request_data.get("shot_id"),
            "shot_number": request_data.get("shot_number"),
            "prompt_id": record.remote_task_id,
            "status": record.status,
            "seed": request_data.get("seed"),
            "checkpoint": record.model,
            "style": request_data.get("style"),
            "width": request_data.get("width"),
            "height": request_data.get("height"),
            "steps": request_data.get("steps"),
            "cfg": request_data.get("cfg"),
            "images": result_data.get("images", []) if isinstance(result_data.get("images"), list) else [],
            "error": record.error,
            "created_at": record.created_at.isoformat() if record.created_at else None,
            "updated_at": record.updated_at.isoformat() if record.updated_at else None,
        })
        if len(items) >= limit:
            break
    return {
        "project_id": project_id,
        "episode_id": episode_id,
        "count": len(items),
        "tasks": items,
        "persistent_local_history": True,
        "local_only": True,
    }


@router.post("/batch/tasks")
def get_local_image_batch_status(data: LocalImageBatchStatusInput, db: Session = Depends(get_db)):
    results = []
    for task in data.tasks:
        record = db.query(MediaTask).filter(
            MediaTask.task_type.in_(("local_image_batch", "local_image_reference")),
            MediaTask.provider == "comfyui-local",
            MediaTask.remote_task_id == task.prompt_id,
        ).first()
        try:
            history = _local_request("/history/" + task.prompt_id, timeout=5.0)
            item = history.get(task.prompt_id)
            if not isinstance(item, dict):
                status = "queued_or_running"
                images = []
            else:
                status_info = item.get("status") if isinstance(item.get("status"), dict) else {}
                status_text = status_info.get("status_str")
                if status_info.get("completed") is True:
                    status = "completed" if status_text in (None, "success") else "failed"
                elif status_text in ("error", "failed"):
                    status = "failed"
                else:
                    status = "running"
                outputs = item.get("outputs") if isinstance(item.get("outputs"), dict) else {}
                images = []
                for output in outputs.values():
                    if not isinstance(output, dict):
                        continue
                    for image in output.get("images", []) if isinstance(output.get("images"), list) else []:
                        if isinstance(image, dict):
                            filename = image.get("filename")
                            subfolder = image.get("subfolder", "")
                            if isinstance(filename, str) and filename and len(filename) <= 240 and ".." not in filename and "/" not in filename and "\\" not in filename and image.get("type", "output") == "output":
                                images.append({"filename": filename, "subfolder": subfolder if isinstance(subfolder, str) else "", "type": "output"})
            if record is not None:
                record.status = status
                record.result_data = {"prompt_id": task.prompt_id, "images": images[:20]}
                record.error = None if status != "failed" else "ComfyUI 报告任务失败"
                db.commit()
            results.append({
                "shot_id": task.shot_id, "shot_number": task.shot_number,
                "prompt_id": task.prompt_id, "status": status, "images": images[:20],
                "history_id": record.id if record else None,
            })
        except (HTTPException, ValueError) as exc:
            if record is not None:
                record.status = "unavailable"
                record.error = exc.detail if isinstance(exc, HTTPException) else str(exc)
                db.commit()
            results.append({
                "shot_id": task.shot_id, "shot_number": task.shot_number,
                "prompt_id": task.prompt_id, "status": "unavailable",
                "error": exc.detail if isinstance(exc, HTTPException) else str(exc),
                "images": [], "history_id": record.id if record else None,
            })
    return {
        "tasks": results,
        "completed_count": sum(item["status"] == "completed" for item in results),
        "running_count": sum(item["status"] in ("running", "queued_or_running") for item in results),
        "failed_count": sum(item["status"] == "failed" for item in results),
        "local_only": True,
        "network_used": False,
        "history_updated": True,
    }

@router.get("/model-library")
def inspect_local_image_model_library():
    """Read installed checkpoint metadata from the fixed local ComfyUI directory.

    This endpoint is read-only: it never downloads, hashes multi-GB weights,
    extracts archives, or starts an image-generation workflow.
    """
    import shutil

    from app.routers.local_image_runtime import _install_root, _runtime_mode, assess_image_hardware
    from app.routers.system import _get_hardware_profile

    install_root = _install_root()
    checkpoint_dir = install_root / "ComfyUI" / "models" / "checkpoints"
    hardware = {}
    try:
        hardware = _get_hardware_profile() or {}
    except Exception:
        hardware = {}
    hardware_assessment = assess_image_hardware(hardware)
    runtime_mode, adapter_name = _runtime_mode()

    disk_root = install_root
    while not disk_root.exists() and disk_root != disk_root.parent:
        disk_root = disk_root.parent
    try:
        free_disk_bytes = shutil.disk_usage(disk_root).free
    except OSError:
        free_disk_bytes = None

    installed = install_root.is_dir()
    models = []
    if checkpoint_dir.is_dir() and not checkpoint_dir.is_symlink():
        try:
            entries = sorted(checkpoint_dir.iterdir(), key=lambda item: item.name.casefold())
        except OSError:
            entries = []
        for entry in entries[:500]:
            if entry.is_symlink() or not entry.is_file() or entry.suffix.lower() not in {".safetensors", ".ckpt", ".pt", ".pth"}:
                continue
            try:
                stat = entry.stat()
            except OSError:
                continue
            if stat.st_size < 1:
                continue
            models.append({
                "name": entry.name,
                "format": entry.suffix.lower().lstrip("."),
                "size_bytes": stat.st_size,
                "modified_at": int(stat.st_mtime),
                "compatibility": "manual_check" if runtime_mode == "cpu" else "unverified",
                "compatibility_note": (
                    "当前设备按 CPU 模式运行；模型文件大小不能代表实际内存需求，需结合模型架构、分辨率和系统内存评估。"
                    if runtime_mode == "cpu"
                    else "已识别运行模式，但尚未验证该模型架构与显存需求；不能仅凭文件存在判定兼容。"
                ),
            })

    return {
        "status": "ready" if installed else "engine_not_installed",
        "engine_installed": installed,
        "directory": str(checkpoint_dir),
        "models": models,
        "model_count": len(models),
        "free_disk_bytes": free_disk_bytes,
        "ram_bytes": hardware_assessment.get("ram_bytes"),
        "vram_bytes": hardware_assessment.get("vram_bytes"),
        "gpu_level": hardware_assessment.get("gpu_level"),
        "runtime_mode": runtime_mode,
        "adapter_name": adapter_name,
        "hardware_notes": hardware_assessment.get("reasons", []),
        "hardware_note": hardware_assessment.get("note"),
        "local_only": True,
        "network_used": False,
        "downloaded": False,
        "installation_started": False,
        "note": "这里只读取本机模型文件名、格式和文件大小，不计算大型权重的 SHA-256，也不下载或安装模型。下载目录和兼容性预览将在后续阶段接入经过核验的官方模型清单。",
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
            raise ValueError("最多为 100 个角色保存服装版本档案")
        cleaned = {}
        for character_id, variants in value.items():
            if not str(character_id).isdigit() or int(character_id) < 1 or not isinstance(variants, dict) or len(variants) > 30:
                raise ValueError("服装档案角色编号或版本列表无效")
            cleaned[str(int(character_id))] = {}
            for variant_id, description in variants.items():
                if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", str(variant_id)):
                    raise ValueError("服装版本编号仅允许字母、数字、下划线和连字符")
                if not isinstance(description, str) or not description.strip() or len(description.strip()) > 1200:
                    raise ValueError("每条服装描述必须为 1 至 1200 个字符")
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
                sections.append(f"【角色 #{character_id} 本镜头指定服装版本 {variant_id}，不得与其他版本混淆】{description}")
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
    return "\n".join(sections)


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
            preview = (original + "\n\n" if original else "") + consistency_prompt
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


class ReferenceImageUploadInput(BaseModel):
    asset_id: int = Field(ge=1)
    media_file_id: int = Field(ge=1)
    category: Literal["characters", "scenes", "props"]
    confirm_local_upload: bool = False


class ReferenceConditionedGenerateInput(BaseModel):
    shot_id: int = Field(ge=1)
    checkpoint: str = Field(min_length=1, max_length=240)
    negative_prompt: str = Field(default="", max_length=6000)
    style: Literal["国漫", "仿真人", "3D漫", "2D漫"] = "国漫"
    width: int = Field(default=768, ge=512, le=1536)
    height: int = Field(default=768, ge=512, le=1536)
    steps: int = Field(default=24, ge=1, le=40)
    cfg: float = Field(default=7.0, ge=1.0, le=12.0)
    seed: int | None = Field(default=None, ge=0, le=4294967295)
    confirm_local_generation: bool = False


class ReferenceConditioningPreflightInput(BaseModel):
    shot_ids: list[int] = Field(default_factory=list, max_length=200)


def _inspect_reference_media_file(media: MediaFile):
    """Validate a reference image stays inside managed storage and matches its bytes."""
    storage_root = Path(STORAGE).resolve()
    candidate = Path(media.file_path)
    try:
        resolved = candidate.resolve(strict=True)
        if os.path.commonpath((str(storage_root), str(resolved))) != str(storage_root):
            return {"ready": False, "reason": "图片路径不在本地素材库目录内"}
        if not resolved.is_file():
            return {"ready": False, "reason": "素材路径不是普通文件"}
        size = resolved.stat().st_size
        if size <= 0 or size > MAX_IMAGE_BYTES:
            return {"ready": False, "reason": "参考图为空或超过 25 MiB 安全上限"}
        with resolved.open("rb") as stream:
            header = stream.read(16)
    except (OSError, ValueError):
        return {"ready": False, "reason": "本地参考图文件不存在或无法安全读取"}

    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
    elif header.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
    elif len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        mime = "image/webp"
    else:
        return {"ready": False, "reason": "文件内容不是有效的 PNG、JPEG 或 WebP 图片"}
    if media.mime_type and media.mime_type not in (mime, "application/octet-stream"):
        return {"ready": False, "reason": "图片文件内容与素材库 MIME 类型不一致"}
    return {"ready": True, "mime_type": mime, "size": size, "local_path_validated": True}


def _upload_reference_to_local_comfyui(media: MediaFile, project_id: int, category: str, asset_id: int):
    validation = _inspect_reference_media_file(media)
    if not validation["ready"]:
        raise HTTPException(400, validation["reason"])
    path = Path(media.file_path).resolve(strict=True)
    suffix = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}[validation["mime_type"]]
    filename = f"ref_p{project_id}_{category}_{asset_id}_{uuid.uuid4().hex}{suffix}"
    subfolder = f"ai_comic_references/p{project_id}"
    boundary = "----AIComicStudio" + uuid.uuid4().hex
    fields = [
        ("type", "input"),
        ("subfolder", subfolder),
        ("overwrite", "false"),
    ]
    chunks = []
    for name, value in fields:
        chunks.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
            value.encode("utf-8") + b"\r\n",
        ])
    chunks.extend([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="image"; filename="{filename}"\r\n'.encode(),
        f'Content-Type: {validation["mime_type"]}\r\n\r\n'.encode(),
    ])
    with path.open("rb") as stream:
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            chunks.append(block)
    chunks.append(f"\r\n--{boundary}--\r\n".encode())
    body = b"".join(chunks)
    request = Request(
        COMFYUI_BASE_URL + "/upload/image",
        data=body,
        headers={"Accept": "application/json", "Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with OPENER.open(request, timeout=30.0) as response:
            raw = response.read(64 * 1024 + 1)
        if len(raw) > 64 * 1024:
            raise HTTPException(502, "ComfyUI 上传响应超过安全上限")
        result = json.loads(raw.decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read(1200).decode("utf-8", errors="replace")
        raise HTTPException(502, f"本机 ComfyUI 图片上传失败（HTTP {exc.code}）：{detail}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise HTTPException(503, "无法连接本机 ComfyUI；图片未确认上传成功") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(502, "ComfyUI 上传响应格式无效") from exc
    if not isinstance(result, dict) or not isinstance(result.get("name"), str):
        raise HTTPException(502, "ComfyUI 未返回有效的参考图文件名")
    return {
        "name": result["name"],
        "subfolder": result.get("subfolder", subfolder),
        "type": result.get("type", "input"),
        "mime_type": validation["mime_type"],
        "size": validation["size"],
    }


@router.post("/projects/{project_id}/consistency/reference-upload")
def upload_project_reference_image(
    project_id: int,
    data: ReferenceImageUploadInput,
    db: Session = Depends(get_db),
):
    if not data.confirm_local_upload:
        raise HTTPException(400, "上传参考图到本机 ComfyUI 前，必须明确确认。")
    project = db.query(StoryProject).filter(StoryProject.id == project_id).first()
    if project is None:
        raise HTTPException(404, "漫剧项目不存在")
    profile = _get_visual_consistency(project)
    field_name = {
        "characters": "character_reference_images",
        "scenes": "scene_reference_images",
        "props": "prop_reference_images",
    }[data.category]
    bindings = profile.get(field_name, {})
    if not isinstance(bindings, dict) or str(data.asset_id) not in bindings or int(bindings[str(data.asset_id)]) != data.media_file_id:
        raise HTTPException(400, "这张图片尚未绑定到指定角色、场景或道具")
    linked = db.query(StoryAssetLink).filter(
        StoryAssetLink.project_id == project.id,
        StoryAssetLink.asset_id == data.asset_id,
    ).first()
    asset = db.query(VisualAsset).filter(VisualAsset.id == data.asset_id).first()
    if linked is None or asset is None or asset.category != data.category:
        raise HTTPException(404, "指定素材不属于当前漫剧项目或素材分类不匹配")
    media = db.query(MediaFile).filter(
        MediaFile.id == data.media_file_id,
        MediaFile.category == "image",
    ).first()
    if media is None:
        raise HTTPException(404, "绑定的本地图片素材不存在")
    uploaded = _upload_reference_to_local_comfyui(media, project.id, data.category, data.asset_id)
    settings = dict(project.settings) if isinstance(project.settings, dict) else {}
    uploaded_refs = dict(settings.get("comfyui_reference_uploads", {})) if isinstance(settings.get("comfyui_reference_uploads", {}), dict) else {}
    reference_key = f"{data.category}:{data.asset_id}:{media.id}"
    uploaded_refs[reference_key] = {
        **uploaded,
        "asset_id": data.asset_id,
        "media_file_id": media.id,
        "category": data.category,
    }
    settings["comfyui_reference_uploads"] = uploaded_refs
    project.settings = settings
    db.add(project)
    db.commit()
    return {
        "project_id": project.id,
        "asset_id": data.asset_id,
        "media_file_id": media.id,
        "category": data.category,
        "comfyui_image": uploaded,
        "local_only": True,
        "user_confirmed": True,
        "generation_started": False,
        "message": "参考图已由你确认上传到本机 ComfyUI 输入目录；尚未启动图片生成。",
    }


@router.post("/projects/{project_id}/consistency/reference-generate")
def generate_reference_conditioned_shot(
    project_id: int,
    data: ReferenceConditionedGenerateInput,
    db: Session = Depends(get_db),
):
    if not data.confirm_local_generation:
        raise HTTPException(400, "参考图条件生图会占用本机 CPU/GPU；请明确确认后再提交。")
    if data.width % 64 or data.height % 64:
        raise HTTPException(400, "图片宽度和高度必须是 64 的倍数。")
    project = db.query(StoryProject).filter(StoryProject.id == project_id).first()
    if project is None:
        raise HTTPException(404, "漫剧项目不存在")
    shot = db.query(StoryboardShot).filter(
        StoryboardShot.id == data.shot_id,
        StoryboardShot.project_id == project.id,
    ).first()
    if shot is None:
        raise HTTPException(404, "分镜不存在或不属于当前项目")
    prompt_text = (shot.image_prompt or "").strip()
    if len(prompt_text) < 3:
        raise HTTPException(400, "该分镜尚未填写图片提示词")

    profile = _get_visual_consistency(project)
    groups = (
        ("character_reference_images", "characters", shot.character_ids if isinstance(shot.character_ids, list) else []),
        ("scene_reference_images", "scenes", [shot.scene_asset_id] if shot.scene_asset_id else []),
        ("prop_reference_images", "props", shot.prop_ids if isinstance(shot.prop_ids, list) else []),
    )
    linked_ids = {
        row.asset_id for row in db.query(StoryAssetLink).filter(
            StoryAssetLink.project_id == project.id
        ).all()
    }
    uploaded_refs = project.settings.get("comfyui_reference_uploads", {}) if isinstance(project.settings, dict) else {}
    if not isinstance(uploaded_refs, dict):
        uploaded_refs = {}
    references = []
    missing = []
    for field_name, category, asset_ids in groups:
        bindings = profile.get(field_name, {})
        if not isinstance(bindings, dict):
            bindings = {}
        for raw_asset_id in dict.fromkeys(asset_ids):
            try:
                asset_id = int(raw_asset_id)
            except (TypeError, ValueError):
                raise HTTPException(400, "分镜包含无效的视觉素材编号")
            if asset_id not in linked_ids:
                raise HTTPException(400, f"素材 #{asset_id} 不属于当前项目")
            asset = db.query(VisualAsset).filter(VisualAsset.id == asset_id).first()
            if asset is None or asset.category != category:
                raise HTTPException(400, f"素材 #{asset_id} 的分类与参考图类型不匹配")
            media_id = bindings.get(str(asset_id))
            if not media_id:
                missing.append(f"{category} 素材 #{asset_id} 尚未绑定参考图")
                continue
            try:
                media_id = int(media_id)
            except (TypeError, ValueError):
                raise HTTPException(400, f"素材 #{asset_id} 的参考图编号无效")
            media = db.query(MediaFile).filter(
                MediaFile.id == media_id, MediaFile.category == "image"
            ).first()
            if media is None:
                missing.append(f"{category} 素材 #{asset_id} 绑定的图片已不存在")
                continue
            upload_key = f"{category}:{asset_id}:{media_id}"
            uploaded = uploaded_refs.get(upload_key)
            if not isinstance(uploaded, dict):
                missing.append(f"{category} 素材 #{asset_id} 尚未确认上传到本机 ComfyUI")
                continue
            if uploaded.get("subfolder") != f"ai_comic_references/p{project.id}" or uploaded.get("type") != "input":
                raise HTTPException(400, f"素材 #{asset_id} 的本机参考图上传记录不安全")
            references.append({
                "name": uploaded.get("name"),
                "subfolder": uploaded.get("subfolder", ""),
                "type": uploaded.get("type", "input"),
            })
    if missing:
        raise HTTPException(409, {"message": "参考图尚未准备完成", "missing": missing})
    if not references:
        raise HTTPException(400, "当前分镜没有绑定角色、场景或道具参考图")
    if len(references) > 10:
        raise HTTPException(400, "单个分镜最多使用 10 张参考图，请先减少绑定数量")

    capability = inspect_local_reference_capabilities()
    if not capability.get("reference_conditioning_ready"):
        raise HTTPException(409, {
            "message": "本机 ComfyUI 尚不具备完整的参考图条件生图能力",
            "missing_nodes": capability.get("missing_node_types", []),
            "missing_models": capability.get("missing_model_files", []),
        })
    checkpoint_info = _local_request("/object_info/CheckpointLoaderSimple")
    if data.checkpoint not in _checkpoint_names(checkpoint_info):
        raise HTTPException(404, "所选图片模型未安装在本机 ComfyUI 中；本程序不会自动下载模型。")
    object_infos = {}
    for node_type in REFERENCE_NODE_TYPES:
        try:
            response = _local_request("/object_info/" + node_type)
            node_info = response.get(node_type)
            if isinstance(node_info, dict):
                object_infos[node_type] = node_info
        except HTTPException as exc:
            if exc.status_code != 502:
                raise
    payload = LocalImageGenerateInput(
        checkpoint=data.checkpoint,
        prompt=prompt_text[:6000],
        negative_prompt=data.negative_prompt,
        style=data.style,
        width=data.width,
        height=data.height,
        steps=data.steps,
        cfg=data.cfg,
        seed=data.seed,
        confirm_local_generation=True,
    )
    seed = data.seed if data.seed is not None else uuid.uuid4().int % (2 ** 32)
    try:
        workflow = build_reference_conditioned_workflow(payload, seed, object_infos, references)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    task_record = MediaTask(
        task_type="local_image_reference",
        provider="comfyui-local",
        model=data.checkpoint,
        status="submitting",
        prompt=prompt_text[:6000],
        request_data={
            "project_id": project.id,
            "episode_id": shot.episode_id,
            "shot_id": shot.id,
            "shot_number": shot.shot_number,
            "reference_count": len(references),
            "seed": seed,
            "checkpoint": data.checkpoint,
            "style": data.style,
            "width": data.width,
            "height": data.height,
            "steps": data.steps,
            "cfg": data.cfg,
        },
        result_data={"prompt_id": None, "images": []},
    )
    db.add(task_record)
    try:
        db.commit()
        db.refresh(task_record)
    except Exception as exc:
        db.rollback()
        raise HTTPException(500, "本地任务记录保存失败；尚未向 ComfyUI 提交生成请求。") from exc

    try:
        result = _local_request("/prompt", {"prompt": workflow, "client_id": str(uuid.uuid4())}, timeout=15.0)
    except HTTPException as exc:
        task_record.status = "submission_unknown"
        task_record.error = "ComfyUI 提交响应不确定，请先刷新本机队列核实；禁止自动重试。"
        db.commit()
        raise HTTPException(502, task_record.error) from exc
    prompt_id = result.get("prompt_id")
    if not isinstance(prompt_id, str) or not PROMPT_ID_RE.fullmatch(prompt_id):
        task_record.status = "submission_unknown"
        task_record.error = "ComfyUI 未返回有效任务编号；请检查本机队列，禁止自动重试。"
        db.commit()
        raise HTTPException(502, task_record.error)
    task_record.remote_task_id = prompt_id
    task_record.status = "queued"
    task_record.result_data = {"prompt_id": prompt_id, "images": []}
    db.commit()
    return {
        "success": True,
        "engine": "ComfyUI",
        "prompt_id": prompt_id,
        "history_id": task_record.id,
        "seed": seed,
        "checkpoint": data.checkpoint,
        "shot_id": shot.id,
        "reference_count": len(references),
        "status": "queued",
        "local_only": True,
        "network_used": False,
        "downloaded": False,
        "paid_api_used": False,
        "cloud_fallback": False,
        "history_persisted": True,
        "message": "参考图条件生图任务已提交到本机 ComfyUI，并已写入统一任务中心。",
    }


@router.post("/projects/{project_id}/consistency/reference-preflight")
def preflight_reference_conditioning(
    project_id: int,
    data: ReferenceConditioningPreflightInput,
    db: Session = Depends(get_db),
):
    """Validate bound references and local ComfyUI support without uploading or generating."""
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

    profile = _get_visual_consistency(project)
    groups = (
        ("character_reference_images", "characters", "角色", lambda shot: shot.character_ids if isinstance(shot.character_ids, list) else []),
        ("scene_reference_images", "scenes", "场景", lambda shot: [shot.scene_asset_id] if shot.scene_asset_id else []),
        ("prop_reference_images", "props", "道具", lambda shot: shot.prop_ids if isinstance(shot.prop_ids, list) else []),
    )
    references = {}
    missing_bindings = []
    for field_name, category, label, get_ids in groups:
        bindings = profile.get(field_name, {})
        if not isinstance(bindings, dict):
            bindings = {}
        for shot in shots:
            for asset_id in get_ids(shot):
                media_id = bindings.get(str(asset_id))
                if not media_id:
                    missing_bindings.append({
                        "category": category, "asset_id": int(asset_id), "label": label,
                        "shot_ids": [shot.id], "ready": False, "reason": "尚未绑定参考图",
                    })
                    continue
                key = (category, str(asset_id), int(media_id))
                references.setdefault(key, {
                    "category": category, "asset_id": int(asset_id), "media_file_id": int(media_id),
                    "label": label, "shot_ids": [],
                })
                references[key]["shot_ids"].append(shot.id)

    asset_ids = {item["asset_id"] for item in references.values()}
    linked_asset_ids = {
        row.asset_id for row in db.query(StoryAssetLink).filter(
            StoryAssetLink.project_id == project.id,
            StoryAssetLink.asset_id.in_(asset_ids or {-1}),
        ).all()
    }
    visual_assets = {
        row.id: row for row in db.query(VisualAsset).filter(VisualAsset.id.in_(asset_ids or {-1})).all()
    }
    media_ids = {item["media_file_id"] for item in references.values()}
    media_rows = {
        row.id: row for row in db.query(MediaFile).filter(
            MediaFile.id.in_(media_ids or {-1}), MediaFile.category == "image"
        ).all()
    }

    checked = []
    failures = list(missing_bindings)
    for item in references.values():
        asset = visual_assets.get(item["asset_id"])
        media = media_rows.get(item["media_file_id"])
        reason = None
        if item["asset_id"] not in linked_asset_ids or asset is None or asset.category != item["category"]:
            reason = "该角色、场景或道具不属于当前项目或素材分类不匹配"
        elif media is None:
            reason = "绑定的参考图不存在或不是图片素材"
        else:
            file_check = _inspect_reference_media_file(media)
            if not file_check["ready"]:
                reason = file_check["reason"]
        result = {**item, "ready": reason is None}
        if reason is not None:
            result["reason"] = reason
            failures.append(result)
        else:
            result.update({"mime_type": file_check["mime_type"], "size": file_check["size"]})
        checked.append(result)

    capabilities = inspect_local_reference_capabilities()
    references_ready = not failures and bool(checked)
    ready = references_ready and capabilities.get("reference_conditioning_ready", False)
    return {
        "project_id": project.id,
        "checked_reference_count": len(checked),
        "references_ready": references_ready,
        "reference_conditioning_ready": ready,
        "references": checked,
        "failures": failures,
        "comfyui": capabilities,
        "upload_started": False,
        "generation_started": False,
        "local_only": True,
        "network_used": False,
        "message": "参考图文件和本机 ComfyUI 条件生图能力均已通过预检；本次没有上传图片或提交生成任务。" if ready else "预检发现参考图文件、项目归属或 ComfyUI 节点/模型尚未满足条件；本次没有上传图片或提交生成任务。",
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
    shot_costume_ids = {int(value) for value in data.shot_costume_variants}
    if shot_costume_ids:
        project_shots = db.query(StoryboardShot).filter(
            StoryboardShot.project_id == project.id,
            StoryboardShot.id.in_(shot_costume_ids),
        ).all()
        if {shot.id for shot in project_shots} != shot_costume_ids:
            raise HTTPException(404, "服装版本指定中包含不属于当前项目的分镜")
        for shot in project_shots:
            assignments = data.shot_costume_variants.get(str(shot.id), {})
            available_characters = {str(value) for value in shot.character_ids} if isinstance(shot.character_ids, list) else set()
            for character_id, variant_id in assignments.items():
                if character_id not in available_characters:
                    raise HTTPException(400, f"分镜 #{shot.id} 未关联角色 #{character_id}，不能指定该角色的服装版本")
                if variant_id not in data.costume_profiles.get(character_id, {}):
                    raise HTTPException(400, f"角色 #{character_id} 没有版本 {variant_id} 的服装版本档案")
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
        shot.image_prompt = (original + "\n\n" if original else "") + marker_start + "\n" + rules + "\n" + marker_end
        updated += 1
    db.commit()
    return {"success": True, "updated_shot_count": updated, "preflight": preflight, "local_only": True, "cloud_api_used": False}
