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

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.media import MediaTask, MediaFile, SubtitleTrack
from app.models.story import EpisodePlan, StoryboardShot, StoryProject, StoryAssetLink
from app.models.visual import VisualAsset
from app.models.studio import AIProviderConfig, AIRuntimeSetting, AIUsageLog
from app.services.task_runtime import acquire_task_lease, release_task_lease
from app.services.media_service import (
    ROOT, STORAGE, VIDEO_DIR, AUDIO_DIR, SUBTITLE_DIR,
    decrypt_api_key, http_json, http_download,
    safe_storage_file, run_ffmpeg, probe_media, validate_rendered_video,
    download_validated_video, finalize_rendered_video,
    validate_rendered_audio, finalize_rendered_audio
)

router = APIRouter(prefix="/api/media", tags=["媒体生产"])


def get_provider(db, provider_name, confirm_paid=False, check_paid=True):
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


def speech_fingerprint(provider, model, text, voice, speed, response_format="mp3", instructions=None):
    return json.dumps({
        "provider": provider, "model": model, "text": text, "voice": voice,
        "speed": float(speed), "response_format": response_format,
        "instructions": instructions or "",
    }, ensure_ascii=False, sort_keys=True)


def latest_completed_episode_shot_tasks(tasks, episode_id):
    """Return the newest usable completed clip per shot, ignoring failed retries."""
    selected = {}
    for task in sorted(tasks, key=lambda item: item.id, reverse=True):
        metadata = task.request_data if isinstance(task.request_data, dict) else {}
        shot_id = metadata.get("shot_id")
        if (
            metadata.get("episode_id") == episode_id
            and shot_id
            and shot_id not in selected
            and task.status == "completed"
            and task.result_path
            and Path(task.result_path).is_file()
        ):
            selected[shot_id] = task
    return selected


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
    ensure_episode_source_current(db, episode)
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
                try:
                    download_validated_video(str(video_url), destination)
                except Exception as exc:
                    task.status = "download_failed"
                    task.error = (
                        f"服务商已生成视频，但本地下载或完整性检查失败：{str(exc)[:500]}。"
                        "可刷新任务仅重试下载，不会重新提交视频生成请求。"
                    )
                    db.commit()
                    outcomes.append({
                        "task_id": task.id, "shot_number": meta.get("shot_number"),
                        "status": task.status, "error": task.error,
                        "retry_download_only": True,
                    })
                    continue
                task.result_path = str(destination)
                task.status = "completed"
                task.error = None
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
    ensure_episode_source_current(db, episode)
    shots = db.query(StoryboardShot).filter(
        StoryboardShot.episode_id == episode_id
    ).order_by(StoryboardShot.sort_order, StoryboardShot.shot_number).all()
    if not shots:
        raise HTTPException(400, "本集没有分镜")
    tasks = db.query(MediaTask).filter(
        MediaTask.task_type == "storyboard_video"
    ).all()
    # A later failed retry must not shadow an earlier successful clip.
    by_shot = latest_completed_episode_shot_tasks(tasks, episode_id)
    clips = []
    missing = []
    invalid = []
    for shot in shots:
        task = by_shot.get(shot.id)
        if not task:
            missing.append(shot.shot_number)
            continue
        try:
            clip = safe_storage_file(task.result_path)
            validate_rendered_video(clip)
            clips.append(clip)
        except Exception as exc:
            invalid.append(f"{shot.shot_number}（{str(exc)[:120]}）")
    if invalid:
        raise HTTPException(409, "以下镜头视频文件未通过完整性检查：" + "、".join(invalid) + "；原始素材已保留，请重新下载或替换。")
    if missing:
        raise HTTPException(409, "以下镜头视频尚未全部完成或下载：" + "、".join(map(str, missing)))
    output_name = re.sub(r"[^a-zA-Z0-9_-]", "_", output_name)
    if not output_name.lower().endswith(".mp4"):
        output_name += ".mp4"
    output = VIDEO_DIR / f"episode_{episode_id}_{uuid.uuid4().hex[:8]}_{output_name}"
    render_stage = output.with_name("." + output.stem + ".rendering.mp4")
    mixed_stage = output.with_name("." + output.stem + ".mixed.mp4")
    subtitle_stage = None
    render_warnings = []
    try:
        # 统一分辨率、帧率、像素格式和音频采样率，兼容不同视频模型输出规格。
        ffmpeg_args = []
        filters = []
        concat_labels = []
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
            concat_labels.append(f"[v{index}][a{index}]")
            input_index += 1
        total_video_duration = sum(actual_durations)
        if total_video_duration < 90 or total_video_duration > 150:
            raise HTTPException(
                400,
                f"本集镜头视频总时长为 {total_video_duration:.1f} 秒，必须控制在 90–150 秒；请调整镜头时长或视频生成设置后再合成。",
            )
        filters.append(
            "".join(concat_labels) + f"concat=n={len(clips)}:v=1:a=1[vout][aout]"
        )
        run_ffmpeg(ffmpeg_args + [
            "-filter_complex", ";".join(filters),
            "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-c:a", "aac", "-ar", "48000", "-movflags", "+faststart",
            str(render_stage),
        ])
        # Promote the first valid render before optional mixing. If mixing fails,
        # the un-mixed episode remains available as a recoverable deliverable.
        quality_report = finalize_rendered_video(
            render_stage, output, expected_duration=total_video_duration,
            duration_tolerance=2.0, require_audio=True,
        )
        # Mix generated dialogue and optional background music with original shot sound.
        speech_tasks = db.query(MediaTask).filter(
            MediaTask.task_type == "episode_speech_track"
        ).order_by(MediaTask.id.desc()).all()
        speech_path = None
        for speech_task in speech_tasks:
            speech_meta = speech_task.request_data if isinstance(speech_task.request_data, dict) else {}
            if speech_meta.get("episode_id") == episode_id and speech_task.result_path and Path(speech_task.result_path).is_file():
                try:
                    candidate = safe_storage_file(speech_task.result_path)
                    validate_rendered_audio(candidate)
                    speech_path = candidate
                    break
                except Exception as exc:
                    render_warnings.append(f"已有配音轨无法使用：{str(exc)[:240]}；保留原始配音素材。")
                    continue
        music = None
        if music_path:
            try:
                music = safe_storage_file(music_path)
                validate_rendered_audio(music)
            except Exception as exc:
                render_warnings.append(f"背景音乐无法使用：{str(exc)[:240]}；本次跳过背景音乐。")
        if speech_path or music:
            try:
                mix_args = ["-i", str(output)]
                mix_filters = ["[0:a]volume=0.35[original]"]
                mix_labels = ["[original]"]
                mix_index = 1
                if speech_path:
                    mix_args += ["-i", str(speech_path)]
                    mix_filters.append(f"[{mix_index}:a]volume=1.0[voice]")
                    mix_labels.append("[voice]")
                    mix_index += 1
                if music:
                    mix_args += ["-stream_loop", "-1", "-i", str(music)]
                    mix_filters.append(f"[{mix_index}:a]volume={max(0.0, min(2.0, music_volume))}[music]")
                    mix_labels.append("[music]")
                mix_filters.append("".join(mix_labels) + f"amix=inputs={len(mix_labels)}:duration=first:dropout_transition=2[mix]")
                run_ffmpeg(mix_args + [
                    "-filter_complex", ";".join(mix_filters),
                    "-map", "0:v:0", "-map", "[mix]",
                    "-c:v", "copy", "-c:a", "aac", "-shortest", str(mixed_stage),
                ])
                quality_report = finalize_rendered_video(
                    mixed_stage, output, expected_duration=total_video_duration,
                    duration_tolerance=2.0, require_audio=True,
                )
            except Exception as exc:
                mixed_stage.unlink(missing_ok=True)
                render_warnings.append(f"音轨混合失败：{str(exc)[:300]}；已保留无额外配音/背景音乐的基础成片。")
        subtitle_lines = []
        elapsed = 0.0
        subtitle_index = 1
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
            elapsed += duration
        subtitle_path = None
        if subtitle_lines:
            subtitle_file = SUBTITLE_DIR / f"episode_{episode_id}_{uuid.uuid4().hex[:8]}.srt"
            subtitle_stage = subtitle_file.with_name("." + subtitle_file.name + ".tmp")
            try:
                subtitle_stage.write_text("\n".join(subtitle_lines), encoding="utf-8-sig")
                if not subtitle_stage.is_file() or subtitle_stage.stat().st_size == 0:
                    raise RuntimeError("字幕文件生成失败或内容为空")
                os.replace(subtitle_stage, subtitle_file)
                subtitle_path = str(subtitle_file)
                db.add(SubtitleTrack(name=f"第{episode.episode_number}集字幕", file_path=subtitle_path, format="srt"))
            except Exception as exc:
                subtitle_stage.unlink(missing_ok=True)
                render_warnings.append(f"字幕文件生成失败：{str(exc)[:240]}；视频成片已保留，可稍后重新生成字幕。")
        db.add(MediaFile(
            name=output.name, category="video", file_path=str(output),
            mime_type="video/mp4", file_size=output.stat().st_size,
            duration=quality_report["duration_seconds"],
            metadata_json={"quality_checked": True, "quality_report": quality_report},
        ))
        episode.status = "rendered"
        db.commit()
        return {"success": True, "episode_id": episode_id,
                "output": str(output), "subtitle_path": subtitle_path,
                "size": output.stat().st_size,
                "clip_count": len(clips), "duration_target": episode.duration_seconds,
                "subtitle_count": subtitle_index - 1 if subtitle_path else 0,
                "quality_report": quality_report,
                "warnings": render_warnings}
    except HTTPException:
        render_stage.unlink(missing_ok=True)
        mixed_stage.unlink(missing_ok=True)
        if subtitle_stage:
            subtitle_stage.unlink(missing_ok=True)
        raise
    except Exception as exc:
        render_stage.unlink(missing_ok=True)
        mixed_stage.unlink(missing_ok=True)
        if subtitle_stage:
            subtitle_stage.unlink(missing_ok=True)
        # Never delete a validated final output or the source shot/audio assets on failure.
        raise HTTPException(500, "分集视频合成失败；已有镜头、配音和已验证成片均予以保留：" + str(exc))



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
        try:
            validate_rendered_video(task.result_path)
        except Exception as exc:
            # Keep the previous file for diagnosis, but do not advertise it as a usable result.
            settings = dict(task.request_data or {})
            settings["invalid_result_path"] = task.result_path
            task.request_data = settings
            task.result_path = None
            task.status = "download_failed"
            task.error = f"上次下载文件未通过完整性检查：{str(exc)[:500]}；可刷新任务重新下载，不会重新提交生成请求。"
            db.commit()
        else:
            task.status = "completed"
            task.error = None
            db.commit()
            return {
                "task_id": task.id,
                "status": "completed",
                "result_path": task.result_path,
                "already_downloaded": True,
            }

    # Polling/downloading an already-submitted task does not create a new paid request.
    config, key, base = get_provider(
        db, task.provider, bool((task.request_data or {}).get("confirm_paid")),
        check_paid=False,
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
        if video_url:
            filename = f"video_{task.id}_{uuid.uuid4().hex[:8]}.mp4"
            destination = VIDEO_DIR / filename
            temporary = VIDEO_DIR / f".download_{task.id}_{uuid.uuid4().hex[:8]}.mp4"
            try:
                http_download(str(video_url), temporary)
                # Never expose/register a partial or corrupt download as a completed video.
                validate_rendered_video(temporary)
                os.replace(temporary, destination)
            except (ValueError, RuntimeError, OSError) as exc:
                if temporary.exists():
                    temporary.unlink()
                task.status = "download_failed"
                task.error = (
                    f"视频已由服务商生成，但下载文件未通过完整性检查：{str(exc)[:500]}。"
                    "可刷新任务重新下载；不会重新提交视频生成请求。"
                )
                db.commit()
                return {
                    "task_id": task.id,
                    "status": task.status,
                    "error": task.error,
                    "retry_download_only": True,
                }
            media = MediaFile(
                name=filename, category="video", file_path=str(destination),
                mime_type="video/mp4", file_size=destination.stat().st_size
            )
            db.add(media)
            task.result_path = str(destination)
            task.status = "completed"
            task.error = None

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


class EpisodeSpeechInput(BaseModel):
    provider: str
    model: str
    voice: str = "alloy"
    confirm_paid: bool = False
    speed: float = Field(default=1.0, ge=0.5, le=2.0)


@router.post("/speech/episodes/{episode_id}/generate")
def generate_episode_voice_track(
    episode_id: int, data: EpisodeSpeechInput, db: Session = Depends(get_db)
):
    episode = db.query(EpisodePlan).filter(EpisodePlan.id == episode_id).first()
    if not episode:
        raise HTTPException(404, "分集不存在")
    ensure_episode_source_current(db, episode)
    shots = db.query(StoryboardShot).filter(
        StoryboardShot.episode_id == episode_id
    ).order_by(StoryboardShot.sort_order, StoryboardShot.shot_number).all()
    if not shots:
        raise HTTPException(400, "本集没有分镜")

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
    failures = []
    blocked_shots = set()
    pending_tasks = {}
    prior_tasks = db.query(MediaTask).filter(
        MediaTask.task_type == "episode_speech"
    ).order_by(MediaTask.id.desc()).all()
    handled_shots = set()
    for prior in prior_tasks:
        prior_meta = prior.request_data if isinstance(prior.request_data, dict) else {}
        prior_shot_id = prior_meta.get("shot_id")
        current_shot = next((item for item in shots if item.id == prior_shot_id), None)
        current_dialogue = (current_shot.dialogue or "").strip() if current_shot else None
        matches = (
            prior.provider == data.provider
            and prior.model == data.model
            and prior_meta.get("voice") == data.voice
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
                audio_report = validate_rendered_audio(existing_audio)
                speech_duration = audio_report["duration_seconds"]
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
        filename = f"episode_{episode_id}_shot_{shot.id}_{uuid.uuid4().hex[:8]}.mp3"
        destination = AUDIO_DIR / filename
        temporary_audio = None
        payload = {
            "model": data.model, "input": text_value,
            "voice": data.voice, "response_format": "mp3",
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
                              "shot_number": shot.shot_number, "voice": data.voice,
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
            temporary_audio = destination.with_name("." + destination.stem + ".partial" + destination.suffix)
            with urllib.request.urlopen(request, timeout=300) as response:
                with temporary_audio.open("wb") as output:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
            audio_report = finalize_rendered_audio(temporary_audio, destination)
            speech_duration = audio_report["duration_seconds"]
            target_duration = clip_durations.get(
                shot.id, max(1.0, float(shot.duration_seconds or 5))
            )
            if speech_duration <= 0:
                raise RuntimeError("FFprobe 无法确认配音时长")
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
                if temporary_audio is not None:
                    temporary_audio.unlink(missing_ok=True)
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
            if temporary_audio is not None:
                temporary_audio.unlink(missing_ok=True)
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
    track_stage = track_path.with_name("." + track_path.stem + ".rendering.mp3")
    try:
        run_ffmpeg(ffmpeg_args + [
            "-filter_complex", ";".join(filters), "-map", "[aout]",
            "-c:a", "libmp3lame", "-q:a", "3", str(track_stage),
        ])
        validate_rendered_audio(track_stage)
        os.replace(track_stage, track_path)
        task = MediaTask(
            task_type="episode_speech_track", provider=data.provider, model=data.model,
            status="partial" if failures else "completed", prompt=f"第{episode.episode_number}集对白配音",
            result_path=str(track_path),
            request_data={"episode_id": episode_id, "voice": data.voice,
                          "shot_count": len(generated), "aligned_to_storyboard": True},
        )
        db.add(task)
        db.add(MediaFile(
            name=track_path.name, category="audio", file_path=str(track_path),
            mime_type="audio/mpeg", file_size=track_path.stat().st_size,
        ))
        db.commit()
    except Exception as exc:
        track_stage.unlink(missing_ok=True)
        raise HTTPException(500, "对白语音合成失败；已生成的逐镜头配音素材予以保留：" + str(exc))
    return {
        "success": not failures, "episode_id": episode_id,
        "voice_track": str(track_path), "generated_shots": len(generated),
        "failures": failures,
        "message": f"已生成并按分镜时长合成对白语音轨，成功 {len(generated)} 个镜头，失败 {len(failures)} 个。",
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
            try:
                if data.response_format == "pcm":
                    if Path(prior.result_path).stat().st_size < 128:
                        raise ValueError("PCM 配音文件过小")
                else:
                    validate_rendered_audio(prior.result_path)
            except Exception:
                # Keep the bad artifact for diagnosis; do not advertise it as reusable.
                continue
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
        temporary_audio = destination.with_name("." + destination.stem + ".partial" + destination.suffix)
        with urllib.request.urlopen(request, timeout=300) as response:
            with temporary_audio.open("wb") as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
        if not temporary_audio.is_file() or temporary_audio.stat().st_size < 128:
            raise RuntimeError("语音服务返回的音频文件为空或无效")
        if data.response_format == "pcm":
            os.replace(temporary_audio, destination)
        else:
            finalize_rendered_audio(temporary_audio, destination)

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
        if 'temporary_audio' in locals():
            temporary_audio.unlink(missing_ok=True)
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
        if 'temporary_audio' in locals():
            temporary_audio.unlink(missing_ok=True)
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

    track = SubtitleTrack(name=data.name, file_path=str(destination), format="srt")
    db.add(track)
    db.commit()
    db.refresh(track)
    return {"id": track.id, "name": track.name, "path": track.file_path}


@router.get("/subtitles")
def list_subtitles(db: Session = Depends(get_db)):
    rows = db.query(SubtitleTrack).order_by(SubtitleTrack.id.desc()).all()
    return [{"id": x.id, "name": x.name, "path": x.file_path,
             "format": x.format} for x in rows]


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



@router.get("/quality-scan")
def scan_media_output_quality(path: str):
    """Run an opt-in FFmpeg scan for sustained black frames and silence."""
    try:
        file_path = safe_storage_file(path)
        return scan_media_quality(file_path)
    except Exception as exc:
        raise HTTPException(422, "媒体质量扫描失败：" + str(exc)[:1200]) from exc
