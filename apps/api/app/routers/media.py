import os
import uuid
import json
import re
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.media import MediaTask, MediaFile, SubtitleTrack
from app.models.story import EpisodePlan, StoryboardShot, StoryProject, StoryAssetLink
from app.models.visual import VisualAsset
from app.models.studio import AIProviderConfig, AIRuntimeSetting
from app.services.media_service import (
    ROOT, STORAGE, VIDEO_DIR, AUDIO_DIR, SUBTITLE_DIR,
    decrypt_api_key, http_json, http_download,
    safe_storage_file, run_ffmpeg, probe_media
)

router = APIRouter(prefix="/api/media", tags=["媒体生产"])


def get_provider(db, provider_name, confirm_paid=False):
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
    if paid:
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
    extra_payload: dict = Field(default_factory=dict)


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
        try:
            result = http_json(
                base + "/" + data.create_path.lstrip("/"),
                key, body=payload, timeout=300
            )
            remote_id = value_at(result, data.task_id_field)
            if not remote_id:
                raise RuntimeError("视频服务未返回任务编号，请检查任务 ID 返回字段配置")
            task = MediaTask(
                task_type="storyboard_video", provider=data.provider,
                model=data.model, status="submitted", prompt=prompt,
                remote_task_id=str(remote_id),
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
                },
                result_data=result,
            )
            db.add(task)
            db.commit()
            db.refresh(task)
            shot.status = "video_submitted"
            created.append({"task_id": task.id, "shot_id": shot.id,
                            "shot_number": shot.shot_number, "remote_task_id": str(remote_id)})
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
        if meta.get("episode_id") == episode_id and task.status not in ("completed", "failed"):
            matched.append(task)
    outcomes = []
    for task in matched:
        try:
            config, key, base = get_provider(
                db, task.provider, bool((task.request_data or {}).get("confirm_paid"))
            )
            meta = task.request_data or {}
            path = meta.get("status_path_template", "/videos/{task_id}").replace(
                "{task_id}", str(task.remote_task_id)
            )
            result = http_json(base + "/" + path.lstrip("/"), key, method="GET")
            status = str(value_at(result, meta.get("status_field", "status")) or "processing").lower()
            task.result_data = result
            task.status = status
            video_url = value_at(result, meta.get("video_url_field", "video_url"))
            if video_url:
                filename = f"episode_{episode_id}_shot_{meta.get('shot_id')}_{uuid.uuid4().hex[:8]}.mp4"
                destination = VIDEO_DIR / filename
                http_download(str(video_url), destination)
                task.result_path = str(destination)
                task.status = "completed"
                shot = db.query(StoryboardShot).filter(
                    StoryboardShot.id == meta.get("shot_id")
                ).first()
                if shot:
                    shot.status = "video_ready"
            db.commit()
            outcomes.append({"task_id": task.id, "shot_number": meta.get("shot_number"),
                             "status": task.status, "error": ""})
        except Exception as exc:
            task.error = str(exc)[:1500]
            db.commit()
            outcomes.append({"task_id": task.id, "shot_number": meta.get("shot_number"),
                             "status": task.status, "error": task.error})
    return {"episode_id": episode_id, "checked": len(outcomes), "tasks": outcomes}


