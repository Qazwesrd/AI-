import os
import base64
import mimetypes
import uuid
import json
import re
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional, Literal
from datetime import datetime
import threading

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db, SessionLocal
from app.services.cost_safety import require_cloud_ai_api_disabled
from app.routers.story import ensure_project_source_current
from app.models.media import MediaTask, MediaFile, SubtitleTrack
from app.models.story import EpisodePlan, StoryboardShot, StoryProject, StoryAssetLink, EpisodeEditTimeline
from app.models.visual import VisualAsset
from app.models.studio import AIProviderConfig, AIRuntimeSetting, AIUsageLog
from app.services.task_runtime import acquire_task_lease, release_task_lease
from app.services.media_service import (
    ROOT, STORAGE, VIDEO_DIR, AUDIO_DIR, SUBTITLE_DIR,
    decrypt_api_key, http_json, http_download,
    safe_storage_file, run_ffmpeg, run_ffmpeg_with_progress, FFmpegCancelledError,
    probe_media, validate_rendered_video, scan_audio_video_sync
)

router = APIRouter(prefix="/api/media", tags=["媒体生产"])


def build_episode_audio_filter(shot_ids, transitions):
    """Build audio concat/acrossfade graph matching the video transition groups."""
    transition_by_shot = {
        item["after_shot_id"]: item
        for item in transitions
        if item.get("transition") in ("fade", "dissolve")
    }
    groups = []
    boundaries = []
    current = []
    for index, shot_id in enumerate(shot_ids):
        current.append(index)
        transition = transition_by_shot.get(shot_id)
        if transition and index < len(shot_ids) - 1:
            groups.append(current)
            boundaries.append(transition)
            current = []
    if current:
        groups.append(current)

    filters = []
    for group_index, indexes in enumerate(groups):
        labels = "".join(f"[a{index}]" for index in indexes)
        label = f"ag{group_index}"
        if len(indexes) == 1:
            filters.append(f"{labels}anull[{label}]")
        else:
            filters.append(f"{labels}concat=n={len(indexes)}:v=0:a=1[{label}]")
    current_label = "ag0"
    for index, transition in enumerate(boundaries, 1):
        duration = float(transition["duration_seconds"])
        next_label = f"ax{index}"
        filters.append(
            f"[{current_label}][ag{index}]acrossfade=d={duration:.3f}:c1=tri:c2=tri[{next_label}]"
        )
        current_label = next_label
    return filters, current_label


def build_episode_video_filter(shot_ids, durations, transitions):
    """Build grouped concat + xfade filters while preserving hard cuts."""
    if not shot_ids or len(shot_ids) != len(durations):
        raise ValueError("镜头编号和时长必须一一对应")
    transition_by_shot = {
        item["after_shot_id"]: item
        for item in transitions
        if item.get("transition") in ("fade", "dissolve")
    }
    groups = []
    boundaries = []
    current = []
    for index, shot_id in enumerate(shot_ids):
        current.append(index)
        transition = transition_by_shot.get(shot_id)
        if transition and index < len(shot_ids) - 1:
            groups.append(current)
            boundaries.append(transition)
            current = []
    if current:
        groups.append(current)

    filters = []
    group_durations = []
    for group_index, indexes in enumerate(groups):
        labels = "".join(f"[v{index}]" for index in indexes)
        group_label = f"vg{group_index}"
        if len(indexes) == 1:
            filters.append(f"{labels}null[{group_label}]")
        else:
            filters.append(f"{labels}concat=n={len(indexes)}:v=1:a=0[{group_label}]")
        group_durations.append(sum(float(durations[index]) for index in indexes))

    current_label = "vg0"
    current_duration = group_durations[0]
    for index, transition in enumerate(boundaries, 1):
        duration = float(transition["duration_seconds"])
        if duration <= 0 or duration >= min(group_durations[index - 1], group_durations[index]) / 2:
            raise ValueError("转场时长超出相邻镜头允许范围")
        next_label = f"vx{index}"
        offset = current_duration - duration
        effect = "dissolve" if transition["transition"] == "dissolve" else "fade"
        filters.append(
            f"[{current_label}][vg{index}]xfade=transition={effect}:"
            f"duration={duration:.3f}:offset={offset:.3f}[{next_label}]"
        )
        current_label = next_label
        current_duration += group_durations[index] - duration
    return filters, current_label, current_duration



def get_provider(db, provider_name, confirm_paid=False, check_paid=True):
    require_cloud_ai_api_disabled()
    config = db.query(AIProviderConfig).filter(
        AIProviderConfig.provider == provider_name
    ).first()
    if not config:
        raise HTTPException(404, f"AI 服务商 {provider_name} 尚未配置")

    if not getattr(config, "enabled", False):
        raise HTTPException(400, "该 AI 服务商尚未启用")

    runtime = db.query(AIRuntimeSetting).first()
    if not runtime or not getattr(runtime, "network_enabled", False):
        raise HTTPException(403, "全局网络调用开关已关闭")

    # 按服务商配置的计费属性区分免费与付费服务。
    # 未知计费状态沿用模型默认值 True，默认阻止调用，避免意外扣费。
    paid = bool(getattr(config, "paid", True))
    if paid and check_paid:
        if not getattr(runtime, "paid_api_allowed", False):
            raise HTTPException(403, "付费 API 总授权未开启")
        if not confirm_paid:
            raise HTTPException(403, "此操作可能产生费用，必须明确确认后才能调用")

    encrypted = (
        getattr(config, "api_key_encrypted", None)
        or getattr(config, "encrypted_api_key", None)
    )
    if not encrypted:
        raise HTTPException(400, "该服务商尚未保存 API Key")

    base_url = getattr(config, "base_url", None)
    if not base_url:
        raise HTTPException(400, "该服务商尚未设置 API Base URL")

    try:
        api_key = decrypt_api_key(encrypted)
    except Exception as exc:
        raise HTTPException(500, str(exc))

    return config, api_key, base_url.rstrip("/")


def value_at(data, dotted_path):
    current = data
    for part in dotted_path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            current = current[int(part)]
        else:
            return None
        if current is None:
            return None
    return current


def speech_task_is_in_flight(task):
    return task.status in ("queued", "submitting", "submission_unknown", "interrupted_unknown")


def resolve_episode_shot_voice(shot, voice_by_character_id, default_voice, speaker_character_id=None):
    character_ids = getattr(shot, "character_ids", None) or []
    if speaker_character_id is not None:
        if str(speaker_character_id) not in {str(value) for value in character_ids}:
            raise ValueError("指定说话人不属于当前分镜")
        return voice_by_character_id.get(str(speaker_character_id), default_voice)
    if len(character_ids) == 1:
        return voice_by_character_id.get(str(character_ids[0]), default_voice)
    return default_voice


def ass_color_from_hex(value, alpha="00"):
    raw = str(value or "#FFFFFF").lstrip("#")
    if not re.fullmatch(r"[0-9A-Fa-f]{6}", raw):
        raise ValueError("字幕颜色必须为 #RRGGBB 格式")
    red, green, blue = raw[0:2], raw[2:4], raw[4:6]
    return f"&H{alpha}{blue.upper()}{green.upper()}{red.upper()}"


def build_subtitle_force_style(timeline):
    font_name = str(timeline.get("subtitle_font_name") or "Microsoft YaHei")
    if not re.fullmatch(r"[A-Za-z0-9 _-]{1,80}", font_name):
        raise ValueError("字幕字体名称包含不支持的字符")
    font_size = int(timeline.get("subtitle_font_size", 36))
    outline_width = float(timeline.get("subtitle_outline_width", 2))
    margin_v = int(timeline.get("subtitle_margin_v", 60))
    alignment = int(timeline.get("subtitle_alignment", 2))
    if not 16 <= font_size <= 72 or not 0 <= outline_width <= 8:
        raise ValueError("字幕字号或描边宽度超出允许范围")
    if not 0 <= margin_v <= 200 or alignment not in (2, 5, 8):
        raise ValueError("字幕位置参数无效")
    primary = ass_color_from_hex(timeline.get("subtitle_primary_color", "#FFFFFF"))
    outline = ass_color_from_hex(timeline.get("subtitle_outline_color", "#000000"))
    background = bool(timeline.get("subtitle_background", False))
    border_style = 3 if background else 1
    back_colour = "&H80000000" if background else "&H00000000"
    return (
        f"FontName={font_name},FontSize={font_size},PrimaryColour={primary},"
        f"OutlineColour={outline},Outline={outline_width:g},Shadow=0,"
        f"BorderStyle={border_style},BackColour={back_colour},"
        f"Alignment={alignment},MarginV={margin_v},Bold=0"
    )


def speech_fingerprint(provider, model, text, voice, speed, response_format="mp3", instructions=None):
    return json.dumps({
        "provider": provider, "model": model, "text": text, "voice": voice,
        "speed": float(speed), "response_format": response_format,
        "instructions": instructions or "",
    }, ensure_ascii=False, sort_keys=True)


class VideoTaskInput(BaseModel):
    provider: str
    model: str
    prompt: str = Field(min_length=1, max_length=20000)
    duration: int = Field(default=10, ge=1, le=600)
    aspect_ratio: str = "16:9"
    confirm_paid: bool = False

    # 每家视频服务商的 API 路径和请求结构可能不同，允许配置
    create_path: str = "/videos/generations"
    status_path_template: str = "/videos/{task_id}"
    task_id_field: str = "id"
    video_url_field: str = "video_url"
    status_field: str = "status"
    extra_payload: dict = Field(default_factory=dict)


class EpisodeVideoInput(BaseModel):
    provider: str
    model: str
    batch_size: int = Field(default=3, ge=1, le=6)
    confirm_paid: bool = False
    create_path: str = "/videos/generations"
    status_path_template: str = "/videos/{task_id}"
    task_id_field: str = "id"
    video_url_field: str = "video_url"
    status_field: str = "status"
    aspect_ratio: str = "16:9"
    # 仅对明确支持 JSON data URI 图片输入的服务商启用；默认关闭以兼容纯文生视频接口。
    reference_image_mode: str = Field(default="none", pattern="^(none|data_uri)$")
    reference_image_field: str = Field(default="image", min_length=1, max_length=120)
    reference_image_max_mb: int = Field(default=8, ge=1, le=12)
    extra_payload: dict = Field(default_factory=dict)


