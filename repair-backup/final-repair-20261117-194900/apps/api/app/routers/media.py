import os
import uuid
import json
import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.media import MediaTask, MediaFile, SubtitleTrack
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

    paid = True
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
    if not task or task.task_type != "video":
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
        MediaTask.task_type == "video"
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