@router.post("/video/episodes/{episode_id}/render")
def render_episode_from_shots(
    episode_id: int, output_name: str = "episode.mp4", db: Session = Depends(get_db)
):
    """将本集已下载的镜头统一规格后合成为初版成片，并生成对白字幕文件。"""
    episode = db.query(EpisodePlan).filter(EpisodePlan.id == episode_id).first()
    if not episode:
        raise HTTPException(404, "分集不存在")
    shots = db.query(StoryboardShot).filter(
        StoryboardShot.episode_id == episode_id
    ).order_by(StoryboardShot.sort_order, StoryboardShot.shot_number).all()
    if not shots:
        raise HTTPException(400, "本集没有分镜")
    tasks = db.query(MediaTask).filter(
        MediaTask.task_type == "storyboard_video"
    ).all()
    by_shot = {}
    for task in tasks:
        metadata = task.request_data if isinstance(task.request_data, dict) else {}
        if metadata.get("episode_id") == episode_id and metadata.get("shot_id"):
            by_shot[metadata["shot_id"]] = task
    clips = []
    missing = []
    for shot in shots:
        task = by_shot.get(shot.id)
        if not task or not task.result_path or not Path(task.result_path).is_file():
            missing.append(shot.shot_number)
        else:
            clips.append(safe_storage_file(task.result_path))
    if missing:
        raise HTTPException(409, "以下镜头视频尚未全部完成或下载：" + "、".join(map(str, missing)))
    output_name = re.sub(r"[^a-zA-Z0-9_-]", "_", output_name)
    if not output_name.lower().endswith(".mp4"):
        output_name += ".mp4"
    output = VIDEO_DIR / f"episode_{episode_id}_{uuid.uuid4().hex[:8]}_{output_name}"
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
        filters.append(
            "".join(concat_labels) + f"concat=n={len(clips)}:v=1:a=1[vout][aout]"
        )
        run_ffmpeg(ffmpeg_args + [
            "-filter_complex", ";".join(filters),
            "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-c:a", "aac", "-ar", "48000", "-movflags", "+faststart",
            str(output),
        ])
        # Mix an optional generated episode dialogue track with the original clip audio.
        speech_tasks = db.query(MediaTask).filter(
            MediaTask.task_type == "episode_speech_track"
        ).order_by(MediaTask.id.desc()).all()
        speech_path = None
        for speech_task in speech_tasks:
            speech_meta = speech_task.request_data if isinstance(speech_task.request_data, dict) else {}
            if speech_meta.get("episode_id") == episode_id and speech_task.result_path and Path(speech_task.result_path).is_file():
                speech_path = safe_storage_file(speech_task.result_path)
                break
        if speech_path:
            mixed_output = output.with_name(output.stem + "_mixed.mp4")
            run_ffmpeg([
                "-i", str(output), "-i", str(speech_path),
                "-filter_complex",
                "[0:a]volume=0.35[original];[1:a]volume=1.0[voice];[original][voice]amix=inputs=2:duration=first:dropout_transition=2[mix]",
                "-map", "0:v:0", "-map", "[mix]",
                "-c:v", "copy", "-c:a", "aac", "-shortest", str(mixed_output),
            ])
            output.unlink(missing_ok=True)
            os.replace(mixed_output, output)
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
            subtitle_file.write_text("\n".join(subtitle_lines), encoding="utf-8-sig")
            subtitle_path = str(subtitle_file)
            db.add(SubtitleTrack(name=f"第{episode.episode_number}集字幕", file_path=subtitle_path, format="srt"))
        db.add(MediaFile(
            name=output.name, category="video", file_path=str(output),
            mime_type="video/mp4", file_size=output.stat().st_size,
        ))
        episode.status = "rendered"
        db.commit()
        return {"success": True, "episode_id": episode_id,
                "output": str(output), "subtitle_path": subtitle_path,
                "size": output.stat().st_size,
                "clip_count": len(clips), "duration_target": episode.duration_seconds,
                "subtitle_count": subtitle_index - 1}
    except Exception as exc:
        output.unlink(missing_ok=True)
        raise HTTPException(500, "分集视频合成失败：" + str(exc))



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
    try:
        result = http_json(base + path, key, body=payload, timeout=300)
        remote_id = value_at(result, data.task_id_field)
        if not remote_id:
            raise RuntimeError(
                "视频服务商没有返回任务编号。请按该服务商文档配置请求路径和返回字段。"
            )

        task = MediaTask(
            task_type="video",
            provider=data.provider,
            model=data.model,
            status="submitted",
            prompt=data.prompt,
            remote_task_id=str(remote_id),
            request_data={
                "base_url": base,
                "status_path_template": data.status_path_template,
                "video_url_field": data.video_url_field,
                "status_field": data.status_field,
                "task_id_field": data.task_id_field,
                "confirm_paid": data.confirm_paid
            },
            result_data=result
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        return {"task_id": task.id, "remote_task_id": remote_id,
                "status": task.status, "provider_response": result}
    except Exception as exc:
        raise HTTPException(502, f"视频生成请求失败：{exc}")


@router.post("/video/tasks/{task_id}/refresh")
def refresh_video_task(task_id: int, db: Session = Depends(get_db)):
    task = db.query(MediaTask).filter(MediaTask.id == task_id).first()
    if not task or task.task_type not in ("video", "storyboard_video"):
        raise HTTPException(404, "视频任务不存在")
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
    response_format: str = "mp3"
    speed: float = Field(default=1.0, ge=0.25, le=4.0)
    confirm_paid: bool = False


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
    shots = db.query(StoryboardShot).filter(
        StoryboardShot.episode_id == episode_id
    ).order_by(StoryboardShot.sort_order, StoryboardShot.shot_number).all()
    if not shots:
        raise HTTPException(400, "本集没有分镜")
    config, key, base = get_provider(db, data.provider, data.confirm_paid)
    generated = {}
    prior_tasks = db.query(MediaTask).filter(
        MediaTask.task_type == "episode_speech"
    ).order_by(MediaTask.id.desc()).all()
    for prior in prior_tasks:
        prior_meta = prior.request_data if isinstance(prior.request_data, dict) else {}
        if prior.provider == data.provider and prior.model == data.model and prior_meta.get("voice") == data.voice and prior_meta.get("episode_id") == episode_id and prior_meta.get("shot_id") and prior.result_path and Path(prior.result_path).is_file():
            generated.setdefault(prior_meta["shot_id"], Path(prior.result_path))
    failures = []
    for shot in shots:
        if shot.id in generated:
            continue
        text_value = (shot.dialogue or "").strip()
        if not text_value:
            continue
        if len(text_value) > 4096:
            failures.append({"shot_number": shot.shot_number, "error": "单镜头对白超过4096字符，请先拆分对白"})
            continue
        filename = f"episode_{episode_id}_shot_{shot.id}_{uuid.uuid4().hex[:8]}.mp3"
        destination = AUDIO_DIR / filename
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
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                with destination.open("wb") as output:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
            if destination.stat().st_size < 128:
                raise RuntimeError("语音服务返回的音频文件为空或无效")
            generated[shot.id] = destination
            db.add(MediaTask(
                task_type="episode_speech", provider=data.provider, model=data.model,
                status="completed", prompt=text_value, result_path=str(destination),
                request_data={"episode_id": episode_id, "shot_id": shot.id,
                              "shot_number": shot.shot_number, "voice": data.voice},
            ))
            db.add(MediaFile(
                name=filename, category="audio", file_path=str(destination),
                mime_type="audio/mpeg", file_size=destination.stat().st_size,
            ))
            db.commit()
        except Exception as exc:
            destination.unlink(missing_ok=True)
            db.rollback()
            failures.append({"shot_number": shot.shot_number, "error": str(exc)[:1000]})

    if not generated:
        raise HTTPException(502, "本集没有成功生成任何对白语音：" + json.dumps(failures, ensure_ascii=False)[:2000])

    # 为没有对白的镜头补静音，并按分镜时长拼成与视频时间线一致的语音轨。
    ffmpeg_args = []
    filters = []
    labels = []
    input_index = 0
    for index, shot in enumerate(shots):
        audio_path = generated.get(shot.id)
        duration = max(1.0, float(shot.duration_seconds or 5))
        if audio_path:
            ffmpeg_args += ["-i", str(audio_path)]
        else:
            ffmpeg_args += [
                "-f", "lavfi", "-t", str(duration), "-i",
                "anullsrc=channel_layout=stereo:sample_rate=48000",
            ]
        filters.append(
            f"[{input_index}:a:0]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
            f"apad,atrim=duration={duration},asetpts=PTS-STARTPTS[a{index}]"
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
        task = MediaTask(
            task_type="episode_speech_track", provider=data.provider, model=data.model,
            status="completed", prompt=f"第{episode.episode_number}集对白配音",
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
        track_path.unlink(missing_ok=True)
        raise HTTPException(500, "对白语音合成失败：" + str(exc))
    return {
        "success": not failures, "episode_id": episode_id,
        "voice_track": str(track_path), "generated_shots": len(generated),
        "failures": failures,
        "message": f"已生成并按分镜时长合成对白语音轨，成功 {len(generated)} 个镜头，失败 {len(failures)} 个。",
    }


@router.post("/speech/generate")
def generate_speech(data: SpeechInput, db: Session = Depends(get_db)):
    config, key, base = get_provider(db, data.provider, data.confirm_paid)

    payload = {
        "model": data.model,
        "input": data.text,
        "voice": data.voice,
        "response_format": data.response_format,
        "speed": data.speed
    }
    if data.instructions:
        payload["instructions"] = data.instructions

    filename = f"speech_{uuid.uuid4().hex}.{data.response_format}"
    destination = AUDIO_DIR / filename
    import urllib.request
    import urllib.error

    request = urllib.request.Request(
        base + "/audio/speech",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json"
        },
        method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            with open(destination, "wb") as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)

        media = MediaFile(
            name=filename, category="audio", file_path=str(destination),
            mime_type="audio/" + data.response_format,
            file_size=destination.stat().st_size
        )
        task = MediaTask(
            task_type="speech", provider=data.provider, model=data.model,
            status="completed", prompt=data.text, result_path=str(destination)
        )
        db.add(media)
        db.add(task)
        db.commit()
        return {"status": "completed", "file": filename,
                "path": str(destination), "size": destination.stat().st_size}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise HTTPException(502, f"语音 API 返回 HTTP {exc.code}: {detail[:1500]}")
    except Exception as exc:
        raise HTTPException(502, f"语音生成失败：{exc}")


@router.post("/files/upload")
async def upload_media(file: UploadFile = File(...), category: str = "video",
                       db: Session = Depends(get_db)):
    allowed = {"video": VIDEO_DIR, "audio": AUDIO_DIR, "image": STORAGE / "images"}
    if category not in allowed:
        raise HTTPException(400, "不支持的素材类别")

    folder = allowed[category]
    folder.mkdir(parents=True, exist_ok=True)
    suffix = Path(file.filename or "upload.bin").suffix[:20]
    filename = f"{uuid.uuid4().hex}{suffix}"
    destination = folder / filename
    size = 0
    limit = 2 * 1024 * 1024 * 1024

    try:
        with open(destination, "wb") as output:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, "单个文件不能超过 2GB")
                output.write(chunk)

        media = MediaFile(
            name=file.filename or filename, category=category,
            file_path=str(destination), mime_type=file.content_type,
            file_size=size
        )
        db.add(media)
        db.commit()
        db.refresh(media)
        return {"id": media.id, "name": media.name, "category": category,
                "size": size, "path": media.file_path}
    except Exception:
        if destination.exists():
            destination.unlink()
        raise


@router.get("/files")
def list_media_files(category: Optional[str] = None, db: Session = Depends(get_db)):
    query = db.query(MediaFile)
    if category:
        query = query.filter(MediaFile.category == category)
    return [{
        "id": f.id, "name": f.name, "category": f.category,
        "path": f.file_path, "size": f.file_size,
        "mime_type": f.mime_type, "created_at": f.created_at
    } for f in query.order_by(MediaFile.id.desc()).limit(500).all()]


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
        return {"status": "completed", "output": str(output),
                "size": output.stat().st_size}
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