def set_nested_value(target, dotted_path, value):
    parts = dotted_path.split(".")
    if not parts or any(not part or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", part) for part in parts):
        raise ValueError("参考图字段路径格式无效")
    current = target
    for part in parts[:-1]:
        if part not in current:
            current[part] = {}
        if not isinstance(current[part], dict):
            raise ValueError("参考图字段路径与额外请求参数冲突")
        current = current[part]
    current[parts[-1]] = value


def make_reference_image_data_uri(asset, max_mb):
    if not asset or not asset.image_path:
        return None
    path = safe_storage_file(asset.image_path)
    size = path.stat().st_size
    if size <= 0 or size > max_mb * 1024 * 1024:
        raise ValueError(f"素材 {asset.name} 图片大小超过 {max_mb} MB 上限或文件为空")
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    if mime not in ("image/png", "image/jpeg", "image/webp"):
        raise ValueError(f"素材 {asset.name} 图片格式不支持：{mime}")
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return "data:" + mime + ";base64," + encoded


@router.post("/video/episodes/{episode_id}/generate")
def generate_episode_shot_videos(
    episode_id: int, data: EpisodeVideoInput, db: Session = Depends(get_db)
):
    """按分镜逐镜头提交视频任务；任务与镜头 ID 持久关联，失败可单镜头重试。"""
    episode = db.query(EpisodePlan).filter(EpisodePlan.id == episode_id).first()
    if not episode:
        raise HTTPException(404, "分集不存在")
    project = db.query(StoryProject).filter(StoryProject.id == episode.project_id).first()
    if not project:
        raise HTTPException(404, "剧本项目不存在")
    ensure_project_source_current(project)
    shots = db.query(StoryboardShot).filter(
        StoryboardShot.episode_id == episode_id
    ).order_by(StoryboardShot.sort_order, StoryboardShot.shot_number).all()
    if not shots:
        raise HTTPException(400, "本集尚无分镜，请先生成剧本与分镜")

    config, key, base = get_provider(db, data.provider, data.confirm_paid)
    if config.purpose not in ("video", "media", "text"):
        raise HTTPException(400, "所选服务商用途不适用于视频生成")

    links = db.query(StoryAssetLink).filter(
        StoryAssetLink.project_id == project.id,
        StoryAssetLink.episode_id == episode_id,
    ).all()
    linked_ids = {link.asset_id for link in links}
    assets = db.query(VisualAsset).filter(VisualAsset.confirmed == True).all()
    asset_by_id = {}
    for asset in assets:
        asset_data = asset.data if isinstance(asset.data, dict) else {}
        if asset_data.get("source_novel_id") == project.novel_id or asset.id in linked_ids:
            asset_by_id[asset.id] = asset

    existing = db.query(MediaTask).filter(
        MediaTask.task_type == "storyboard_video"
    ).all()
    submitted = {
        (task.request_data or {}).get("shot_id")
        for task in existing
        if (task.request_data or {}).get("episode_id") == episode_id
        and task.status not in ("failed", "cancelled")
    }
    pending_shots = [shot for shot in shots if shot.id not in submitted]
    batch_shots = pending_shots[:data.batch_size]
    created, skipped, failures = [], [], []
    for shot in batch_shots:
        prompt_parts = [
            "制作一段用于漫剧的连续镜头视频。整体风格：" + str(project.adaptation_style),
            "分集：" + str(episode.title),
            "镜头：" + str(shot.shot_number),
            "场景：" + str(shot.scene_name),
            "景别：" + str(shot.shot_type),
            "运镜：" + str(shot.camera_movement),
            "画面：" + str(shot.visual_description),
            "角色动作：" + str(shot.character_actions),
            "对白/旁白：" + str(shot.dialogue),
            "音效：" + str(shot.sound_effects),
            "视觉提示词：" + str(shot.image_prompt),
        ]
        shot_links = [link for link in links if link.shot_id == shot.id]
        shot_asset_ids = {link.asset_id for link in shot_links}
        if shot.scene_asset_id:
            shot_asset_ids.add(shot.scene_asset_id)
        shot_assets = []
        reference_candidates = []
        for asset_id in shot_asset_ids:
            asset = asset_by_id.get(asset_id)
            if not asset:
                continue
            description = "；".join(x for x in [
                asset.name, asset.description, asset.appearance,
                asset.clothing, asset.consistency_prompt
            ] if x)
            if description:
                shot_assets.append(description[:1200])
            if asset.image_path:
                reference_candidates.append(asset)
        if shot_assets:
            prompt_parts.append("本镜头已关联视觉素材设定，必须保持外观一致：" + "；".join(shot_assets))
        prompt = "\n".join(x for x in prompt_parts if x and not x.endswith("："))[:20000]
        payload = {
            "model": data.model,
            "prompt": prompt,
            "duration": max(1, min(60, int(round(shot.duration_seconds or 5)))),
            "aspect_ratio": data.aspect_ratio,
            **data.extra_payload,
        }
        reference_asset = None
        reference_image_attached = False
        try:
            if data.reference_image_mode == "data_uri":
                if not reference_candidates:
                    raise ValueError("已启用参考图输入，但本镜头关联的已确认素材没有可用图片")
                # 优先使用角色参考图，其次场景，再使用道具，减少把道具图误当人物参考。
                priority = {"characters": 0, "scenes": 1, "weapons": 2, "props": 3, "special_items": 4, "costumes": 5}
                reference_candidates.sort(key=lambda item: (priority.get(item.category, 9), item.id))
                reference_asset = reference_candidates[0]
                data_uri = make_reference_image_data_uri(reference_asset, data.reference_image_max_mb)
                if not data_uri:
                    raise ValueError("所选视觉素材没有可用参考图片")
                set_nested_value(payload, data.reference_image_field, data_uri)
                reference_image_attached = True
            # 先持久化“正在提交”记录，再请求第三方，防止网络超时/进程退出后
            # 因本地没有任务记录而再次点击，造成重复付费提交。
            task = MediaTask(
                task_type="storyboard_video", provider=data.provider,
                model=data.model, status="pending", prompt=prompt,
                request_data={
                    "base_url": base,
                    "status_path_template": data.status_path_template,
                    "video_url_field": data.video_url_field,
                    "status_field": data.status_field,
                    "confirm_paid": data.confirm_paid,
                    "episode_id": episode_id,
                    "project_id": project.id,
                    "shot_id": shot.id,
                    "shot_number": shot.shot_number,
                    "duration": payload["duration"],
                    "reference_image_mode": data.reference_image_mode,
                    "reference_image_attached": reference_image_attached,
                    "reference_asset_id": reference_asset.id if reference_asset else None,
                    "reference_asset_name": reference_asset.name if reference_asset else None,
                    "reference_image_field": data.reference_image_field if reference_image_attached else None,
                },
            )
            db.add(task)
            db.commit()
            db.refresh(task)

            try:
                acquire_task_lease(db, "media", task.id, priority=5)
                task.status = "submitting"
                db.commit()
                result = http_json(
                    base + "/" + data.create_path.lstrip("/"),
                    key, body=payload, timeout=300
                )
                remote_id = value_at(result, data.task_id_field)
                if not remote_id:
                    raise RuntimeError("服务商已返回响应，但未找到任务编号；提交结果不确定，请先到服务商控制台核对")
                task.remote_task_id = str(remote_id)
                task.result_data = result
                task.status = "submitted"
                task.error = None
                db.commit()
                release_task_lease(db, "media", task.id, True)
                shot.status = "video_submitted"
                created.append({"task_id": task.id, "shot_id": shot.id,
                                "shot_number": shot.shot_number, "remote_task_id": str(remote_id)})
            except Exception as exc:
                if task.status == "pending" and isinstance(exc, HTTPException) and exc.status_code == 429:
                    task.status = "queued"
                    task.error = str(exc.detail)[:2000]
                    db.commit()
                    failures.append({"task_id": task.id, "shot_id": shot.id,
                                     "shot_number": shot.shot_number,
                                     "status": "queued", "error": task.error})
                elif task.status == "pending":
                    task.status = "failed"
                    task.error = ("任务尚未提交，未调用视频服务商，可在本集重新提交：" + str(exc))[:2000]
                    db.commit()
                    failures.append({"task_id": task.id, "shot_id": shot.id,
                                     "shot_number": shot.shot_number,
                                     "status": "failed", "error": task.error})
                else:
                    # 超时或连接中断时无法判断服务商是否已接单，保留未知状态并阻止自动重复提交。
                    task.status = "submission_unknown"
                    task.error = (
                        "提交结果不确定：请先在视频服务商控制台核对是否已创建任务；"
                        "确认不存在后再人工重新提交。原始错误：" + str(exc)
                    )[:2000]
                    db.commit()
                    release_task_lease(db, "media", task.id, False, task.error)
                    shot.status = "video_submission_unknown"
                    failures.append({"task_id": task.id, "shot_id": shot.id,
                                     "shot_number": shot.shot_number,
                                     "status": task.status, "error": task.error})
        except Exception as exc:
            db.rollback()
            failures.append({"shot_id": shot.id, "shot_number": shot.shot_number,
                             "error": str(exc)[:1200]})
    episode.status = "video_generating" if created or skipped else "video_failed"
    db.commit()
    remaining = max(0, len(pending_shots) - len(batch_shots))
    done = remaining == 0 and not failures
    return {
        "success": not failures,
        "done": done,
        "remaining": remaining,
        "episode_id": episode_id,
        "created": created,
        "skipped_existing": skipped,
        "failures": failures,
        "message": f"本批提交 {len(created)} 个镜头视频任务，失败 {len(failures)} 个，尚有 {remaining} 个未处理镜头。"
    }


@router.get("/video/episodes/{episode_id}/tasks")
def list_episode_video_tasks(episode_id: int, db: Session = Depends(get_db)):
    episode = db.query(EpisodePlan).filter(EpisodePlan.id == episode_id).first()
    if not episode:
        raise HTTPException(404, "分集不存在")
    tasks = db.query(MediaTask).filter(
        MediaTask.task_type == "storyboard_video"
    ).order_by(MediaTask.id.asc()).all()
    rows = []
    for task in tasks:
        meta = task.request_data if isinstance(task.request_data, dict) else {}
        if meta.get("episode_id") == episode_id:
            rows.append({
                "id": task.id, "shot_id": meta.get("shot_id"),
                "shot_number": meta.get("shot_number"),
                "provider": task.provider, "model": task.model,
                "status": task.status, "remote_task_id": task.remote_task_id,
                "result_path": task.result_path, "error": task.error,
                "created_at": task.created_at.isoformat() if task.created_at else None,
                "updated_at": task.updated_at.isoformat() if task.updated_at else None,
            })
    return rows


@router.post("/video/episodes/{episode_id}/refresh")
def refresh_episode_video_tasks(episode_id: int, db: Session = Depends(get_db)):
    episode = db.query(EpisodePlan).filter(EpisodePlan.id == episode_id).first()
    if not episode:
        raise HTTPException(404, "分集不存在")
    tasks = db.query(MediaTask).filter(
        MediaTask.task_type == "storyboard_video"
    ).all()
    matched = []
    for task in tasks:
        meta = task.request_data if isinstance(task.request_data, dict) else {}
        if meta.get("episode_id") == episode_id and task.status not in ("completed", "failed", "cancelled", "submission_unknown"):
            matched.append(task)
    outcomes = []
    for task in matched:
        try:
            # 任务提交时已完成付费授权；轮询结果本身不再创建新任务，不应因用户后来关闭付费开关而丢失查询能力。
            config, key, base = get_provider(
                db, task.provider, bool((task.request_data or {}).get("confirm_paid")),
                check_paid=False,
            )
            meta = task.request_data or {}
            path = meta.get("status_path_template", "/videos/{task_id}").replace(
                "{task_id}", str(task.remote_task_id)
            )
            result = http_json(base + "/" + path.lstrip("/"), key, method="GET")
            remote_status = str(
                value_at(result, meta.get("status_field", "status")) or "processing"
            ).strip().lower()
            task.result_data = result
            video_url = value_at(result, meta.get("video_url_field", "video_url"))

            # 不同视频平台使用 success/succeeded/completed 等不同状态；
            # 只有真正下载到本地文件后才标记 completed，避免“远端完成但本地无文件”导致无法重试。
            failed_states = {"failed", "error", "cancelled", "canceled", "rejected"}
            completed_states = {"completed", "complete", "success", "succeeded", "finished", "done"}
            if remote_status in failed_states:
                task.status = "failed"
                task.error = str(
                    value_at(result, "error.message")
                    or value_at(result, "message")
                    or "视频服务商报告任务失败"
                )[:1500]
            elif video_url:
                filename = f"episode_{episode_id}_shot_{meta.get('shot_id')}_{uuid.uuid4().hex[:8]}.mp4"
                destination = VIDEO_DIR / filename
                http_download(str(video_url), destination)
                if not destination.is_file() or destination.stat().st_size < 1024:
                    destination.unlink(missing_ok=True)
                    raise RuntimeError("视频服务返回的文件为空或无效")
                task.result_path = str(destination)
                task.status = "completed"
                task.error = ""
                db.add(MediaFile(
                    name=filename, category="video", file_path=str(destination),
                    mime_type="video/mp4", file_size=destination.stat().st_size,
                ))
                shot = db.query(StoryboardShot).filter(
                    StoryboardShot.id == meta.get("shot_id")
                ).first()
                if shot:
                    shot.status = "video_ready"
            elif remote_status in completed_states:
                # 服务商状态已完成但暂时未给出视频地址，继续允许用户再次轮询。
                task.status = "awaiting_video_url"
            else:
                task.status = remote_status if remote_status in {
                    "queued", "pending", "submitted", "processing", "running"
                } else "processing"
            db.commit()
            outcomes.append({"task_id": task.id, "shot_number": meta.get("shot_number"),
                             "status": task.status, "error": task.error or ""})
        except Exception as exc:
            task.error = str(exc)[:1500]
            db.commit()
            outcomes.append({"task_id": task.id, "shot_number": meta.get("shot_number"),
                             "status": task.status, "error": task.error})
    return {"episode_id": episode_id, "checked": len(outcomes), "tasks": outcomes}


@router.get("/video/episodes/{episode_id}/render-readiness")
def get_episode_render_readiness(episode_id: int, db: Session = Depends(get_db)):
    """Inspect every shot before rendering; this endpoint never calls an AI provider."""
    episode = db.query(EpisodePlan).filter(EpisodePlan.id == episode_id).first()
    if not episode:
        raise HTTPException(404, "分集不存在")
    project = db.query(StoryProject).filter(StoryProject.id == episode.project_id).first()
    if not project:
        raise HTTPException(404, "剧本项目不存在")
    ensure_project_source_current(project)
    shots = db.query(StoryboardShot).filter(
        StoryboardShot.episode_id == episode_id
    ).order_by(StoryboardShot.sort_order, StoryboardShot.shot_number).all()
    if not shots:
        return {
            "episode_id": episode_id, "ready": False, "total_shots": 0,
            "ready_count": 0, "missing_count": 0, "processing_count": 0,
            "broken_count": 0, "items": [],
            "message": "本集尚无分镜，无法合成。",
        }

    shot_ids = {shot.id for shot in shots}
    tasks = db.query(MediaTask).filter(
        MediaTask.task_type == "storyboard_video"
    ).order_by(MediaTask.id.desc()).all()
    tasks_by_shot = {}
    for task in tasks:
        metadata = task.request_data if isinstance(task.request_data, dict) else {}
        shot_id = metadata.get("shot_id")
        if metadata.get("episode_id") == episode_id and shot_id in shot_ids:
            tasks_by_shot.setdefault(shot_id, []).append(task)

    items = []
    ready_count = missing_count = processing_count = broken_count = 0
    for shot in shots:
        history = tasks_by_shot.get(shot.id, [])
        latest = history[0] if history else None
        selected = None
        selected_info = None
        rejected = []
        for task in history:
            if task.status != "completed" or not task.result_path:
                continue
            try:
                clip_path = safe_storage_file(task.result_path)
                info = probe_media(clip_path)
                streams = info.get("streams") or []
                video_stream = next(
                    (stream for stream in streams if stream.get("codec_type") == "video"),
                    None,
                )
                duration = float((info.get("format") or {}).get("duration") or 0)
                if not video_stream or duration <= 0 or duration > 600:
                    raise ValueError("视频流缺失、时长无效或超过 10 分钟")
                selected = task
                registered_media = db.query(MediaFile).filter(
                    MediaFile.file_path == str(clip_path)
                ).order_by(MediaFile.id.desc()).first()
                selected_info = {
                    "duration_seconds": round(duration, 3),
                    "width": video_stream.get("width"),
                    "height": video_stream.get("height"),
                    "path": str(clip_path),
                    "preview_url": f"/api/media/files/{registered_media.id}/download" if registered_media else None,
                }
                break
            except Exception as exc:
                rejected.append({"task_id": task.id, "reason": str(exc)[:300]})

        if selected:
            status = "ready"
            ready_count += 1
        elif latest and latest.status in {
            "pending", "queued", "submitting", "submitted", "processing",
            "running", "submission_unknown", "awaiting_video_url",
        }:
            status = "processing"
            processing_count += 1
        elif history and (rejected or (latest and latest.status == "completed" and not latest.result_path)):
            status = "broken"
            broken_count += 1
        else:
            status = "missing"
            missing_count += 1

        items.append({
            "shot_id": shot.id,
            "shot_number": shot.shot_number,
            "scene_name": shot.scene_name or "",
            "visual_description": shot.visual_description or "",
            "planned_duration_seconds": float(shot.duration_seconds or 0),
            "status": status,
            "latest_task_id": latest.id if latest else None,
            "latest_task_status": latest.status if latest else None,
            "selected_task_id": selected.id if selected else None,
            "video": selected_info,
            "warnings": rejected,
            "error": latest.error if latest and latest.status in {"failed", "cancelled"} else None,
        })

    ready = ready_count == len(shots)
    return {
        "episode_id": episode_id,
        "episode_number": episode.episode_number,
        "episode_title": episode.title,
        "ready": ready,
        "total_shots": len(shots),
        "ready_count": ready_count,
        "missing_count": missing_count,
        "processing_count": processing_count,
        "broken_count": broken_count,
        "items": items,
        "message": (
            "全部镜头均有可用视频，可以开始合成。"
            if ready else
            "请先处理缺失、生成中或损坏的镜头视频，再进行成片合成。"
        ),
    }


@router.get("/video/projects/{project_id}/quality-summary")
def scan_project_episode_quality(project_id: int, db: Session = Depends(get_db)):
    """Run a local, read-only render-readiness scan for every episode in a project."""
    project = db.query(StoryProject).filter(StoryProject.id == project_id).first()
    if not project:
        raise HTTPException(404, "剧本项目不存在")
    ensure_project_source_current(project)
    episodes = db.query(EpisodePlan).filter(
        EpisodePlan.project_id == project_id
    ).order_by(EpisodePlan.sort_order, EpisodePlan.episode_number, EpisodePlan.id).limit(200).all()
    totals = {
        "episodes": len(episodes), "ready_episodes": 0, "incomplete_episodes": 0,
        "total_shots": 0, "ready_shots": 0, "missing_shots": 0,
        "processing_shots": 0, "broken_shots": 0, "scan_errors": 0,
    }
    results = []
    for episode in episodes:
        try:
            readiness = get_episode_render_readiness(episode.id, db)
            row = {
                "episode_id": episode.id, "episode_number": episode.episode_number,
                "title": episode.title or "", "ready": bool(readiness.get("ready")),
                "total_shots": int(readiness.get("total_shots") or 0),
                "ready_count": int(readiness.get("ready_count") or 0),
                "missing_count": int(readiness.get("missing_count") or 0),
                "processing_count": int(readiness.get("processing_count") or 0),
                "broken_count": int(readiness.get("broken_count") or 0),
                "message": readiness.get("message") or "",
            }
        except Exception as exc:
            row = {
                "episode_id": episode.id, "episode_number": episode.episode_number,
                "title": episode.title or "", "ready": False, "total_shots": 0,
                "ready_count": 0, "missing_count": 0, "processing_count": 0,
                "broken_count": 0, "scan_error": str(exc)[:300],
            }
            totals["scan_errors"] += 1
        results.append(row)
        totals["ready_episodes"] += int(row["ready"])
        totals["incomplete_episodes"] += int(not row["ready"])
        totals["total_shots"] += row["total_shots"]
        totals["ready_shots"] += row["ready_count"]
        totals["missing_shots"] += row["missing_count"]
        totals["processing_shots"] += row["processing_count"]
        totals["broken_shots"] += row["broken_count"]
    return {
        "project_id": project_id, "project_title": project.title or "",
        "summary": totals, "episodes": results,
        "message": "项目全部分集扫描完成。" if episodes and not totals["scan_errors"] else "扫描完成；部分分集需要复核。" if episodes else "该项目尚无分集。",
        "note": "仅检查本地镜头视频文件及可解码视频流，不调用 AI API、不提交生成任务、不修改媒体文件。最多扫描 200 集。",
    }


@router.post("/video/episodes/{episode_id}/render")
def render_episode_from_shots(
    episode_id: int, output_name: str = "episode.mp4",
    music_path: Optional[str] = None,
    music_volume: float = 0.18,
    db: Session = Depends(get_db)
):
    """将本集已下载的镜头统一规格后合成为初版成片，并生成对白字幕文件。"""
    episode = db.query(EpisodePlan).filter(EpisodePlan.id == episode_id).first()
    if not episode:
        raise HTTPException(404, "分集不存在")
    project = db.query(StoryProject).filter(StoryProject.id == episode.project_id).first()
    if not project:
        raise HTTPException(404, "剧本项目不存在")
    ensure_project_source_current(project)
    shots = db.query(StoryboardShot).filter(
        StoryboardShot.episode_id == episode_id
    ).order_by(StoryboardShot.sort_order, StoryboardShot.shot_number).all()
    if not shots:
        raise HTTPException(400, "本集没有分镜")
    timeline_record = db.query(EpisodeEditTimeline).filter(
        EpisodeEditTimeline.episode_id == episode_id
    ).first()
    timeline = timeline_record.edit_data if timeline_record and isinstance(timeline_record.edit_data, dict) else {}
    shot_by_id = {shot.id: shot for shot in shots}
    requested_order = timeline.get("shot_order") or [shot.id for shot in shots]
    if len(requested_order) != len(shot_by_id) or set(requested_order) != set(shot_by_id):
        raise HTTPException(409, "已保存的剪辑时间轴与当前分镜不一致，请重新载入并保存时间轴")
    shots = [shot_by_id[shot_id] for shot_id in requested_order]
    tasks = db.query(MediaTask).filter(
        MediaTask.task_type == "storyboard_video"
    ).order_by(MediaTask.id.desc()).all()
    by_shot = {}
    for task in tasks:
        metadata = task.request_data if isinstance(task.request_data, dict) else {}
        shot_id = metadata.get("shot_id")
        if (
            metadata.get("episode_id") != episode_id
            or not shot_id
            or shot_id in by_shot
            or task.status != "completed"
            or not task.result_path
        ):
            continue
        try:
            candidate = safe_storage_file(task.result_path)
            info = probe_media(candidate)
            streams = info.get("streams") or []
            has_video = any(stream.get("codec_type") == "video" for stream in streams)
            duration = float((info.get("format") or {}).get("duration") or 0)
            if not has_video or duration <= 0 or duration > 600:
                continue
            by_shot[shot_id] = candidate
        except Exception:
            # A newer corrupt/missing clip must not hide an older valid version.
            continue
    clips = []
    missing = []
    for shot in shots:
        clip = by_shot.get(shot.id)
        if not clip:
            missing.append(shot.shot_number)
        else:
            clips.append(clip)
    if missing:
        raise HTTPException(409, "以下镜头视频尚未全部完成或下载：" + "、".join(map(str, missing)))
    output_name = re.sub(r"[^a-zA-Z0-9_-]", "_", output_name)
    if not output_name.lower().endswith(".mp4"):
        output_name += ".mp4"
    final_output = VIDEO_DIR / f"episode_{episode_id}_{uuid.uuid4().hex[:8]}_{output_name}"
    output = final_output.with_name(final_output.stem + ".partial" + final_output.suffix)
    task_id = getattr(_EPISODE_RENDER_CONTEXT, "task_id", None)
    if task_id is not None:
        _update_episode_render_job(task_id, result_path=str(output), progress={
            "percent": 8, "stage": "preparing", "output_path": str(output),
        })
    try:
        # 统一分辨率、帧率、像素格式和音频采样率，兼容不同视频模型输出规格。
        ffmpeg_args = []
        filters = []
        actual_durations = []
        input_index = 0
        for index, clip in enumerate(clips):
            info = probe_media(clip)
            streams = info.get("streams", [])
            duration = float((info.get("format") or {}).get("duration") or shots[index].duration_seconds or 5)
            duration = max(1.0, duration)
            actual_durations.append(duration)
            ffmpeg_args += ["-i", str(clip)]
            filters.append(
                f"[{input_index}:v:0]scale=1920:1080:force_original_aspect_ratio=decrease,"
                f"pad=1920:1080:(ow-iw)/2:(oh-ih)/2,fps=30,setsar=1,format=yuv420p,"
                f"trim=duration={duration},setpts=PTS-STARTPTS[v{index}]"
            )
            has_audio = any(stream.get("codec_type") == "audio" for stream in streams)
            if has_audio:
                filters.append(
                    f"[{input_index}:a:0]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                    f"atrim=duration={duration},asetpts=PTS-STARTPTS[a{index}]"
                )
            else:
                input_index += 1
                ffmpeg_args += [
                    "-f", "lavfi", "-t", str(duration), "-i",
                    "anullsrc=channel_layout=stereo:sample_rate=48000",
                ]
                filters.append(
                    f"[{input_index}:a:0]atrim=duration={duration},asetpts=PTS-STARTPTS[a{index}]"
                )
            input_index += 1
        transition_items = timeline.get("transitions") or []
        try:
            video_filters, video_label, total_video_duration = build_episode_video_filter(
                [shot.id for shot in shots], actual_durations, transition_items
            )
        except (TypeError, ValueError, KeyError) as exc:
            raise HTTPException(422, "剪辑转场配置无效：" + str(exc)) from exc
        if total_video_duration < 90 or total_video_duration > 150:
            raise HTTPException(
                400,
                f"本集镜头视频总时长为 {total_video_duration:.1f} 秒，必须控制在 90–150 秒；请调整镜头时长或视频生成设置后再合成。",
            )
        _EPISODE_RENDER_CONTEXT.duration_seconds = total_video_duration
        filters.extend(video_filters)
        audio_filters, audio_label = build_episode_audio_filter(
            [shot.id for shot in shots], transition_items
        )
        filters.extend(audio_filters)
        filters.append(
            f"[{audio_label}]atrim=duration={total_video_duration:.3f},"
            "asetpts=PTS-STARTPTS[aout]"
        )
        filters.append(f"[{video_label}]null[vout]")
        title_files = []
        title_texts = []
        intro_title = str(timeline.get("intro_title") or "").strip()
        outro_title = str(timeline.get("outro_title") or "").strip()
        if intro_title or outro_title:
            font_candidates = [
                Path("C:/Windows/Fonts/msyh.ttc"),
                Path("C:/Windows/Fonts/arial.ttf"),
                Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
                Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
                Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            ]
            font_path = next((item for item in font_candidates if item.is_file()), None)
            if not font_path:
                raise HTTPException(422, "已设置片头/片尾标题，但未找到可用字体；请安装微软雅黑或 DejaVu Sans 字体")
            def escape_filter_path(value):
                return str(value).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
            for title, start, end, suffix in [
                (intro_title, 0, min(4.0, total_video_duration), "intro"),
                (outro_title, max(0.0, total_video_duration - 4.0), total_video_duration, "outro"),
            ]:
                if not title:
                    continue
                text_file = SUBTITLE_DIR / f"episode_{episode_id}_{uuid.uuid4().hex}_{suffix}.txt"
                text_file.write_text(title, encoding="utf-8")
                title_files.append(text_file)
                title_texts.append(
                    f"drawtext=fontfile='{escape_filter_path(font_path)}':"
                    f"textfile='{escape_filter_path(text_file)}':reload=0:"
                    f"fontcolor=white:fontsize=64:borderw=3:bordercolor=black:"
                    f"x=(w-text_w)/2:y=h*0.12:enable='between(t,{start:.3f},{end:.3f})'"
                )
            filters.append(f"[vout]{','.join(title_texts)}[vtitled]")
            final_video_label = "vtitled"
        else:
            final_video_label = "vout"
        _run_episode_render_ffmpeg(ffmpeg_args + [
            "-filter_complex", ";".join(filters),
            "-map", f"[{final_video_label}]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-c:a", "aac", "-ar", "48000", "-movflags", "+faststart",
            str(output),
        ])
        # Mix generated dialogue and optional background music with original shot sound.
        speech_tasks = db.query(MediaTask).filter(
            MediaTask.task_type == "episode_speech_track"
        ).order_by(MediaTask.id.desc()).all()
        speech_path = None
        selected_voice_path = timeline.get("voice_path")
        if selected_voice_path:
            speech_path = safe_storage_file(selected_voice_path)
            voice_record = db.query(MediaFile).filter(
                MediaFile.category == "audio",
                MediaFile.file_path == str(speech_path),
            ).first()
            if not voice_record:
                raise HTTPException(422, "对白配音必须来自本地音频素材库")
            if not _media_file_belongs_to_project(voice_record, episode.project_id, episode.id):
                raise HTTPException(422, "对白配音属于其他项目，不能跨项目引用")
        else:
            for speech_task in speech_tasks:
                speech_meta = speech_task.request_data if isinstance(speech_task.request_data, dict) else {}
                if (
                    speech_meta.get("episode_id") == episode_id
                    and speech_meta.get("project_id") in (None, episode.project_id)
                    and speech_task.result_path
                    and Path(speech_task.result_path).is_file()
                ):
                    speech_path = safe_storage_file(speech_task.result_path)
                    speech_record = db.query(MediaFile).filter(
                        MediaFile.category == "audio",
                        MediaFile.file_path == str(speech_path),
                    ).first()
                    if speech_record and not _media_file_belongs_to_project(
                        speech_record, episode.project_id, episode.id
                    ):
                        continue
                    break
        voice_volume = max(0.0, min(2.0, float(timeline.get("voice_volume", 1.0))))
        original_volume = max(0.0, min(2.0, float(timeline.get("original_volume", 0.35))))
        selected_music_path = timeline.get("music_path") or music_path
        music = safe_storage_file(selected_music_path) if selected_music_path else None
        if music:
            music_record = db.query(MediaFile).filter(
                MediaFile.category == "audio",
                MediaFile.file_path == str(music),
            ).first()
            if not music_record:
                raise HTTPException(422, "背景音乐必须先上传并登记到本地音频素材库")
            if not _media_file_belongs_to_project(music_record, episode.project_id, episode.id):
                raise HTTPException(422, "背景音乐属于其他项目，不能跨项目引用")
            music_info = probe_media(music)
            if not any(stream.get("codec_type") == "audio" for stream in music_info.get("streams", [])):
                raise HTTPException(422, "所选背景音乐文件不包含可用音轨")
        music_volume = float(timeline.get("music_volume", music_volume))
        music_fade_in = max(0.0, min(30.0, float(timeline.get("music_fade_in_seconds", 1.0))))
        music_fade_out = max(0.0, min(30.0, float(timeline.get("music_fade_out_seconds", 2.0))))
        sound_effect_tracks = timeline.get("sound_effect_tracks") or []
        validated_effects = []
        for effect in sound_effect_tracks:
            effect_path = safe_storage_file(effect.get("path", ""))
            effect_record = db.query(MediaFile).filter(
                MediaFile.category == "audio",
                MediaFile.file_path == str(effect_path),
            ).first()
            if not effect_record:
                raise HTTPException(422, "音效必须来自本地音频素材库")
            if not _media_file_belongs_to_project(effect_record, episode.project_id, episode.id):
                raise HTTPException(422, "音效属于其他项目，不能跨项目引用")
            effect_info = probe_media(effect_path)
            if not any(stream.get("codec_type") == "audio" for stream in effect_info.get("streams", [])):
                raise HTTPException(422, "音效文件不包含可用音轨")
            start_seconds = float(effect.get("start_seconds", 0))
            effect_volume = float(effect.get("volume", 1.0))
            if start_seconds < 0 or start_seconds >= total_video_duration or not 0 <= effect_volume <= 2:
                raise HTTPException(422, "音效起始时间或音量超出允许范围")
            validated_effects.append((effect_path, start_seconds, effect_volume))
        if speech_path or music or validated_effects:
            mixed_output = output.with_name(output.stem + "_mixed.mp4")
            mix_args = ["-i", str(output)]
            mix_filters = [f"[0:a]volume={original_volume:.3f}[original]"]
            mix_labels = ["[original]"]
            mix_index = 1
            if speech_path:
                mix_args += ["-i", str(speech_path)]
                mix_filters.append(f"[{mix_index}:a]volume={voice_volume:.3f}[voice]")
                mix_labels.append("[voice]")
                mix_index += 1
            if music:
                mix_args += ["-stream_loop", "-1", "-i", str(music)]
                fade_in_filter = f",afade=t=in:st=0:d={music_fade_in:.3f}" if music_fade_in > 0 else ""
                fade_out_duration = min(music_fade_out, total_video_duration)
                fade_out_start = max(0.0, total_video_duration - fade_out_duration)
                fade_out_filter = f",afade=t=out:st={fade_out_start:.3f}:d={fade_out_duration:.3f}" if fade_out_duration > 0 else ""
                mix_filters.append(
                    f"[{mix_index}:a]volume={max(0.0, min(2.0, music_volume))}"
                    f"{fade_in_filter}{fade_out_filter}[music]"
                )
                mix_labels.append("[music]")
                mix_index += 1
            for effect_index, (effect_path, start_seconds, effect_volume) in enumerate(validated_effects):
                mix_args += ["-i", str(effect_path)]
                delay_ms = int(round(start_seconds * 1000))
                label = f"sfx{effect_index}"
                mix_filters.append(
                    f"[{mix_index}:a]volume={effect_volume:.3f},adelay={delay_ms}|{delay_ms}[{label}]"
                )
                mix_labels.append(f"[{label}]")
                mix_index += 1
            mix_filters.append(
                "".join(mix_labels)
                + f"amix=inputs={len(mix_labels)}:duration=first:dropout_transition=2,"
                "loudnorm=I=-16:TP=-1.5:LRA=11,alimiter=limit=0.95[mix]"
            )
            _run_episode_render_ffmpeg(mix_args + [
                "-filter_complex", ";".join(mix_filters),
                "-map", "0:v:0", "-map", "[mix]",
                "-c:v", "copy", "-c:a", "aac", "-shortest", str(mixed_output),
            ])
            output.unlink(missing_ok=True)
            os.replace(mixed_output, output)
        subtitle_lines = []
        subtitle_index = 1
        custom_cues = timeline.get("subtitle_cues")
        if custom_cues is not None:
            for cue in sorted(custom_cues, key=lambda item: float(item["start"])):
                start = float(cue["start"])
                end = float(cue["end"])
                if start < 0 or end <= start or end > total_video_duration + 0.05:
                    raise HTTPException(422, "字幕时间必须位于成片时长范围内，且结束时间大于开始时间")
                subtitle_lines.extend([
                    str(subtitle_index),
                    f"{srt_time(start)} --> {srt_time(end)}",
                    str(cue["text"]).strip(),
                    "",
                ])
                subtitle_index += 1
        else:
            elapsed = 0.0
            transition_by_shot = {
                item["after_shot_id"]: item
                for item in transition_items
                if item.get("transition") in ("fade", "dissolve")
            }
            for shot, duration in zip(shots, actual_durations):
                dialogue = (shot.dialogue or "").strip()
                if dialogue:
                    subtitle_lines.extend([
                        str(subtitle_index),
                        f"{srt_time(elapsed)} --> {srt_time(elapsed + duration)}",
                        dialogue,
                        "",
                    ])
                    subtitle_index += 1
                overlap = float(transition_by_shot.get(shot.id, {}).get("duration_seconds", 0))
                elapsed += duration - overlap
        subtitle_path = None
        selected_subtitle_id = timeline.get("subtitle_track_id")
        if selected_subtitle_id:
            selected_track = db.query(SubtitleTrack).filter(
                SubtitleTrack.id == selected_subtitle_id
            ).first()
            if not selected_track:
                raise HTTPException(422, "时间轴选择的字幕轨道不存在")
            if selected_track.project_id not in (None, episode.project_id):
                raise HTTPException(422, "时间轴字幕轨道属于其他项目，不能跨项目使用")
            if selected_track.episode_id not in (None, episode.id):
                raise HTTPException(422, "时间轴字幕轨道属于其他分集，不能跨分集使用")
            if Path(selected_track.file_path).suffix.lower() != ".srt":
                raise HTTPException(422, "当前成片字幕烧录仅支持 SRT 格式")
            subtitle_path = str(safe_storage_file(selected_track.file_path))
        elif subtitle_lines:
            subtitle_file = SUBTITLE_DIR / f"episode_{episode_id}_{uuid.uuid4().hex[:8]}.srt"
            subtitle_file.write_text("\n".join(subtitle_lines), encoding="utf-8-sig")
            subtitle_path = str(subtitle_file)
            db.add(SubtitleTrack(
                name=f"第{episode.episode_number}集字幕",
                file_path=subtitle_path,
                format="srt",
                project_id=episode.project_id,
                episode_id=episode.id,
            ))
        subtitles_burned_in = False
        if subtitle_path and bool(timeline.get("subtitle_burn_in", True)):
            try:
                force_style = build_subtitle_force_style(timeline)
            except (TypeError, ValueError) as exc:
                raise HTTPException(422, "字幕样式配置无效：" + str(exc)) from exc
            subtitle_filter_path = (
                subtitle_path.replace("\\", "/")
                .replace(":", "\\:")
                .replace("'", "\\'")
            )
            burn_output = output.with_name(output.stem + "_subtitle_burn.mp4")
            _run_episode_render_ffmpeg([
                "-i", str(output),
                "-vf", f"subtitles='{subtitle_filter_path}':force_style='{force_style}'",
                "-map", "0:v:0", "-map", "0:a?",
                "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                "-c:a", "copy", "-movflags", "+faststart",
                str(burn_output),
            ])
            validate_rendered_video(
                burn_output, expected_duration=total_video_duration,
                duration_tolerance=2.0, require_audio=True,
            )
            output.unlink(missing_ok=True)
            os.replace(burn_output, output)
            subtitles_burned_in = True
        quality_report = validate_rendered_video(
            output, expected_duration=total_video_duration,
            duration_tolerance=2.0, require_audio=True,
        )
        render_task_id = getattr(_EPISODE_RENDER_CONTEXT, "task_id", None)
        if render_task_id is not None:
            with _EPISODE_RENDER_LOCK:
                render_runtime = _EPISODE_RENDER_RUNTIME.get(render_task_id)
                if not render_runtime or render_runtime["cancel"].is_set():
                    raise FFmpegCancelledError("用户在成片发布前请求停止合成")
                os.replace(output, final_output)
        else:
            os.replace(output, final_output)
        output = final_output
        db.add(MediaFile(
            name=output.name, category="video", file_path=str(output),
            mime_type="video/mp4", file_size=output.stat().st_size,
            duration=quality_report["duration_seconds"],
            metadata_json={
                "quality_checked": True,
                "quality_report": quality_report,
                "project_id": episode.project_id,
                "episode_id": episode.id,
                "rendered_output": True,
            },
        ))
        episode.status = "rendered"
        db.commit()
        for title_file in locals().get("title_files", []):
            title_file.unlink(missing_ok=True)
        return {"success": True, "episode_id": episode_id,
                "output": str(output), "subtitle_path": subtitle_path,
                "size": output.stat().st_size,
                "clip_count": len(clips), "duration_target": episode.duration_seconds,
                "subtitle_count": subtitle_index - 1,
                "subtitles_burned_in": subtitles_burned_in,
                "applied_timeline_version": timeline_record.version if timeline_record else 0,
                "applied_transitions": len(transition_items),
                "quality_report": quality_report}
    except FFmpegCancelledError:
        output.unlink(missing_ok=True)
        for title_file in locals().get("title_files", []):
            title_file.unlink(missing_ok=True)
        raise
    except HTTPException:
        output.unlink(missing_ok=True)
        for title_file in locals().get("title_files", []):
            title_file.unlink(missing_ok=True)
        raise
    except Exception as exc:
        output.unlink(missing_ok=True)
        for title_file in locals().get("title_files", []):
            title_file.unlink(missing_ok=True)
        raise HTTPException(500, "分集视频合成失败：" + str(exc))





# Episode assembly jobs reuse MediaTask and remain local-only. The worker handle
# is intentionally process-local; after restart a missing handle means review,
# never an automatic second render.
_EPISODE_RENDER_RUNTIME = {}
_EPISODE_RENDER_LOCK = threading.Lock()
_EPISODE_RENDER_CONTEXT = threading.local()


def _update_episode_render_job(task_id, *, status=None, progress=None, result_data=None, error=None, result_path=None):
    context_db = getattr(_EPISODE_RENDER_CONTEXT, "db", None)
    owns_session = context_db is None
    db = SessionLocal() if owns_session else context_db
    try:
        task = db.query(MediaTask).filter(
            MediaTask.id == task_id, MediaTask.task_type == "episode_render"
        ).first()
        if not task:
            return
        if status is not None:
            task.status = status
        if progress is not None:
            current = dict(task.result_data) if isinstance(task.result_data, dict) else {}
            current["progress"] = progress
            task.result_data = current
        if result_data is not None:
            task.result_data = result_data
        if error is not None:
            task.error = str(error)[:4000]
        if result_path is not None:
            task.result_path = str(result_path)
        task.updated_at = datetime.utcnow()
        db.commit()
    finally:
        if owns_session:
            db.close()


def _run_episode_render_ffmpeg(args):
    task_id = getattr(_EPISODE_RENDER_CONTEXT, "task_id", None)
    if task_id is None:
        return run_ffmpeg(args)
    with _EPISODE_RENDER_LOCK:
        runtime = _EPISODE_RENDER_RUNTIME.get(task_id)
    if not runtime:
        raise FFmpegCancelledError("分集合成任务执行上下文已丢失")
    if runtime["cancel"].is_set():
        raise FFmpegCancelledError("用户请求停止分集视频合成")
    phase = runtime["phase"]
    phase_index = phase["index"]
    phase["index"] += 1
    stages = [
        ("normalizing", 10, 35),
        ("mixing_audio", 35, 65),
        ("burning_subtitles", 65, 88),
    ]
    stage, lower, upper = stages[min(phase_index, len(stages) - 1)]
    _update_episode_render_job(task_id, progress={
        "percent": lower, "stage": stage, "phase": phase_index + 1,
        "phase_count": 3,
    })

    def report(progress):
        if runtime["cancel"].is_set():
            return
        elapsed = float(progress.get("out_time_seconds") or 0)
        duration = float(progress.get("duration_seconds") or 0)
        fraction = min(0.98, elapsed / duration) if duration > 0 else 0.0
        percent = round(lower + (upper - lower) * fraction, 1)
        _update_episode_render_job(task_id, progress={
            "percent": percent, "stage": stage,
            "phase": phase_index + 1, "phase_count": 3,
            "out_time_seconds": elapsed,
        })

    run_ffmpeg_with_progress(
        args,
        duration_seconds=getattr(_EPISODE_RENDER_CONTEXT, "duration_seconds", None),
        progress_callback=report,
        cancel_event=runtime["cancel"],
    )
    if runtime["cancel"].is_set():
        raise FFmpegCancelledError("用户请求停止分集视频合成")


def _run_episode_render_job(task_id, episode_id, output_name, music_path, music_volume):
    db = SessionLocal()
    try:
        task = db.query(MediaTask).filter(
            MediaTask.id == task_id, MediaTask.task_type == "episode_render"
        ).first()
        if not task or task.status != "queued":
            return
        with _EPISODE_RENDER_LOCK:
            runtime = _EPISODE_RENDER_RUNTIME.get(task_id)
            if runtime is None:
                runtime = {"cancel": threading.Event(), "phase": {"index": 0}}
                _EPISODE_RENDER_RUNTIME[task_id] = runtime
        if runtime["cancel"].is_set():
            task.status = "cancelled"
            task.error = "任务启动前已收到停止请求；未启动 FFmpeg"
            db.commit()
            return
        task.status = "processing"
        task.error = None
        task.result_data = {"progress": {"percent": 5, "stage": "preparing"}}
        db.commit()
        _EPISODE_RENDER_CONTEXT.task_id = task_id
        _EPISODE_RENDER_CONTEXT.db = db
        result = render_episode_from_shots(
            episode_id, output_name=output_name,
            music_path=music_path, music_volume=music_volume, db=db,
        )
        # Successful atomic publication is the point of no return: a late stop
        # request must not relabel a valid, already-published episode as cancelled.
        task = db.query(MediaTask).filter(MediaTask.id == task_id).first()
        task.status = "completed"
        task.result_path = result.get("output")
        task.result_data = {
            "progress": {"percent": 100, "stage": "completed"},
            "result": result,
        }
        task.error = None
        db.commit()
    except FFmpegCancelledError as exc:
        _update_episode_render_job(
            task_id, status="cancelled",
            progress={"percent": 0, "stage": "cancelled"},
            error=str(exc),
        )
    except Exception as exc:
        _update_episode_render_job(
            task_id, status="failed",
            progress={"percent": 0, "stage": "failed"},
            error=str(exc),
        )
    finally:
        if hasattr(_EPISODE_RENDER_CONTEXT, "task_id"):
            del _EPISODE_RENDER_CONTEXT.task_id
        if hasattr(_EPISODE_RENDER_CONTEXT, "db"):
            del _EPISODE_RENDER_CONTEXT.db
        db.close()
        with _EPISODE_RENDER_LOCK:
            _EPISODE_RENDER_RUNTIME.pop(task_id, None)


@router.post("/video/episodes/{episode_id}/render/jobs", status_code=202)
def create_episode_render_job(
    episode_id: int,
    background_tasks: BackgroundTasks,
    output_name: str = "episode.mp4",
    music_path: Optional[str] = None,
    music_volume: float = 0.18,
    db: Session = Depends(get_db),
):
    episode = db.query(EpisodePlan).filter(EpisodePlan.id == episode_id).first()
    if not episode:
        raise HTTPException(404, "分集不存在")
    if not db.query(StoryProject).filter(StoryProject.id == episode.project_id).first():
        raise HTTPException(404, "剧本项目不存在")
    active = db.query(MediaTask).filter(
        MediaTask.task_type == "episode_render",
        MediaTask.status.in_(("queued", "processing")),
    ).all()
    if any(
        (item.request_data or {}).get("episode_id") == episode_id
        for item in active if isinstance(item.request_data, dict)
    ):
        raise HTTPException(409, "本集已有正在等待或执行的合成任务，请先查询任务状态")
    if not 0 <= float(music_volume) <= 2:
        raise HTTPException(422, "背景音乐音量必须在 0 到 2 之间")
    task = MediaTask(
        task_type="episode_render", provider="local_ffmpeg",
        model="libx264", status="queued", prompt="本地分集合成",
        request_data={
            "episode_id": episode_id, "output_name": output_name,
            "music_path": music_path, "music_volume": music_volume,
        },
        result_data={"progress": {"percent": 0, "stage": "queued"}},
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    with _EPISODE_RENDER_LOCK:
        _EPISODE_RENDER_RUNTIME[task.id] = {
            "cancel": threading.Event(), "phase": {"index": 0},
        }
    background_tasks.add_task(
        _run_episode_render_job, task.id, episode_id,
        output_name, music_path, music_volume,
    )
    return {
        "task_id": task.id, "status": "queued",
        "status_url": f"/api/media/video/episodes/render/jobs/{task.id}",
        "cancel_url": f"/api/media/video/episodes/render/jobs/{task.id}/cancel",
        "message": "分集视频合成已进入本地后台队列；不会调用云端 AI 服务。",
    }


@router.get("/video/episodes/render/jobs/{task_id}")
def get_episode_render_job(task_id: int, db: Session = Depends(get_db)):
    task = db.query(MediaTask).filter(
        MediaTask.id == task_id, MediaTask.task_type == "episode_render"
    ).first()
    if not task:
        raise HTTPException(404, "分集合成任务不存在")
    if task.status in ("queued", "processing"):
        with _EPISODE_RENDER_LOCK:
            running_here = task_id in _EPISODE_RENDER_RUNTIME
        if not running_here:
            task.status = "interrupted_unknown"
            task.error = "应用重启或执行进程丢失；已保留任务记录，未自动重新合成。请检查输出后手动重新创建任务。"
            task.updated_at = datetime.utcnow()
            db.commit()
    data = task.result_data if isinstance(task.result_data, dict) else {}
    return {
        "task_id": task.id, "episode_id": (task.request_data or {}).get("episode_id"),
        "status": task.status, "progress": data.get("progress", {}),
        "output": task.result_path if task.status == "completed" else None,
        "partial_output": task.result_path if task.status in ("interrupted_unknown", "failed", "cancelled") else None,
        "result": data.get("result") if task.status == "completed" else None,
        "error": task.error,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
    }


@router.get("/video/episodes/{episode_id}/render/jobs/latest")
def get_latest_episode_render_job(episode_id: int, db: Session = Depends(get_db)):
    candidates = db.query(MediaTask).filter(
        MediaTask.task_type == "episode_render"
    ).order_by(MediaTask.id.desc()).limit(200).all()
    task = next((
        item for item in candidates
        if isinstance(item.request_data, dict)
        and item.request_data.get("episode_id") == episode_id
    ), None)
    if not task:
        return {"task": None}
    return {"task": get_episode_render_job(task.id, db)}


@router.post("/video/episodes/render/jobs/{task_id}/cancel")
def cancel_episode_render_job(task_id: int, db: Session = Depends(get_db)):
    task = db.query(MediaTask).filter(
        MediaTask.id == task_id, MediaTask.task_type == "episode_render"
    ).first()
    if not task:
        raise HTTPException(404, "分集合成任务不存在")
    if task.status == "queued":
        with _EPISODE_RENDER_LOCK:
            runtime = _EPISODE_RENDER_RUNTIME.get(task_id)
            if runtime:
                runtime["cancel"].set()
        task.status = "cancelled"
        task.error = "用户在合成开始前取消任务"
        task.updated_at = datetime.utcnow()
        db.commit()
        return {"task_id": task_id, "status": "cancelled"}
    if task.status != "processing":
        raise HTTPException(409, f"当前任务状态为 {task.status}，不能停止")
    with _EPISODE_RENDER_LOCK:
        runtime = _EPISODE_RENDER_RUNTIME.get(task_id)
        if not runtime:
            task.status = "interrupted_unknown"
            task.error = "找不到本机 FFmpeg 执行上下文；未自动重启任务。"
            db.commit()
            return {"task_id": task_id, "status": task.status, "message": task.error}
        runtime["cancel"].set()
    return {
        "task_id": task_id, "status": "cancellation_requested",
        "message": "已向本地 FFmpeg 发送停止请求，请稍后查询最终状态。",
    }


@router.post("/video/episodes/{episode_id}/shots/{shot_id}/replace")
async def replace_episode_shot_video(
    episode_id: int,
    shot_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Replace one shot with a user-supplied local clip; never submits an AI task."""
    episode = db.query(EpisodePlan).filter(EpisodePlan.id == episode_id).first()
    shot = db.query(StoryboardShot).filter(
        StoryboardShot.id == shot_id,
        StoryboardShot.episode_id == episode_id,
    ).first()
    if not episode or not shot:
        raise HTTPException(404, "分集或镜头不存在")
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".mp4", ".mov", ".mkv", ".webm"}:
        raise HTTPException(415, "仅支持 MP4、MOV、MKV 或 WebM 视频文件")
    destination = VIDEO_DIR / f"local_replace_ep{episode_id}_shot{shot_id}_{uuid.uuid4().hex[:8]}{suffix}"
    temporary = destination.with_suffix(destination.suffix + ".uploading")
    size = 0
    try:
        with temporary.open("wb") as output:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > 1024 * 1024 * 1024:
                    raise HTTPException(413, "单个替换镜头视频不能超过 1GB")
                output.write(chunk)
        if size < 1024:
            raise HTTPException(400, "视频文件为空或过小")
        os.replace(temporary, destination)
        info = probe_media(destination)
        streams = info.get("streams") or []
        video_stream = next((item for item in streams if item.get("codec_type") == "video"), None)
        duration = float((info.get("format") or {}).get("duration") or 0)
        if not video_stream or duration <= 0 or duration > 600:
            raise HTTPException(422, "替换文件不是有效视频，或时长超过 10 分钟")
        task = MediaTask(
            task_type="storyboard_video",
            provider="local",
            model="local-import",
            status="completed",
            prompt="用户手动替换镜头素材",
            request_data={
                "episode_id": episode_id, "shot_id": shot_id,
                "shot_number": shot.shot_number, "local_replacement": True,
                "source_filename": Path(file.filename or "replacement").name,
            },
            result_path=str(destination),
        )
        db.add(task)
        db.add(MediaFile(
            name=destination.name, category="video", file_path=str(destination),
            mime_type=file.content_type or "video/mp4", file_size=size,
            duration=duration,
            metadata_json={"episode_id": episode_id, "shot_id": shot_id, "local_replacement": True},
        ))
        db.commit()
        db.refresh(task)
        return {
            "success": True, "episode_id": episode_id, "shot_id": shot_id,
            "task_id": task.id, "duration_seconds": round(duration, 3),
            "message": "镜头替换素材已保存。原视频和历史任务均保留；重新合成时将优先使用本次替换素材。",
        }
    except HTTPException:
        db.rollback()
        destination.unlink(missing_ok=True)
        raise
    except Exception as exc:
        db.rollback()
        destination.unlink(missing_ok=True)
        raise HTTPException(422, "镜头替换失败：" + str(exc)[:1000]) from exc
    finally:
        temporary.unlink(missing_ok=True)
        await file.close()



@router.post("/video/tasks")
def create_video_task(data: VideoTaskInput, db: Session = Depends(get_db)):
    config, key, base = get_provider(db, data.provider, data.confirm_paid)

    payload = {
        "model": data.model,
        "prompt": data.prompt,
        "duration": data.duration,
        "aspect_ratio": data.aspect_ratio,
        **data.extra_payload
    }
    path = "/" + data.create_path.lstrip("/")
    task = MediaTask(
        task_type="video",
        provider=data.provider,
        model=data.model,
        status="pending",
        prompt=data.prompt,
        request_data={
            "base_url": base,
            "status_path_template": data.status_path_template,
            "video_url_field": data.video_url_field,
            "status_field": data.status_field,
            "task_id_field": data.task_id_field,
            "confirm_paid": data.confirm_paid,
        },
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    try:
        acquire_task_lease(db, "media", task.id, priority=5)
        task.status = "submitting"
        db.commit()
        result = http_json(base + path, key, body=payload, timeout=300)
        remote_id = value_at(result, data.task_id_field)
        if not remote_id:
            raise RuntimeError(
                "服务商已返回响应，但未找到任务编号；提交结果不确定，请先到服务商控制台核对。"
            )
        task.remote_task_id = str(remote_id)
        task.result_data = result
        task.status = "submitted"
        task.error = None
        db.commit()
        release_task_lease(db, "media", task.id, True)
        return {"task_id": task.id, "remote_task_id": remote_id,
                "status": task.status, "provider_response": result}
    except Exception as exc:
        if task.status == "pending" and isinstance(exc, HTTPException) and exc.status_code == 429:
            task.status = "queued"
            task.error = str(exc.detail)[:2000]
            db.commit()
        elif task.status == "pending":
            task.status = "failed"
            task.error = ("任务尚未提交，未调用视频服务商，可安全重新提交：" + str(exc))[:2000]
            db.commit()
        else:
            task.status = "submission_unknown"
            task.error = (
                "提交结果不确定：请先在视频服务商控制台核对是否已创建任务；"
                "确认不存在后再人工重新提交。原始错误：" + str(exc)
            )[:2000]
            db.commit()
            release_task_lease(db, "media", task.id, False, task.error)
        return {
            "success": False,
            "task_id": task.id,
            "status": task.status,
            "message": task.error,
        }


@router.post("/video/tasks/{task_id}/refresh")
def refresh_video_task(task_id: int, db: Session = Depends(get_db)):
    task = db.query(MediaTask).filter(MediaTask.id == task_id).first()
    if not task or task.task_type not in ("video", "storyboard_video"):
        raise HTTPException(404, "视频任务不存在")
    if task.status == "submission_unknown" or not task.remote_task_id:
        return {
            "task_id": task.id,
            "status": "submission_unknown",
            "error": task.error or "提交结果尚未核实，请先检查服务商控制台",
            "polling_skipped": True,
        }
    if task.result_path and Path(task.result_path).is_file():
        task.status = "completed"
        task.error = None
        db.commit()
        return {
            "task_id": task.id,
            "status": "completed",
            "result_path": task.result_path,
            "already_downloaded": True,
        }

    config, key, base = get_provider(
        db, task.provider, bool((task.request_data or {}).get("confirm_paid"))
    )
    settings = task.request_data or {}
    template = settings.get("status_path_template", "/videos/{task_id}")
    path = template.replace("{task_id}", str(task.remote_task_id))
    try:
        result = http_json(base + "/" + path.lstrip("/"), key, "GET")
        status = str(value_at(result, settings.get("status_field", "status")) or "processing")
        task.result_data = result
        task.status = status

        video_url = value_at(result, settings.get("video_url_field", "video_url"))
        if video_url and not (task.result_path and Path(task.result_path).is_file()):
            filename = f"video_{task.id}_{uuid.uuid4().hex[:8]}.mp4"
            destination = VIDEO_DIR / filename
            http_download(str(video_url), destination)
            media = MediaFile(
                name=filename, category="video", file_path=str(destination),
                mime_type="video/mp4", file_size=destination.stat().st_size
            )
            db.add(media)
            task.result_path = str(destination)
            task.status = "completed"

        db.commit()
        return {"task_id": task.id, "status": task.status,
                "result_path": task.result_path, "provider_response": result}
    except Exception as exc:
        task.error = str(exc)
        db.commit()
        raise HTTPException(502, f"查询视频任务失败：{exc}")


@router.get("/video/tasks")
def list_video_tasks(db: Session = Depends(get_db)):
    tasks = db.query(MediaTask).filter(
        MediaTask.task_type.in_(("video", "storyboard_video"))
    ).order_by(MediaTask.id.desc()).limit(200).all()
    return [{
        "id": t.id, "provider": t.provider, "model": t.model,
        "status": t.status, "prompt": t.prompt,
        "remote_task_id": t.remote_task_id,
        "result_path": t.result_path, "error": t.error
    } for t in tasks]


class SpeechInput(BaseModel):
    provider: str
    model: str
    text: str = Field(min_length=1, max_length=4096)
    voice: str = "alloy"
    instructions: Optional[str] = None
    response_format: Literal["mp3", "opus", "aac", "flac", "wav", "pcm"] = "mp3"
    speed: float = Field(default=1.0, ge=0.25, le=4.0)
    confirm_paid: bool = False
    force_regenerate: bool = False


class EpisodeDialogueSegmentInput(BaseModel):
    speaker_character_id: Optional[int] = None
    text: str = Field(min_length=1, max_length=2000)


def validate_dialogue_segments_for_shot(shot, segments):
    if not segments:
        return
    if len(segments) > 20:
        raise HTTPException(422, "每个镜头最多配置 20 条对白")
    allowed_speakers = {
        int(value) for value in (shot.character_ids or []) if str(value).isdigit()
    }
    for segment in segments:
        if not segment.text.strip():
            raise HTTPException(422, "对白内容不能为空或仅包含空格")
        if segment.speaker_character_id is not None and segment.speaker_character_id not in allowed_speakers:
            raise HTTPException(422, f"镜头 {shot.shot_number} 的对白说话人不属于该镜头角色")
        if len(allowed_speakers) > 1 and segment.speaker_character_id is None:
            raise HTTPException(422, f"镜头 {shot.shot_number} 有多个角色，请为每句对白指定说话人")


class EpisodeSpeechInput(BaseModel):
    provider: str
    model: str
    voice: str = "alloy"
    voice_by_character_id: dict[str, str] = Field(default_factory=dict)
    speaker_by_shot_id: dict[str, int] = Field(default_factory=dict)
    dialogue_segments_by_shot_id: dict[str, list[EpisodeDialogueSegmentInput]] = Field(default_factory=dict)
    confirm_paid: bool = False
    speed: float = Field(default=1.0, ge=0.5, le=2.0)


@router.post("/speech/episodes/{episode_id}/generate")
def generate_episode_voice_track(
    episode_id: int, data: EpisodeSpeechInput, db: Session = Depends(get_db)
):
    episode = db.query(EpisodePlan).filter(EpisodePlan.id == episode_id).first()
    if not episode:
        raise HTTPException(404, "分集不存在")
    project = db.query(StoryProject).filter(StoryProject.id == episode.project_id).first()
    if not project:
        raise HTTPException(404, "剧本项目不存在")
    ensure_project_source_current(project)
    shots = db.query(StoryboardShot).filter(
        StoryboardShot.episode_id == episode_id
    ).order_by(StoryboardShot.sort_order, StoryboardShot.shot_number).all()
    if not shots:
        raise HTTPException(400, "本集没有分镜")
    episode_character_ids = {
        str(character_id)
        for shot in shots
        for character_id in (shot.character_ids or [])
    }
    unknown_voice_profiles = set(data.voice_by_character_id) - episode_character_ids
    if unknown_voice_profiles:
        raise HTTPException(422, "角色音色配置引用了不属于本集分镜的角色")
    if any(not str(value).strip() or len(str(value)) > 100 for value in data.voice_by_character_id.values()):
        raise HTTPException(422, "角色音色名称不能为空且不能超过100个字符")
    shots_by_id = {str(shot.id): shot for shot in shots}
    unknown_speaker_shots = set(data.speaker_by_shot_id) - set(shots_by_id)
    if unknown_speaker_shots:
        raise HTTPException(422, "说话人配置引用了不属于本集的分镜")
    for shot_key, speaker_id in data.speaker_by_shot_id.items():
        if str(speaker_id) not in {str(value) for value in (shots_by_id[shot_key].character_ids or [])}:
            raise HTTPException(422, "所选说话人不属于对应分镜")
    unknown_segment_shots = set(data.dialogue_segments_by_shot_id) - set(shots_by_id)
    if unknown_segment_shots:
        raise HTTPException(422, "对白拆分引用了不属于本集的镜头")
    for shot_key, segments in data.dialogue_segments_by_shot_id.items():
        validate_dialogue_segments_for_shot(shots_by_id[shot_key], segments)

    def voice_for_shot(shot):
        selected_speaker = data.speaker_by_shot_id.get(str(shot.id))
        try:
            return resolve_episode_shot_voice(
                shot, data.voice_by_character_id, data.voice, selected_speaker
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    def voice_for_segment(shot, segment):
        if segment.speaker_character_id is not None:
            return data.voice_by_character_id.get(str(segment.speaker_character_id), data.voice)
        return voice_for_shot(shot)

    # Prefer the real generated clip duration over the planned storyboard duration.
    # This keeps the voice timeline aligned with the video that will actually be rendered.
    clip_durations = {}
    video_tasks = db.query(MediaTask).filter(
        MediaTask.task_type == "storyboard_video"
    ).order_by(MediaTask.id.desc()).all()
    for video_task in video_tasks:
        meta = video_task.request_data if isinstance(video_task.request_data, dict) else {}
        shot_id = meta.get("shot_id")
        if (
            meta.get("episode_id") == episode_id
            and shot_id not in clip_durations
            and video_task.status == "completed"
            and video_task.result_path
            and Path(video_task.result_path).is_file()
        ):
            try:
                info = probe_media(video_task.result_path)
                clip_durations[shot_id] = max(
                    1.0, float((info.get("format") or {}).get("duration") or 0)
                )
            except Exception:
                # Fall back to the authored shot duration if a provider file cannot be probed.
                pass

    config, key, base = get_provider(db, data.provider, data.confirm_paid)
    if getattr(config, "purpose", "text") not in ("text", "audio", "speech"):
        raise HTTPException(400, "所选服务商用途不支持语音合成，请选择文本或语音服务商")
    generated = {}
    speech_durations = {}
    dialogue_line_timings = {}
    failures = []
    warnings = []
    blocked_shots = set()
    pending_tasks = {}
    prior_tasks = db.query(MediaTask).filter(
        MediaTask.task_type == "episode_speech"
    ).order_by(MediaTask.id.desc()).all()
    # Explicitly segmented dialogue is generated line-by-line so each speaker can use
    # their own configured voice. Each line has its own idempotent task record.
    segmented_shot_ids = {
        int(key) for key, segments in data.dialogue_segments_by_shot_id.items() if segments
    }
    segmented_audio = {}
    for shot_key, segments in data.dialogue_segments_by_shot_id.items():
        if not segments:
            continue
        shot = shots_by_id[shot_key]
        segment_paths = []
        segment_durations = []
        segment_failed = False
        for segment_index, segment in enumerate(segments):
            text_value = segment.text.strip()
            selected_voice = voice_for_segment(shot, segment)
            prior_segment = None
            for prior in db.query(MediaTask).filter(
                MediaTask.task_type == "episode_speech_segment"
            ).order_by(MediaTask.id.desc()).all():
                meta = prior.request_data if isinstance(prior.request_data, dict) else {}
                if (
                    meta.get("episode_id") == episode_id
                    and meta.get("shot_id") == shot.id
                    and meta.get("segment_index") == segment_index
                    and meta.get("speaker_character_id") == segment.speaker_character_id
                    and prior.provider == data.provider
                    and prior.model == data.model
                    and prior.prompt == text_value
                    and meta.get("voice") == selected_voice
                    and float(meta.get("speed", 1.0)) == float(data.speed)
                ):
                    prior_segment = prior
                    break
            if prior_segment and prior_segment.status == "completed" and prior_segment.result_path and Path(prior_segment.result_path).is_file():
                segment_path = Path(prior_segment.result_path)
                segment_info = probe_media(segment_path)
                segment_duration = float((segment_info.get("format") or {}).get("duration") or 0)
                if segment_duration <= 0:
                    blocked_shots.add(shot.id)
                    failures.append({
                        "shot_number": shot.shot_number,
                        "segment_index": segment_index,
                        "task_id": prior_segment.id,
                        "error": "已缓存的对白音频时长无效，请检查素材文件；系统不会自动重复请求以避免额外费用。",
                    })
                    segment_failed = True
                    break
                segment_paths.append(segment_path)
                segment_durations.append(segment_duration)
                continue
            if prior_segment and speech_task_is_in_flight(prior_segment):
                segment_failed = True
                failures.append({
                    "shot_number": shot.shot_number,
                    "segment_index": segment_index,
                    "task_id": prior_segment.id,
                    "status": prior_segment.status,
                    "error": "该句对白已有任务正在提交或等待核实；为避免重复扣费，本次不会再次提交。",
                })
                break
            filename = f"episode_{episode_id}_shot_{shot.id}_line_{segment_index + 1}_{uuid.uuid4().hex[:8]}.mp3"
            destination = AUDIO_DIR / filename
            task = prior_segment if prior_segment and prior_segment.status == "pending" else MediaTask(
                task_type="episode_speech_segment", provider=data.provider, model=data.model,
                status="pending", prompt=text_value,
                request_data={
                    "episode_id": episode_id, "shot_id": shot.id,
                    "shot_number": shot.shot_number, "segment_index": segment_index,
                    "speaker_character_id": segment.speaker_character_id,
                    "voice": selected_voice, "speed": data.speed,
                    "confirm_paid": data.confirm_paid,
                },
            )
            if prior_segment is None:
                db.add(task)
                db.commit()
                db.refresh(task)
            request_started = False
            lease_acquired = False
            try:
                acquire_task_lease(db, "media", task.id, priority=2)
                lease_acquired = True
                task.status = "submitting"
                task.error = None
                db.commit()
                payload = {
                    "model": data.model, "input": text_value,
                    "voice": selected_voice, "response_format": "mp3",
                    "speed": data.speed,
                }
                request = urllib.request.Request(
                    base + "/audio/speech",
                    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
                    method="POST",
                )
                request_started = True
                with urllib.request.urlopen(request, timeout=300) as response:
                    with destination.open("wb") as output:
                        while True:
                            chunk = response.read(1024 * 1024)
                            if not chunk:
                                break
                            output.write(chunk)
                if destination.stat().st_size < 128:
                    raise RuntimeError("语音服务返回的音频文件为空或无效")
                segment_info = probe_media(destination)
                segment_duration = float((segment_info.get("format") or {}).get("duration") or 0)
                if segment_duration <= 0:
                    raise RuntimeError("语音服务返回的音频时长无效")
                task.status = "completed"
                task.result_path = str(destination)
                task.error = None
                task.request_data = {
                    **(task.request_data if isinstance(task.request_data, dict) else {}),
                    "audio_duration": segment_duration,
                }
                db.add(MediaFile(
                    name=filename, category="audio", file_path=str(destination),
                    mime_type="audio/mpeg", file_size=destination.stat().st_size,
                ))
                db.add(AIUsageLog(
                    provider=data.provider, model=data.model, task="episode_speech_segment",
                    status="completed",
                    detail=f"分集：{episode_id}；镜头：{shot.id}；对白行：{segment_index + 1}；任务：{task.id}",
                ))
                db.commit()
                release_task_lease(db, "media", task.id, True)
                segment_paths.append(destination)
                segment_durations.append(segment_duration)
            except Exception as exc:
                db.rollback()
                task = db.query(MediaTask).filter(MediaTask.id == task.id).first()
                if task:
                    task.status = "submission_unknown" if request_started else "failed"
                    task.error = (
                        "语音请求已发出但结果无法确认；请核对服务商记录，勿直接重复提交。"
                        if request_started else "语音请求尚未发出："
                    ) + str(exc)[:1000]
                    db.commit()
                    if lease_acquired:
                        release_task_lease(db, "media", task.id, False, task.error)
                destination.unlink(missing_ok=True)
                failures.append({
                    "shot_number": shot.shot_number,
                    "segment_index": segment_index,
                    "task_id": task.id if task else None,
                    "status": task.status if task else "failed",
                    "error": str(exc)[:1000],
                })
                segment_failed = True
                break
        if segment_failed or not segment_paths:
            blocked_shots.add(shot.id)
            continue
        combined_duration = sum(segment_durations)
        target_duration = clip_durations.get(
            shot.id, max(1.0, float(shot.duration_seconds or 5))
        )
        if combined_duration > target_duration * 1.5:
            blocked_shots.add(shot.id)
            failures.append({
                "shot_number": shot.shot_number,
                "error": f"拆分对白总时长 {combined_duration:.1f} 秒超过镜头时长 {target_duration:.1f} 秒的 1.5 倍，请延长镜头或精简对白。",
            })
            continue
        if len(segment_paths) == 1:
            shot_audio = segment_paths[0]
        else:
            shot_audio = AUDIO_DIR / f"episode_{episode_id}_shot_{shot.id}_dialogue_{uuid.uuid4().hex[:8]}.mp3"
            concat_inputs = []
            concat_filters = []
            concat_labels = []
            for index, segment_path in enumerate(segment_paths):
                concat_inputs += ["-i", str(segment_path)]
                concat_filters.append(
                    f"[{index}:a:0]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,asetpts=PTS-STARTPTS[line{index}]"
                )
                concat_labels.append(f"[line{index}]")
            concat_filters.append("".join(concat_labels) + f"concat=n={len(segment_paths)}:v=0:a=1[out]")
            run_ffmpeg(concat_inputs + [
                "-filter_complex", ";".join(concat_filters), "-map", "[out]",
                "-c:a", "libmp3lame", "-q:a", "3", str(shot_audio),
            ])
            db.add(MediaFile(
                name=shot_audio.name, category="audio", file_path=str(shot_audio),
                mime_type="audio/mpeg", file_size=shot_audio.stat().st_size,
            ))
            db.commit()
        generated[shot.id] = shot_audio
        speech_durations[shot.id] = combined_duration
        segmented_audio[shot.id] = len(segment_paths)
        dialogue_line_timings[shot.id] = [
            {
                "text": segment.text.strip(),
                "speaker_character_id": segment.speaker_character_id,
                "duration_seconds": segment_durations[index],
            }
            for index, segment in enumerate(segments)
        ]

    handled_shots = set()
    for prior in prior_tasks:
        prior_meta = prior.request_data if isinstance(prior.request_data, dict) else {}
        prior_shot_id = prior_meta.get("shot_id")
        if prior_shot_id in segmented_shot_ids:
            continue
        current_shot = next((item for item in shots if item.id == prior_shot_id), None)
        current_dialogue = (current_shot.dialogue or "").strip() if current_shot else None
        selected_voice = voice_for_shot(current_shot) if current_shot else data.voice
        matches = (
            prior.provider == data.provider
            and prior.model == data.model
            and prior_meta.get("voice") == selected_voice
            and float(prior_meta.get("speed", 1.0)) == float(data.speed)
            and prior_meta.get("episode_id") == episode_id
            and prior_shot_id
            and current_dialogue
            and prior.prompt == current_dialogue
        )
        if not matches or prior_shot_id in handled_shots:
            continue
        if (
            prior.status == "completed"
            and prior.result_path
            and Path(prior.result_path).is_file()
        ):
            generated[prior_shot_id] = Path(prior.result_path)
            handled_shots.add(prior_shot_id)
        elif prior.status == "pending":
            # A pending task is known not to have reached the provider yet;
            # reuse it instead of creating another local row after a restart.
            pending_tasks[prior_shot_id] = prior
            handled_shots.add(prior_shot_id)
        elif speech_task_is_in_flight(prior):
            blocked_shots.add(prior_shot_id)
            handled_shots.add(prior_shot_id)
            failures.append({
                "shot_number": current_shot.shot_number,
                "task_id": prior.id,
                "status": prior.status,
                "error": "该镜头已有配音任务正在排队、提交或等待人工核实；为避免重复扣费，本次不会再次调用语音服务商。",
            })

    for shot in shots:
        existing_audio = generated.get(shot.id)
        if existing_audio:
            try:
                info = probe_media(existing_audio)
                speech_duration = float((info.get("format") or {}).get("duration") or 0)
                target_duration = clip_durations.get(
                    shot.id, max(1.0, float(shot.duration_seconds or 5))
                )
                if speech_duration > target_duration * 1.5:
                    generated.pop(shot.id, None)
                    blocked_shots.add(shot.id)
                    failures.append({
                        "shot_number": shot.shot_number,
                        "error": f"对白音频约 {speech_duration:.1f} 秒，超过镜头时长 {target_duration:.1f} 秒的 1.5 倍；为避免截断台词，请拆分对白或延长镜头。",
                    })
                else:
                    speech_durations[shot.id] = speech_duration
            except Exception as exc:
                generated.pop(shot.id, None)
                blocked_shots.add(shot.id)
                failures.append({"shot_number": shot.shot_number, "error": f"无法读取已有配音时长：{exc}"})
        if shot.id in generated or shot.id in blocked_shots:
            continue
        text_value = (shot.dialogue or "").strip()
        if not text_value:
            continue
        if len(text_value) > 4096:
            failures.append({"shot_number": shot.shot_number, "error": "单镜头对白超过4096字符，请先拆分对白"})
            continue
        selected_voice = voice_for_shot(shot)
        if len(shot.character_ids or []) > 1 and str(shot.id) not in data.speaker_by_shot_id:
            warnings.append({
                "shot_number": shot.shot_number,
                "message": "该镜头包含多个角色且未指定说话人，暂用默认音色。请在分集工作台选择本镜头的对白说话人。",
            })
        filename = f"episode_{episode_id}_shot_{shot.id}_{uuid.uuid4().hex[:8]}.mp3"
        destination = AUDIO_DIR / filename
        payload = {
            "model": data.model, "input": text_value,
            "voice": selected_voice, "response_format": "mp3",
            "speed": data.speed,
        }
        request = urllib.request.Request(
            base + "/audio/speech",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
            method="POST",
        )
        task = pending_tasks.get(shot.id)
        if task is None:
            task = MediaTask(
                task_type="episode_speech", provider=data.provider, model=data.model,
                status="pending", prompt=text_value,
                request_data={"episode_id": episode_id, "shot_id": shot.id,
                              "shot_number": shot.shot_number, "voice": selected_voice,
                              "character_ids": shot.character_ids or [],
                              "speaker_character_id": data.speaker_by_shot_id.get(str(shot.id)),
                              "speed": data.speed, "confirm_paid": data.confirm_paid},
            )
            db.add(task)
            db.commit()
            db.refresh(task)

        lease_acquired = False
        request_started = False
        try:
            acquire_task_lease(db, "media", task.id, priority=2)
            lease_acquired = True
            task.status = "submitting"
            task.error = None
            db.commit()

            request_started = True
            with urllib.request.urlopen(request, timeout=300) as response:
                with destination.open("wb") as output:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
            if destination.stat().st_size < 128:
                raise RuntimeError("语音服务返回的音频文件为空或无效")
            audio_info = probe_media(destination)
            speech_duration = float((audio_info.get("format") or {}).get("duration") or 0)
            target_duration = clip_durations.get(
                shot.id, max(1.0, float(shot.duration_seconds or 5))
            )
            if speech_duration > target_duration * 1.5:
                raise RuntimeError(
                    f"对白音频约 {speech_duration:.1f} 秒，超过镜头时长 {target_duration:.1f} 秒的 1.5 倍；"
                    "请拆分对白或延长镜头，系统不会直接截断台词。"
                )
            task.status = "completed"
            task.result_path = str(destination)
            task.error = None
            task.request_data = {
                **(task.request_data if isinstance(task.request_data, dict) else {}),
                "audio_duration": speech_duration,
            }
            db.add(MediaFile(
                name=filename, category="audio", file_path=str(destination),
                mime_type="audio/mpeg", file_size=destination.stat().st_size,
            ))
            db.add(AIUsageLog(
                provider=data.provider, model=data.model, task="episode_speech",
                status="completed", detail=f"分集：{episode_id}；镜头：{shot.id}；本地任务：{task.id}",
            ))
            db.commit()
            release_task_lease(db, "media", task.id, True)
            generated[shot.id] = destination
            speech_durations[shot.id] = speech_duration
        except HTTPException as exc:
            db.rollback()
            task = db.query(MediaTask).filter(MediaTask.id == task.id).first()
            if exc.status_code == 429:
                if task:
                    task.status = "queued"
                    task.error = str(exc.detail)[:2000]
                    db.commit()
                failures.append({"shot_number": shot.shot_number, "task_id": task.id if task else None,
                                 "status": "queued", "error": str(exc.detail)[:1000]})
            elif exc.status_code == 409:
                failures.append({"shot_number": shot.shot_number, "task_id": task.id if task else None,
                                 "status": task.status if task else "unknown",
                                 "error": "该配音任务已被另一个请求占用；没有再次调用语音服务商。"})
            else:
                if task:
                    task.status = "submission_unknown" if request_started else "failed"
                    task.error = (
                        "语音请求已发出，但结果无法确认。请核对语音服务商记录后再人工确认重试。"
                        if request_started else str(exc.detail)
                    )[:2000]
                    if request_started:
                        db.add(AIUsageLog(
                            provider=data.provider, model=data.model, task="episode_speech",
                            status="submission_unknown", detail=f"分集：{episode_id}；镜头：{shot.id}；本地任务：{task.id}；{str(exc.detail)[:1000]}",
                        ))
                    db.commit()
                if lease_acquired:
                    release_task_lease(db, "media", task.id, False, task.error if task else str(exc.detail))
                destination.unlink(missing_ok=True)
                failures.append({"shot_number": shot.shot_number, "task_id": task.id if task else None,
                                 "status": task.status if task else "failed", "error": str(exc.detail)[:1000]})
        except Exception as exc:
            db.rollback()
            task = db.query(MediaTask).filter(MediaTask.id == task.id).first()
            if task:
                task.status = "submission_unknown" if request_started else "failed"
                task.error = (
                    "语音请求已发出，但结果无法确认。请核对语音服务商记录后再人工确认重试。原始错误："
                    if request_started else "语音请求尚未发出："
                ) + str(exc)[:1500]
                task.error = task.error[:2000]
                if request_started:
                    db.add(AIUsageLog(
                        provider=data.provider, model=data.model, task="episode_speech",
                        status="submission_unknown", detail=f"分集：{episode_id}；镜头：{shot.id}；本地任务：{task.id}；{str(exc)[:1000]}",
                    ))
                db.commit()
                if lease_acquired:
                    release_task_lease(db, "media", task.id, False, task.error)
            destination.unlink(missing_ok=True)
            failures.append({"shot_number": shot.shot_number, "task_id": task.id if task else None,
                             "status": task.status if task else "failed", "error": str(exc)[:1000]})

    if not generated:
        raise HTTPException(502, "本集没有成功生成任何对白语音：" + json.dumps(failures, ensure_ascii=False)[:2000])

    # 为没有对白的镜头补静音，并按分镜时长拼成与视频时间线一致的语音轨。
    ffmpeg_args = []
    filters = []
    labels = []
    input_index = 0
    for index, shot in enumerate(shots):
        audio_path = generated.get(shot.id)
        duration = clip_durations.get(
            shot.id, max(1.0, float(shot.duration_seconds or 5))
        )
        if audio_path:
            ffmpeg_args += ["-i", str(audio_path)]
            speech_duration = speech_durations.get(shot.id, 0.0)
            speed_ratio = speech_duration / duration if duration > 0 else 1.0
            tempo = f"atempo={min(1.5, max(1.0, speed_ratio)):.4f}," if speed_ratio > 1.02 else ""
        else:
            tempo = ""
            ffmpeg_args += [
                "-f", "lavfi", "-t", str(duration), "-i",
                "anullsrc=channel_layout=stereo:sample_rate=48000",
            ]
        filters.append(
            f"[{input_index}:a:0]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
            f"{tempo}apad,atrim=duration={duration},asetpts=PTS-STARTPTS[a{index}]"
        )
        labels.append(f"[a{index}]")
        input_index += 1
    filters.append("".join(labels) + f"concat=n={len(shots)}:v=0:a=1[aout]")
    track_path = AUDIO_DIR / f"episode_{episode_id}_voice_{uuid.uuid4().hex[:8]}.mp3"
    try:
        run_ffmpeg(ffmpeg_args + [
            "-filter_complex", ";".join(filters), "-map", "[aout]",
            "-c:a", "libmp3lame", "-q:a", "3", str(track_path),
        ])
        timeline_record = db.query(EpisodeEditTimeline).filter(
            EpisodeEditTimeline.episode_id == episode_id
        ).first()
        saved_timeline = timeline_record.edit_data if timeline_record and isinstance(timeline_record.edit_data, dict) else {}
        subtitle_cues = build_dialogue_subtitle_cues(
            shots=shots,
            shot_order=saved_timeline.get("shot_order") or [shot.id for shot in shots],
            clip_durations=clip_durations,
            speech_durations=speech_durations,
            dialogue_line_timings=dialogue_line_timings,
            transitions=saved_timeline.get("transitions") or [],
        )
        task = MediaTask(
            task_type="episode_speech_track", provider=data.provider, model=data.model,
            status="completed", prompt=f"第{episode.episode_number}集对白配音",
            result_path=str(track_path),
            request_data={"episode_id": episode_id, "project_id": episode.project_id,
                          "voice": data.voice,
                          "voice_by_character_id": data.voice_by_character_id,
                          "segmented_shots": segmented_audio,
                          "subtitle_cues": subtitle_cues,
                          "shot_count": len(generated), "aligned_to_storyboard": True},
        )
        db.add(task)
        db.add(MediaFile(
            name=track_path.name, category="audio", file_path=str(track_path),
            mime_type="audio/mpeg", file_size=track_path.stat().st_size,
            metadata_json={"project_id": episode.project_id, "episode_id": episode_id},
        ))
        db.commit()
    except Exception as exc:
        track_path.unlink(missing_ok=True)
        raise HTTPException(500, "对白语音合成失败：" + str(exc))
    return {
        "success": not failures, "episode_id": episode_id,
        "voice_track": str(track_path), "generated_shots": len(generated),
        "generated_dialogue_segments": sum(segmented_audio.values()),
        "segmented_shots": len(segmented_audio),
        "subtitle_cues": subtitle_cues,
        "failures": failures, "warnings": warnings,
        "message": f"已生成并按分镜时长合成对白语音轨，成功 {len(generated)} 个镜头，拆分对白 {sum(segmented_audio.values())} 句，失败 {len(failures)} 个，提醒 {len(warnings)} 条。",
    }


@router.post("/speech/generate")
def generate_speech(data: SpeechInput, db: Session = Depends(get_db)):
    config, key, base = get_provider(db, data.provider, data.confirm_paid)
    if getattr(config, "purpose", "text") not in ("text", "audio", "speech"):
        raise HTTPException(400, "所选服务商用途不支持语音合成，请选择文本或语音服务商")

    payload = {
        "model": data.model,
        "input": data.text,
        "voice": data.voice,
        "response_format": data.response_format,
        "speed": data.speed
    }
    if data.instructions:
        payload["instructions"] = data.instructions

    fingerprint = speech_fingerprint(
        data.provider, data.model, data.text, data.voice, data.speed,
        data.response_format, data.instructions,
    )
    matching = db.query(MediaTask).filter(
        MediaTask.task_type == "speech"
    ).order_by(MediaTask.id.desc()).all()
    task = None
    for prior in matching:
        meta = prior.request_data if isinstance(prior.request_data, dict) else {}
        if meta.get("speech_fingerprint") != fingerprint:
            continue
        if prior.status == "completed" and prior.result_path and Path(prior.result_path).is_file() and not data.force_regenerate:
            return {
                "status": "completed", "task_id": prior.id,
                "file": Path(prior.result_path).name, "path": prior.result_path,
                "size": Path(prior.result_path).stat().st_size, "reused": True,
                "message": "相同文本和语音参数已有有效配音，已复用原文件，没有再次调用 AI。",
            }
        if prior.status == "pending":
            task = prior
            break
        if speech_task_is_in_flight(prior):
            raise HTTPException(
                409,
                f"相同配音请求已有任务 #{prior.id}（{prior.status}）。"
                "请先在统一任务中心查看或核实，不要再次点击生成，以免重复扣费。",
            )

    if task is None:
        task = MediaTask(
            task_type="speech", provider=data.provider, model=data.model,
            status="pending", prompt=data.text,
            request_data={
                "speech_fingerprint": fingerprint,
                "voice": data.voice, "speed": data.speed,
                "response_format": data.response_format,
                "instructions": data.instructions or "",
                "confirm_paid": data.confirm_paid,
            },
        )
        db.add(task)
        db.commit()
        db.refresh(task)

    filename = f"speech_{uuid.uuid4().hex}.{data.response_format}"
    destination = AUDIO_DIR / filename
    request = urllib.request.Request(
        base + "/audio/speech",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json"
        },
        method="POST"
    )
    lease_acquired = False
    request_started = False
    try:
        acquire_task_lease(db, "media", task.id, priority=2)
        lease_acquired = True
        task.status = "submitting"
        task.error = None
        db.commit()

        request_started = True
        with urllib.request.urlopen(request, timeout=300) as response:
            with destination.open("wb") as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
        if not destination.is_file() or destination.stat().st_size < 128:
            raise RuntimeError("语音服务返回的音频文件为空或无效")

        task.status = "completed"
        task.result_path = str(destination)
        task.error = None
        db.add(MediaFile(
            name=filename, category="audio", file_path=str(destination),
            mime_type="audio/" + data.response_format,
            file_size=destination.stat().st_size
        ))
        db.add(AIUsageLog(
            provider=data.provider, model=data.model, task="speech",
            status="completed", detail=f"独立配音；本地任务：{task.id}；格式：{data.response_format}",
        ))
        db.commit()
        release_task_lease(db, "media", task.id, True)
        return {
            "status": "completed", "task_id": task.id, "file": filename,
            "path": str(destination), "size": destination.stat().st_size,
            "reused": False,
        }
    except HTTPException as exc:
        db.rollback()
        task = db.query(MediaTask).filter(MediaTask.id == task.id).first()
        if exc.status_code == 429:
            if task:
                task.status = "queued"
                task.error = str(exc.detail)[:2000]
                db.commit()
            raise
        if exc.status_code == 409:
            # Another request owns this task lease. Do not change its state.
            raise
        if task:
            task.status = "submission_unknown" if request_started else "failed"
            task.error = (
                "语音请求已发出，但结果无法确认。请核对语音服务商记录后再人工确认重试。"
                if request_started else str(exc.detail)
            )[:2000]
            if request_started:
                db.add(AIUsageLog(
                    provider=data.provider, model=data.model, task="speech",
                    status="submission_unknown", detail=f"本地任务：{task.id}；{str(exc.detail)[:1000]}",
                ))
            db.commit()
        if lease_acquired and task:
            release_task_lease(db, "media", task.id, False, task.error)
        destination.unlink(missing_ok=True)
        raise HTTPException(502, f"语音生成失败（任务 #{task.id if task else '未知'}）：{exc.detail}") from exc
    except Exception as exc:
        db.rollback()
        task = db.query(MediaTask).filter(MediaTask.id == task.id).first()
        if task:
            task.status = "submission_unknown" if request_started else "failed"
            task.error = (
                "语音请求已发出，但结果无法确认。请核对语音服务商记录后再人工确认重试。原始错误："
                if request_started else "语音请求尚未发出："
            ) + str(exc)[:1500]
            task.error = task.error[:2000]
            if request_started:
                db.add(AIUsageLog(
                    provider=data.provider, model=data.model, task="speech",
                    status="submission_unknown", detail=f"本地任务：{task.id}；{str(exc)[:1000]}",
                ))
            db.commit()
        if lease_acquired and task:
            release_task_lease(db, "media", task.id, False, task.error)
        destination.unlink(missing_ok=True)
        raise HTTPException(502, f"语音生成失败（任务 #{task.id if task else '未知'}）：{exc}") from exc


@router.post("/files/upload")
async def upload_media(
    file: UploadFile = File(...),
    category: str = "video",
    description: str = Form(""),
    db: Session = Depends(get_db),
):
    allowed = {
        "video": VIDEO_DIR,
        "audio": AUDIO_DIR,
        "image": STORAGE / "images",
        "other": STORAGE / "other",
    }
    if category not in allowed:
        raise HTTPException(400, "不支持的素材类别")

    original_name = Path(file.filename or "upload.bin").name
    suffix = Path(original_name).suffix.lower()[:20]
    folder = allowed[category]
    folder.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}{suffix}"
    destination = folder / filename
    temporary = destination.with_suffix(destination.suffix + ".uploading")
    size = 0
    limit = 2 * 1024 * 1024 * 1024

    try:
        with temporary.open("wb") as output:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, "单个文件不能超过 2GB")
                output.write(chunk)
        if size == 0:
            raise HTTPException(400, "不能上传空文件")
        os.replace(temporary, destination)

        media = MediaFile(
            name=original_name, category=category,
            file_path=str(destination), mime_type=file.content_type,
            file_size=size,
            metadata_json={"description": description[:2000], "stored_name": filename},
        )
        db.add(media)
        db.commit()
        db.refresh(media)
        return {"id": media.id, "name": media.name, "category": category,
                "size": size, "path": media.file_path,
                "description": description[:2000]}
    except Exception:
        db.rollback()
        destination.unlink(missing_ok=True)
        temporary.unlink(missing_ok=True)
        raise
    finally:
        await file.close()


@router.get("/files")
def list_media_files(category: Optional[str] = None, db: Session = Depends(get_db)):
    query = db.query(MediaFile)
    if category:
        query = query.filter(MediaFile.category == category)
    return [{
        "id": f.id, "name": f.name, "category": f.category,
        "path": f.file_path, "size": f.file_size,
        "mime_type": f.mime_type,
        "description": (f.metadata_json or {}).get("description", ""),
        "url": f"/api/media/files/{f.id}/download",
        "created_at": f.created_at
    } for f in query.order_by(MediaFile.id.desc()).limit(500).all()]


@router.get("/files/{file_id}/download")
def download_media_file(file_id: int, db: Session = Depends(get_db)):
    media = db.query(MediaFile).filter(MediaFile.id == file_id).first()
    if not media:
        raise HTTPException(404, "素材文件不存在")
    try:
        path = safe_storage_file(media.file_path)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "素材文件不存在或路径无效") from exc
    return FileResponse(
        path=str(path),
        media_type=media.mime_type or "application/octet-stream",
        filename=media.name,
    )


@router.delete("/files/{file_id}")
def delete_media_file(file_id: int, db: Session = Depends(get_db)):
    media = db.query(MediaFile).filter(MediaFile.id == file_id).first()
    if not media:
        raise HTTPException(404, "素材文件不存在")
    try:
        path = safe_storage_file(media.file_path)
        path.unlink(missing_ok=True)
    except FileNotFoundError:
        pass
    except ValueError as exc:
        raise HTTPException(400, "素材文件路径无效，已阻止删除目录外文件") from exc
    db.delete(media)
    db.commit()
    return {"success": True, "id": file_id}


class SubtitleCue(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    text: str = Field(min_length=1, max_length=5000)


class SubtitleInput(BaseModel):
    name: str = "字幕"
    cues: list[SubtitleCue]
    project_id: Optional[int] = Field(default=None, gt=0)
    episode_id: Optional[int] = Field(default=None, gt=0)


def srt_time(seconds):
    ms = int(round(seconds * 1000))
    hours, ms = divmod(ms, 3600000)
    minutes, ms = divmod(ms, 60000)
    secs, ms = divmod(ms, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{ms:03}"


@router.post("/subtitles")
def create_subtitle(data: SubtitleInput, db: Session = Depends(get_db)):
    if not data.cues:
        raise HTTPException(400, "字幕内容不能为空")
    lines = []
    for index, cue in enumerate(data.cues, 1):
        if cue.end <= cue.start:
            raise HTTPException(400, f"第 {index} 条字幕结束时间必须大于开始时间")
        lines.extend([
            str(index),
            f"{srt_time(cue.start)} --> {srt_time(cue.end)}",
            cue.text,
            ""
        ])
    filename = f"subtitle_{uuid.uuid4().hex}.srt"
    destination = SUBTITLE_DIR / filename
    destination.write_text("\n".join(lines), encoding="utf-8-sig")

    project_id = data.project_id
    episode_id = data.episode_id
    if episode_id is not None:
        episode = db.query(EpisodePlan).filter(EpisodePlan.id == episode_id).first()
        if not episode:
            destination.unlink(missing_ok=True)
            raise HTTPException(404, "指定分集不存在")
        if project_id is not None and project_id != episode.project_id:
            destination.unlink(missing_ok=True)
            raise HTTPException(422, "指定项目与分集不匹配")
        project_id = episode.project_id
    if project_id is not None and not db.query(StoryProject).filter(
        StoryProject.id == project_id
    ).first():
        destination.unlink(missing_ok=True)
        raise HTTPException(404, "指定剧本项目不存在")
    track = SubtitleTrack(
        name=data.name,
        file_path=str(destination),
        format="srt",
        project_id=project_id,
        episode_id=episode_id,
    )
    db.add(track)
    db.commit()
    db.refresh(track)
    return {
        "id": track.id, "name": track.name, "path": track.file_path,
        "project_id": track.project_id, "episode_id": track.episode_id,
    }


@router.get("/subtitles")
def list_subtitles(
    project_id: Optional[int] = None,
    episode_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    query = db.query(SubtitleTrack)
    if project_id is not None:
        query = query.filter(SubtitleTrack.project_id == project_id)
    if episode_id is not None:
        query = query.filter(SubtitleTrack.episode_id == episode_id)
    rows = query.order_by(SubtitleTrack.id.desc()).all()
    return [{
        "id": x.id, "name": x.name, "path": x.file_path,
        "format": x.format, "language": x.language,
        "project_id": x.project_id, "episode_id": x.episode_id,
    } for x in rows]


class RenderInput(BaseModel):
    video_path: str
    output_name: str = "rendered_video.mp4"
    audio_path: Optional[str] = None
    subtitle_path: Optional[str] = None
    music_volume: float = Field(default=0.25, ge=0, le=2)
    original_volume: float = Field(default=1.0, ge=0, le=2)
    width: int = Field(default=1920, ge=256, le=3840)
    height: int = Field(default=1080, ge=256, le=3840)


@router.post("/render")
def render_video(data: RenderInput):
    output = None
    try:
        video = safe_storage_file(data.video_path)
        output_name = re.sub(r"[^a-zA-Z0-9_\-\u4e00-\u9fff]", "_", data.output_name)
        if not output_name.lower().endswith(".mp4"):
            output_name += ".mp4"
        if data.width % 2 or data.height % 2:
            raise HTTPException(400, "输出宽度和高度必须是偶数")
        output = VIDEO_DIR / f"{uuid.uuid4().hex}_{output_name}"

        args = ["-i", str(video)]
        audio = safe_storage_file(data.audio_path) if data.audio_path else None
        subtitle = safe_storage_file(data.subtitle_path) if data.subtitle_path else None

        video_info = probe_media(video)
        has_original_audio = any(
            stream.get("codec_type") == "audio"
            for stream in video_info.get("streams", [])
        )
        if audio:
            args += ["-i", str(audio)]
            if has_original_audio:
                filter_complex = (
                    f"[0:a]volume={data.original_volume}[original];"
                    f"[1:a]volume={data.music_volume}[music];"
                    "[original][music]amix=inputs=2:duration=first:dropout_transition=2[mix]"
                )
            else:
                filter_complex = f"[1:a]volume={data.music_volume}[mix]"
            args += ["-filter_complex", filter_complex, "-map", "0:v:0",
                     "-map", "[mix]", "-shortest"]
        else:
            args += ["-map", "0:v:0"]
            if has_original_audio:
                args += ["-map", "0:a:0"]
            else:
                args += ["-an"]

        video_filters = [
            f"scale={data.width}:{data.height}:force_original_aspect_ratio=decrease",
            f"pad={data.width}:{data.height}:(ow-iw)/2:(oh-ih)/2",
        ]
        if subtitle:
            escaped = str(subtitle).replace("\\", "/").replace(":", "\\:")
            video_filters.append(f"subtitles='{escaped}'")
        args += ["-vf", ",".join(video_filters)]

        args += [
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-c:a", "aac", "-movflags", "+faststart", str(output)
        ]
        run_ffmpeg(args)
        quality_report = validate_rendered_video(
            output, require_audio=bool(audio or has_original_audio),
        )
        return {"status": "completed", "output": str(output),
                "size": output.stat().st_size, "quality_report": quality_report}
    except Exception as exc:
        if output and output.exists():
            output.unlink()
        raise HTTPException(500, f"视频合成失败：{exc}")


# Local render jobs are process-local by design: this desktop app runs one API
# worker. Persisted state remains authoritative after restart; a missing runtime
# handle is treated as interrupted/unknown, never automatically resubmitted.
_LOCAL_RENDER_RUNTIME = {}
_LOCAL_RENDER_LOCK = threading.Lock()


def _local_render_args(payload, output_path):
    video = safe_storage_file(payload["video_path"])
    audio = safe_storage_file(payload["audio_path"]) if payload.get("audio_path") else None
    subtitle = safe_storage_file(payload["subtitle_path"]) if payload.get("subtitle_path") else None
    if payload["width"] % 2 or payload["height"] % 2:
        raise ValueError("输出宽度和高度必须是偶数")
    video_info = probe_media(video)
    has_original_audio = any(
        stream.get("codec_type") == "audio"
        for stream in video_info.get("streams", [])
    )
    args = ["-i", str(video)]
    if audio:
        args += ["-i", str(audio)]
        if has_original_audio:
            filter_complex = (
                f"[0:a]volume={payload['original_volume']}[original];"
                f"[1:a]volume={payload['music_volume']}[music];"
                "[original][music]amix=inputs=2:duration=first:dropout_transition=2[mix]"
            )
        else:
            filter_complex = f"[1:a]volume={payload['music_volume']}[mix]"
        args += ["-filter_complex", filter_complex, "-map", "0:v:0",
                 "-map", "[mix]", "-shortest"]
    else:
        args += ["-map", "0:v:0"]
        if has_original_audio:
            args += ["-map", "0:a:0"]
        else:
            args += ["-an"]
    filters = [
        f"scale={payload['width']}:{payload['height']}:force_original_aspect_ratio=decrease",
        f"pad={payload['width']}:{payload['height']}:(ow-iw)/2:(oh-ih)/2",
    ]
    if subtitle:
        escaped = str(subtitle).replace("\\", "/").replace(":", "\\:")
        filters.append(f"subtitles='{escaped}'")
    args += ["-vf", ",".join(filters),
             "-c:v", "libx264", "-preset", "medium", "-crf", "20",
             "-c:a", "aac", "-movflags", "+faststart", str(output_path)]
    duration = float(video_info.get("format", {}).get("duration") or 0)
    return args, duration, bool(audio or has_original_audio)


def _update_local_render_job(task_id, *, status=None, progress=None, result_data=None, error=None, result_path=None):
    db = SessionLocal()
    try:
        task = db.query(MediaTask).filter(MediaTask.id == task_id).first()
        if not task:
            return
        if status is not None:
            task.status = status
        if progress is not None:
            current = dict(task.result_data) if isinstance(task.result_data, dict) else {}
            current["progress"] = progress
            task.result_data = current
        if result_data is not None:
            task.result_data = result_data
        if error is not None:
            task.error = str(error)[:4000]
        if result_path is not None:
            task.result_path = str(result_path)
        task.updated_at = datetime.utcnow()
        db.commit()
    finally:
        db.close()


def _run_local_render_job(task_id):
    db = SessionLocal()
    try:
        task = db.query(MediaTask).filter(MediaTask.id == task_id).first()
        if not task or task.task_type != "local_render" or task.status != "queued":
            return
        payload = dict(task.request_data or {})
        output = Path(task.result_path)
        temporary = output.with_name(output.stem + ".partial" + output.suffix)
        with _LOCAL_RENDER_LOCK:
            runtime = _LOCAL_RENDER_RUNTIME.get(task_id)
            if runtime is None:
                runtime = {"cancel": threading.Event(), "process": None}
                _LOCAL_RENDER_RUNTIME[task_id] = runtime
        if runtime["cancel"].is_set():
            task.status = "cancelled"
            task.error = "任务在启动前已收到停止请求；未启动 FFmpeg"
            db.commit()
            return
        task.status = "processing"
        task.error = None
        task.result_data = {"progress": {"percent": 0, "stage": "preparing"}}
        db.commit()
        args, duration, require_audio = _local_render_args(payload, temporary)

        def report(progress):
            _update_local_render_job(task_id, progress=progress)

        def process_changed(process):
            runtime["process"] = process

        run_ffmpeg_with_progress(
            args,
            duration_seconds=duration,
            progress_callback=report,
            cancel_event=runtime["cancel"],
            process_callback=process_changed,
        )
        if runtime["cancel"].is_set():
            raise FFmpegCancelledError("用户请求停止本地渲染")
        _update_local_render_job(task_id, progress={
            "percent": 99.9, "stage": "validating", "out_time_seconds": duration,
        })
        quality = validate_rendered_video(
            temporary, expected_duration=duration if duration > 0 else None,
            duration_tolerance=2.0, require_audio=require_audio,
        )
        os.replace(temporary, output)
        size = output.stat().st_size
        db.expire_all()
        task = db.query(MediaTask).filter(MediaTask.id == task_id).first()
        task.status = "completed"
        task.result_path = str(output)
        task.result_data = {"progress": {"percent": 100, "stage": "completed"},
                            "size": size, "quality_report": quality}
        task.error = None
        db.add(MediaFile(name=output.name, category="video", file_path=str(output),
                         mime_type="video/mp4", file_size=size,
                         duration=quality.get("duration_seconds")))
        db.commit()
    except FFmpegCancelledError as exc:
        _update_local_render_job(
            task_id, status="cancelled",
            error=str(exc),
            result_data={"progress": {"stage": "cancelled"},
                         "partial_output": str(temporary) if "temporary" in locals() and temporary.exists() else None},
        )
    except Exception as exc:
        _update_local_render_job(
            task_id, status="failed", error=str(exc),
            result_data={"progress": {"stage": "failed"},
                         "partial_output": str(temporary) if "temporary" in locals() and temporary.exists() else None},
        )
    finally:
        db.close()
        with _LOCAL_RENDER_LOCK:
            _LOCAL_RENDER_RUNTIME.pop(task_id, None)


@router.post("/render/jobs", status_code=202)
def create_local_render_job(
    data: RenderInput,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    # Validate every input and build a unique output name before persisting work.
    try:
        video = safe_storage_file(data.video_path)
        if data.audio_path:
            safe_storage_file(data.audio_path)
        if data.subtitle_path:
            safe_storage_file(data.subtitle_path)
        if data.width % 2 or data.height % 2:
            raise HTTPException(400, "输出宽度和高度必须是偶数")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"渲染输入检查失败：{exc}") from exc

    output_name = re.sub(r"[^a-zA-Z0-9_\-\u4e00-\u9fff]", "_", data.output_name)
    if not output_name.lower().endswith(".mp4"):
        output_name += ".mp4"
    output = VIDEO_DIR / f"{uuid.uuid4().hex}_{output_name}"
    task = MediaTask(
        task_type="local_render",
        provider="local_ffmpeg",
        model="libx264",
        status="queued",
        prompt="本地视频合成",
        request_data=data.model_dump(),
        result_path=str(output),
        result_data={"progress": {"percent": 0, "stage": "queued"}},
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    with _LOCAL_RENDER_LOCK:
        _LOCAL_RENDER_RUNTIME[task.id] = {"cancel": threading.Event(), "process": None}
    background_tasks.add_task(_run_local_render_job, task.id)
    return {"task_id": task.id, "status": task.status,
            "status_url": f"/api/media/render/jobs/{task.id}",
            "cancel_url": f"/api/media/render/jobs/{task.id}/cancel",
            "message": "本地渲染任务已保存并进入后台执行；不会调用云端 AI 服务。"}


@router.get("/render/jobs/{task_id}")
def get_local_render_job(task_id: int, db: Session = Depends(get_db)):
    task = db.query(MediaTask).filter(
        MediaTask.id == task_id, MediaTask.task_type == "local_render"
    ).first()
    if not task:
        raise HTTPException(404, "本地渲染任务不存在")
    if task.status in ("queued", "processing"):
        with _LOCAL_RENDER_LOCK:
            running_here = task_id in _LOCAL_RENDER_RUNTIME
        if not running_here:
            task.status = "interrupted_unknown"
            task.error = (
                "应用重启或执行进程丢失；已保留任务记录和部分输出，未自动重新渲染。"
                "请检查部分文件后手动新建任务。"
            )
            task.updated_at = datetime.utcnow()
            task_payload = task.request_data if isinstance(task.request_data, dict) else {}
            if task_payload.get("render_kind") == "video_edit":
                from app.models.editing import VideoEditJob
                edit_job = db.query(VideoEditJob).filter(
                    VideoEditJob.id == task_payload.get("video_edit_job_id")
                ).first()
                if edit_job:
                    edit_job.status = "interrupted_unknown"
                    edit_job.error = task.error
            db.commit()
    return {
        "task_id": task.id, "status": task.status, "task_type": task.task_type,
        "progress": (task.result_data or {}).get("progress", {}) if isinstance(task.result_data, dict) else {},
        "output": task.result_path if task.status == "completed" else None,
        "result": task.result_data if task.status == "completed" else None,
        "partial_output": (task.result_data or {}).get("partial_output") if isinstance(task.result_data, dict) else None,
        "error": task.error, "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
    }


@router.post("/render/jobs/{task_id}/cancel")
def cancel_local_render_job(task_id: int, db: Session = Depends(get_db)):
    task = db.query(MediaTask).filter(
        MediaTask.id == task_id, MediaTask.task_type == "local_render"
    ).first()
    if not task:
        raise HTTPException(404, "本地渲染任务不存在")
    if task.status == "queued":
        with _LOCAL_RENDER_LOCK:
            runtime = _LOCAL_RENDER_RUNTIME.get(task_id)
            if runtime:
                runtime["cancel"].set()
        task.status = "cancelled"
        task.error = "用户在渲染开始前取消任务"
        task.updated_at = datetime.utcnow()
        task_payload = task.request_data if isinstance(task.request_data, dict) else {}
        if task_payload.get("render_kind") == "video_edit":
            from app.models.editing import VideoEditJob
            edit_job = db.query(VideoEditJob).filter(
                VideoEditJob.id == task_payload.get("video_edit_job_id")
            ).first()
            if edit_job:
                edit_job.status = "cancelled"
                edit_job.error = task.error
        db.commit()
        return {"task_id": task_id, "status": "cancelled", "message": "已取消尚未启动的本地任务。"}
    if task.status != "processing":
        raise HTTPException(409, f"当前任务状态为 {task.status}，不能停止")
    with _LOCAL_RENDER_LOCK:
        runtime = _LOCAL_RENDER_RUNTIME.get(task_id)
        if not runtime:
            task.status = "interrupted_unknown"
            task.error = "找不到本机渲染进程；未尝试自动重启。"
            db.commit()
            return {"task_id": task_id, "status": task.status, "message": task.error}
        runtime["cancel"].set()
    return {"task_id": task_id, "status": "cancellation_requested",
            "message": "已向本地 FFmpeg 发送安全停止请求；稍后查询任务状态确认停止结果。"}



@router.get("/probe")
def media_probe(path: str):
    try:
        file_path = safe_storage_file(path)
        return probe_media(file_path)
    except Exception as exc:
        raise HTTPException(400, str(exc))


@router.get("/health")
def media_health():
    import shutil
    return {
        "media_api": "ready",
        "ffmpeg": shutil.which(os.environ.get("FFMPEG_PATH", "ffmpeg")),
        "ffprobe": shutil.which(os.environ.get("FFPROBE_PATH", "ffprobe")),
        "storage": str(STORAGE)
    }


@router.get("/validate")
def validate_media_output(path: str, expected_duration: Optional[float] = None):
    try:
        file_path = safe_storage_file(path)
        return validate_rendered_video(
            file_path, expected_duration=expected_duration,
            duration_tolerance=2.0, require_audio=False,
        )
    except Exception as exc:
        raise HTTPException(422, "视频质量检查未通过：" + str(exc)[:1200]) from exc



@router.get("/sync-check")
def check_media_audio_video_sync(path: str):
    """Compare audio/video stream timing metadata; does not infer lip sync."""
    try:
        file_path = safe_storage_file(path)
        return scan_audio_video_sync(file_path)
    except Exception as exc:
        raise HTTPException(422, "音画同步检查失败：" + str(exc)[:1200]) from exc


@router.get("/final-quality-report")
def scan_episode_final_quality(path: str, episode_id: Optional[int] = None, db: Session = Depends(get_db)):
    """Run an opt-in local final-cut report; findings are advisory."""
    try:
        file_path = safe_storage_file(path)
        subtitle_cues = []
        if episode_id is not None:
            record = db.query(EpisodeEditTimeline).filter(EpisodeEditTimeline.episode_id == episode_id).first()
            edit_data = record.edit_data if record and isinstance(record.edit_data, dict) else {}
            subtitle_cues = edit_data.get("subtitle_cues") or []
            if not subtitle_cues:
                speech_tasks = db.query(MediaTask).filter(
                    MediaTask.task_type == "episode_speech_track"
                ).order_by(MediaTask.id.desc()).all()
                for speech_task in speech_tasks:
                    speech_meta = speech_task.request_data if isinstance(speech_task.request_data, dict) else {}
                    if speech_meta.get("episode_id") == episode_id:
                        candidate = speech_meta.get("subtitle_cues")
                        if isinstance(candidate, list):
                            subtitle_cues = candidate
                        break
        from app.services.media_service import scan_final_cut_quality, analyze_shot_media_consistency
        report = scan_final_cut_quality(file_path, subtitle_cues)
        if episode_id is not None:
            readiness = get_episode_render_readiness(episode_id, db)
            shot_media = [{"shot_id": item.get("shot_id"), "shot_number": item.get("shot_number"), "path": (item.get("video") or {}).get("path")} for item in readiness.get("items", []) if item.get("status") == "ready" and (item.get("video") or {}).get("path")]
            consistency = analyze_shot_media_consistency(shot_media)
            report["shot_media_consistency"] = consistency
            report["warnings"].extend(consistency.get("warnings") or [])
            if consistency.get("warnings"): report["status"] = "warning"
        return report
    except Exception as exc:
        raise HTTPException(422, "成片综合质量检查失败：" + str(exc)[:1200]) from exc


@router.get("/quality-scan")
def scan_media_output_quality(path: str):
    """Run an opt-in FFmpeg scan for sustained black frames and silence."""
    try:
        file_path = safe_storage_file(path)
        return scan_media_quality(file_path)
    except Exception as exc:
        raise HTTPException(422, "媒体质量扫描失败：" + str(exc)[:1200]) from exc


# Episode editing timeline: local-only, explicit save; no AI/provider calls.
from app.models.story import EpisodeEditTimeline


class TimelineTransitionInput(BaseModel):
    after_shot_id: int
    transition: Literal["cut", "fade", "dissolve"] = "cut"
    duration_seconds: float = Field(default=0.5, ge=0, le=2)


class EpisodeSubtitleCueInput(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    text: str = Field(min_length=1, max_length=2000)


class EpisodeSoundEffectInput(BaseModel):
    path: str = Field(min_length=1, max_length=2000)
    name: str = Field(default="", max_length=300)
    start_seconds: float = Field(default=0, ge=0, le=900)
    volume: float = Field(default=1.0, ge=0, le=2)


class EpisodeTimelineInput(BaseModel):
    shot_order: list[int]
    character_voice_profiles: dict[str, str] = Field(default_factory=dict)
    speaker_by_shot_id: dict[str, int] = Field(default_factory=dict)
    dialogue_segments_by_shot_id: dict[str, list[EpisodeDialogueSegmentInput]] = Field(default_factory=dict)
    subtitle_burn_in: bool = True
    subtitle_font_name: str = Field(default="Microsoft YaHei", min_length=1, max_length=80, pattern=r"^[A-Za-z0-9 _-]+$")
    subtitle_font_size: int = Field(default=36, ge=16, le=72)
    subtitle_primary_color: str = Field(default="#FFFFFF", pattern=r"^#[0-9A-Fa-f]{6}$")
    subtitle_outline_color: str = Field(default="#000000", pattern=r"^#[0-9A-Fa-f]{6}$")
    subtitle_outline_width: float = Field(default=2, ge=0, le=8)
    subtitle_background: bool = False
    subtitle_alignment: Literal[2, 5, 8] = 2
    subtitle_margin_v: int = Field(default=60, ge=0, le=200)
    transitions: list[TimelineTransitionInput] = Field(default_factory=list)
    intro_title: str = Field(default="", max_length=120)
    outro_title: str = Field(default="", max_length=120)
    music_fade_in_seconds: float = Field(default=1.0, ge=0, le=30)
    music_fade_out_seconds: float = Field(default=2.0, ge=0, le=30)
    music_volume: float = Field(default=0.18, ge=0, le=1)
    music_path: Optional[str] = Field(default=None, max_length=2000)
    music_name: Optional[str] = Field(default=None, max_length=300)
    voice_path: Optional[str] = Field(default=None, max_length=2000)
    voice_name: Optional[str] = Field(default=None, max_length=300)
    voice_volume: float = Field(default=1.0, ge=0, le=2)
    original_volume: float = Field(default=0.35, ge=0, le=2)
    sound_effect_tracks: list[EpisodeSoundEffectInput] = Field(default_factory=list)
    subtitle_cues: Optional[list[EpisodeSubtitleCueInput]] = None
    subtitle_track_id: Optional[int] = Field(default=None, gt=0)



def build_dialogue_subtitle_cues(
    shots, shot_order, clip_durations, speech_durations,
    dialogue_line_timings, transitions,
):
    """Build approximate line-level subtitle timing from probed TTS durations.

    The generated cue boundaries follow concatenated speech segment durations and
    the same atempo ratio used by the dialogue-track renderer. Word-level alignment
    is not inferred; users can still adjust every cue manually.
    """
    shot_by_id = {shot.id: shot for shot in shots}
    ordered_ids = list(shot_order or [shot.id for shot in shots])
    if len(ordered_ids) != len(shot_by_id) or set(ordered_ids) != set(shot_by_id):
        ordered_ids = [shot.id for shot in shots]
    transition_by_shot = {
        item.get("after_shot_id"): item
        for item in transitions
        if isinstance(item, dict)
        and item.get("transition") in ("fade", "dissolve")
    }
    cues = []
    elapsed = 0.0
    for shot_id in ordered_ids:
        shot = shot_by_id[shot_id]
        target_duration = max(
            1.0,
            float(clip_durations.get(shot_id, shot.duration_seconds or 5)),
        )
        speech_duration = max(0.0, float(speech_durations.get(shot_id, 0.0)))
        if speech_duration <= 0:
            lines = []
        else:
            lines = dialogue_line_timings.get(shot_id) or [{
                "text": (shot.dialogue or "").strip(),
                "speaker_character_id": None,
                "duration_seconds": speech_duration,
            }]
        ratio = speech_duration / target_duration if target_duration > 0 else 1.0
        tempo_factor = round(min(1.5, max(1.0, ratio)), 4) if ratio > 1.02 else 1.0
        line_elapsed = elapsed
        shot_end = elapsed + target_duration
        for line in lines:
            text_value = str(line.get("text") or "").strip()
            line_duration = max(0.0, float(line.get("duration_seconds") or 0.0)) / tempo_factor
            cue_end = min(shot_end, line_elapsed + line_duration)
            if text_value and cue_end > line_elapsed:
                cues.append({
                    "start": round(line_elapsed, 3),
                    "end": round(cue_end, 3),
                    "text": text_value,
                })
            line_elapsed = cue_end
            if line_elapsed >= shot_end:
                break
        transition = transition_by_shot.get(shot_id) or {}
        overlap = max(0.0, float(transition.get("duration_seconds") or 0.0))
        elapsed += max(0.0, target_duration - overlap)
    return cues


def _media_file_belongs_to_project(media_record, project_id, episode_id=None):
    """Reject media explicitly owned by another project or episode; allow legacy shared rows."""
    metadata = media_record.metadata_json if isinstance(media_record.metadata_json, dict) else {}
    owner_project = metadata.get("project_id")
    owner_episode = metadata.get("episode_id")
    if owner_project not in (None, "", 0, "0"):
        if isinstance(owner_project, bool):
            return False
        try:
            if int(owner_project) != int(project_id):
                return False
        except (TypeError, ValueError):
            return False
    if episode_id is not None and owner_episode not in (None, "", 0, "0"):
        if isinstance(owner_episode, bool):
            return False
        try:
            if int(owner_episode) != int(episode_id):
                return False
        except (TypeError, ValueError):
            return False
    return True


def _timeline_episode_shots(db, episode_id):
    episode = db.query(EpisodePlan).filter(EpisodePlan.id == episode_id).first()
    if not episode:
        raise HTTPException(404, "分集不存在")
    return episode, db.query(StoryboardShot).filter(
        StoryboardShot.episode_id == episode_id
    ).order_by(StoryboardShot.sort_order, StoryboardShot.shot_number).all()


@router.get("/video/episodes/{episode_id}/timeline")
def get_episode_edit_timeline(episode_id: int, db: Session = Depends(get_db)):
    episode, shots = _timeline_episode_shots(db, episode_id)
    record = db.query(EpisodeEditTimeline).filter(
        EpisodeEditTimeline.episode_id == episode_id
    ).first()
    data = record.edit_data if record and isinstance(record.edit_data, dict) else {}
    data = dict(data)
    subtitle_track = None
    subtitle_track_warning = None
    try:
        selected_subtitle_id = int(data.get("subtitle_track_id") or 0)
    except (TypeError, ValueError):
        selected_subtitle_id = 0
    if selected_subtitle_id:
        candidate = db.query(SubtitleTrack).filter(
            SubtitleTrack.id == selected_subtitle_id
        ).first()
        if not candidate:
            subtitle_track_warning = "时间轴引用的字幕轨道已不存在"
        elif candidate.project_id not in (None, episode.project_id):
            subtitle_track_warning = "时间轴引用的字幕轨道属于其他项目，已停止使用"
        elif candidate.episode_id not in (None, episode.id):
            subtitle_track_warning = "时间轴引用的字幕轨道属于其他分集，已停止使用"
        else:
            subtitle_track = candidate
    if subtitle_track_warning:
        data.pop("subtitle_track_id", None)
    if not data:
        data = {
            "shot_order": [shot.id for shot in shots],
            "transitions": [],
            "intro_title": "",
            "outro_title": "",
            "music_fade_in_seconds": 1.0,
            "music_fade_out_seconds": 2.0,
            "music_volume": 0.18,
        }
    character_ids = {
        int(character_id)
        for shot in shots
        for character_id in (shot.character_ids or [])
        if str(character_id).isdigit()
    }
    characters = db.query(VisualAsset).filter(
        VisualAsset.id.in_(character_ids),
        VisualAsset.category.in_(("characters", "character")),
        VisualAsset.confirmed == True,
    ).all() if character_ids else []
    latest_speech_track = db.query(MediaTask).filter(
        MediaTask.task_type == "episode_speech_track"
    ).order_by(MediaTask.id.desc()).all()
    generated_subtitle_cues = []
    for speech_task in latest_speech_track:
        speech_meta = speech_task.request_data if isinstance(speech_task.request_data, dict) else {}
        if speech_meta.get("episode_id") == episode_id:
            candidate = speech_meta.get("subtitle_cues")
            if isinstance(candidate, list):
                generated_subtitle_cues = candidate
            break
    return {
        "episode_id": episode.id,
        "episode_title": episode.title,
        "version": record.version if record else 0,
        "timeline": data,
        "subtitle_track": ({
            "id": subtitle_track.id,
            "name": subtitle_track.name,
            "path": subtitle_track.file_path,
            "format": subtitle_track.format,
            "language": subtitle_track.language,
        } if subtitle_track else None),
        "subtitle_track_warning": subtitle_track_warning,
        "generated_subtitle_cues": generated_subtitle_cues,
        "characters": [{"id": item.id, "name": item.name} for item in characters],
        "shots": [{
            "id": shot.id, "shot_number": shot.shot_number,
            "duration_seconds": shot.duration_seconds,
            "scene_name": shot.scene_name,
            "visual_description": shot.visual_description,
            "dialogue": shot.dialogue,
            "character_ids": shot.character_ids or [],
        } for shot in shots],
    }


@router.put("/video/episodes/{episode_id}/timeline")
def save_episode_edit_timeline(
    episode_id: int, data: EpisodeTimelineInput, db: Session = Depends(get_db)
):
    episode, shots = _timeline_episode_shots(db, episode_id)
    valid_ids = {shot.id for shot in shots}
    if len(data.shot_order) != len(set(data.shot_order)):
        raise HTTPException(422, "镜头顺序中存在重复镜头")
    if set(data.shot_order) != valid_ids:
        raise HTTPException(422, "镜头顺序必须完整包含本集全部镜头，不能遗漏或加入其他分集镜头")
    transition_ids = [item.after_shot_id for item in data.transitions]
    if len(transition_ids) != len(set(transition_ids)):
        raise HTTPException(422, "同一镜头后不能重复设置转场")
    if any(shot_id not in valid_ids for shot_id in transition_ids):
        raise HTTPException(422, "转场引用了不属于本集的镜头")
    order_positions = {shot_id: index for index, shot_id in enumerate(data.shot_order)}
    shot_by_id = {shot.id: shot for shot in shots}
    for item in data.transitions:
        position = order_positions[item.after_shot_id]
        if position >= len(data.shot_order) - 1:
            raise HTTPException(422, "最后一个镜头后不能设置转场")
        if item.transition == "cut" and item.duration_seconds != 0:
            raise HTTPException(422, "直接切镜（cut）的转场时长必须为 0")
        if item.transition != "cut":
            next_shot = shot_by_id[data.shot_order[position + 1]]
            adjacent_limit = min(
                float(shot_by_id[item.after_shot_id].duration_seconds or 0),
                float(next_shot.duration_seconds or 0),
            ) / 2
            if item.duration_seconds <= 0 or item.duration_seconds >= adjacent_limit:
                raise HTTPException(422, "转场时长必须大于 0，且小于相邻较短镜头时长的一半")
    episode_character_ids = {
        str(character_id)
        for shot in shots
        for character_id in (shot.character_ids or [])
    }
    if set(data.character_voice_profiles) - episode_character_ids:
        raise HTTPException(422, "角色音色配置引用了不属于本集分镜的角色")
    if any(not str(value).strip() or len(str(value)) > 100 for value in data.character_voice_profiles.values()):
        raise HTTPException(422, "角色音色名称不能为空且不能超过100个字符")
    shots_by_id = {str(shot.id): shot for shot in shots}
    if set(data.speaker_by_shot_id) - set(shots_by_id):
        raise HTTPException(422, "说话人配置引用了不属于本集的分镜")
    for shot_key, speaker_id in data.speaker_by_shot_id.items():
        if str(speaker_id) not in {str(value) for value in (shots_by_id[shot_key].character_ids or [])}:
            raise HTTPException(422, "所选说话人不属于对应分镜")
    unknown_segment_shots = set(data.dialogue_segments_by_shot_id) - set(shots_by_id)
    if unknown_segment_shots:
        raise HTTPException(422, "对白拆分引用了不属于本集的镜头")
    for shot_key, segments in data.dialogue_segments_by_shot_id.items():
        validate_dialogue_segments_for_shot(shots_by_id[shot_key], segments)
    for selected_path, label in ((data.music_path, "背景音乐"), (data.voice_path, "对白配音")):
        if selected_path:
            audio_path = safe_storage_file(selected_path)
            audio_record = db.query(MediaFile).filter(
                MediaFile.category == "audio",
                MediaFile.file_path == str(audio_path),
            ).first()
            if not audio_record:
                raise HTTPException(422, f"{label}必须来自本地音频素材库")
            if not _media_file_belongs_to_project(audio_record, episode.project_id, episode.id):
                raise HTTPException(422, f"{label}属于其他项目，不能跨项目引用")
            audio_info = probe_media(audio_path)
            if not any(stream.get("codec_type") == "audio" for stream in audio_info.get("streams", [])):
                raise HTTPException(422, f"{label}文件不包含可用音轨")
    for effect in data.sound_effect_tracks:
        effect_path = safe_storage_file(effect.path)
        effect_record = db.query(MediaFile).filter(
            MediaFile.category == "audio",
            MediaFile.file_path == str(effect_path),
        ).first()
        if not effect_record:
            raise HTTPException(422, "音效必须来自本地音频素材库")
        if not _media_file_belongs_to_project(effect_record, episode.project_id, episode.id):
            raise HTTPException(422, "音效属于其他项目，不能跨项目引用")
        effect_info = probe_media(effect_path)
        if not any(stream.get("codec_type") == "audio" for stream in effect_info.get("streams", [])):
            raise HTTPException(422, "音效文件不包含可用音轨")
    if data.subtitle_track_id is not None:
        subtitle_track = db.query(SubtitleTrack).filter(
            SubtitleTrack.id == data.subtitle_track_id
        ).first()
        if not subtitle_track:
            raise HTTPException(422, "所选字幕轨道不存在")
        if subtitle_track.project_id not in (None, episode.project_id):
            raise HTTPException(422, "所选字幕轨道属于其他项目，不能跨项目引用")
        if subtitle_track.episode_id not in (None, episode.id):
            raise HTTPException(422, "所选字幕轨道属于其他分集，不能跨分集引用")
    for cue in data.subtitle_cues or []:
        if cue.end <= cue.start:
            raise HTTPException(422, "字幕结束时间必须大于开始时间")
    try:
        build_subtitle_force_style(data.model_dump())
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, "字幕样式配置无效：" + str(exc)) from exc
    payload = data.model_dump()
    record = db.query(EpisodeEditTimeline).filter(
        EpisodeEditTimeline.episode_id == episode_id
    ).first()
    if record:
        record.edit_data = payload
        record.version = int(record.version or 0) + 1
    else:
        record = EpisodeEditTimeline(
            episode_id=episode_id, edit_data=payload, version=1
        )
        db.add(record)
    db.commit()
    db.refresh(record)
    return {
        "success": True, "episode_id": episode_id,
        "version": record.version, "timeline": record.edit_data,
        "message": "剪辑时间轴已保存到本地项目数据库；不会调用 AI 服务或覆盖原始镜头。",
    }
