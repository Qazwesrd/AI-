import json
import os
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.editing import VideoEditJob
from app.models.media import MediaFile
from app.services.editing_service import (
    call_ai_for_edit_plan, validate_plan, execute_edit_plan
)
from app.services.media_service import safe_storage_file, probe_media

router = APIRouter(prefix="/api/video-editor", tags=["AI 视频剪辑"])


class CreateEditInput(BaseModel):
    title: str = "视频剪辑任务"
    provider: str
    model: str
    instruction: str = Field(min_length=2, max_length=12000)
    source_paths: list[str] = Field(min_length=1, max_length=30)
    confirm_paid: bool = False


class WebHandoffInput(BaseModel):
    title: str = "网页版 AI 视频剪辑任务"
    instruction: str = Field(min_length=2, max_length=12000)
    source_paths: list[str] = Field(min_length=1, max_length=30)
    confirm_selected_videos: bool = False


class WebPlanImportInput(BaseModel):
    plan: dict


class ExecuteEditInput(BaseModel):
    subtitle_path: Optional[str] = None
    audio_path: Optional[str] = None


def _get_job(db: Session, job_id: int):
    job = db.query(VideoEditJob).filter(VideoEditJob.id == job_id).first()
    if not job:
        raise HTTPException(404, "剪辑任务不存在")
    return job


def _web_file_manifest(paths: list[str]):
    manifest = []
    for index, raw in enumerate(paths, 1):
        path = safe_storage_file(raw)
        info = probe_media(path)
        fmt = info.get("format") or {}
        streams = info.get("streams") or []
        video = next((x for x in streams if x.get("codec_type") == "video"), {})
        manifest.append({
            "file_key": f"video_{index}",
            "file_name": path.name,
            "duration_seconds": round(float(fmt.get("duration") or 0), 3),
            "width": video.get("width"),
            "height": video.get("height"),
            "size_bytes": path.stat().st_size,
        })
    return manifest


@router.post("/web-handoff")
def create_web_handoff(data: WebHandoffInput, db: Session = Depends(get_db)):
    if not data.confirm_selected_videos:
        raise HTTPException(400, "请先确认本次交给网页版 AI 分析的视频文件")
    try:
        sources = [str(safe_storage_file(raw)) for raw in data.source_paths]
        manifest = _web_file_manifest(sources)
        job = VideoEditJob(
            title=data.title,
            provider="web_ai",
            model="用户选择的网页版 AI",
            instruction=data.instruction,
            status="awaiting_web_ai",
            source_files=sources,
        )
        db.add(job)
        db.commit()
        db.refresh(job)

        schema = {
            "type": "object",
            "required": ["title", "summary", "segments", "width", "height"],
            "properties": {
                "title": {"type": "string"},
                "summary": {"type": "string"},
                "segments": {"type": "array", "items": {
                    "type": "object",
                    "required": ["file_key", "start", "end", "description"],
                    "properties": {
                        "file_key": {"type": "string", "enum": [x["file_key"] for x in manifest]},
                        "start": {"type": "number", "minimum": 0},
                        "end": {"type": "number", "exclusiveMinimum": 0},
                        "description": {"type": "string"},
                    },
                }},
                "width": {"type": "integer"},
                "height": {"type": "integer"},
                "subtitle_text": {"type": "string"},
                "notes": {"type": "array", "items": {"type": "string"}},
            },
        }
        prompt = (
            "你是专业影视剪辑师。请根据用户的剪辑要求和视频素材信息，生成可执行的剪辑方案。"
            "注意：你目前只收到文件元数据，没有看到视频画面；除非用户另外在网页端上传并让你查看视频，"
            "否则不得编造人物、场景、动作或镜头内容。无法判断的画面内容必须写入 notes，"
            "并提示用户补充视频画面/字幕/转写文本。"
            "片段只能引用 manifest 中的 file_key，时间必须在对应视频时长范围内。"
            "按用户要求排序片段；只返回符合 schema 的 JSON，不要 Markdown。\n\n"
            + "用户剪辑要求：\n" + data.instruction
            + "\n\n视频素材元数据：\n"
            + json.dumps(manifest, ensure_ascii=False, indent=2)
            + "\n\n输出 JSON Schema：\n"
            + json.dumps(schema, ensure_ascii=False, indent=2)
        )
        return {
            "id": job.id,
            "status": job.status,
            "title": job.title,
            "manifest": manifest,
            "prompt": prompt,
            "output_schema": schema,
            "user_review_required": True,
            "video_bytes_transferred": False,
            "third_party_transfer_started": False,
            "notice": "本交接包仅包含文件名、时长、分辨率和剪辑要求，不会上传视频。若要让网页版 AI 理解画面，请由你自行在该网页上传视频；完成后将 JSON 结果粘贴回来。",
        }
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(422, f"无法准备网页版 AI 交接包：{exc}")


@router.post("/jobs/{job_id}/web-result")
def import_web_result(job_id: int, data: WebPlanImportInput, db: Session = Depends(get_db)):
    job = _get_job(db, job_id)
    if job.status != "awaiting_web_ai":
        raise HTTPException(400, "该任务当前不在等待网页版 AI 结果的状态")
    sources = [str(safe_storage_file(x)) for x in (job.source_files or [])]
    key_to_path = {f"video_{index}": path for index, path in enumerate(sources, 1)}
    plan = data.plan
    segments = plan.get("segments")
    if not isinstance(segments, list) or not segments:
        raise HTTPException(422, "AI 结果缺少有效的 segments 数组")
    mapped = []
    for index, segment in enumerate(segments, 1):
        if not isinstance(segment, dict):
            raise HTTPException(422, f"第 {index} 个片段格式错误")
        file_key = segment.get("file_key")
        if file_key not in key_to_path:
            raise HTTPException(422, f"第 {index} 个片段引用了未授权的视频文件")
        mapped.append({
            "file_path": key_to_path[file_key],
            "start": segment.get("start"),
            "end": segment.get("end"),
            "description": segment.get("description", ""),
        })
    normalized = {**plan, "segments": mapped}
    try:
        validated = validate_plan(normalized, sources)
    except Exception as exc:
        raise HTTPException(422, f"网页版 AI 剪辑方案未通过校验：{exc}")
    job.edit_plan = validated
    job.title = validated["title"] or job.title
    job.status = "planned"
    job.error = None
    db.commit()
    return {"id": job.id, "status": job.status, "plan": validated, "message": "网页版 AI 方案已校验并保存；请检查分段后再执行剪辑。"}


@router.post("/jobs")
def create_edit_job(data: CreateEditInput, db: Session = Depends(get_db)):
    job = None
    try:
        sources = []
        for raw in data.source_paths:
            path = safe_storage_file(raw)
            sources.append(str(path))

        job = VideoEditJob(
            title=data.title,
            provider=data.provider,
            model=data.model,
            instruction=data.instruction,
            status="analyzing",
            source_files=sources
        )
        db.add(job)
        db.commit()
        db.refresh(job)

        plan, raw_response = call_ai_for_edit_plan(
            db, data.provider, data.model,
            data.instruction, sources, data.confirm_paid
        )
        validated = validate_plan(plan, sources)

        job.edit_plan = validated
        job.title = validated["title"] or data.title
        job.status = "planned"
        db.commit()

        return {"id": job.id, "status": job.status, "plan": validated}
    except Exception as exc:
        db.rollback()
        if job is not None:
            try:
                job.status = "failed"
                job.error = str(exc)[:4000]
                db.add(job)
                db.commit()
            except Exception:
                db.rollback()
        raise HTTPException(502, f"AI 剪辑方案生成失败：{exc}")


@router.get("/jobs")
def list_edit_jobs(db: Session = Depends(get_db)):
    rows = db.query(VideoEditJob).order_by(VideoEditJob.id.desc()).limit(200).all()
    return [{
        "id": x.id, "title": x.title, "provider": x.provider, "model": x.model,
        "instruction": x.instruction, "status": x.status, "edit_plan": x.edit_plan,
        "output_path": x.output_path, "error": x.error, "created_at": x.created_at
    } for x in rows]


@router.get("/jobs/{job_id}")
def get_edit_job(job_id: int, db: Session = Depends(get_db)):
    job = _get_job(db, job_id)
    return {
        "id": job.id, "title": job.title, "status": job.status,
        "edit_plan": job.edit_plan, "output_path": job.output_path, "error": job.error
    }


@router.post("/jobs/{job_id}/execute")
def execute_job(job_id: int, data: ExecuteEditInput, db: Session = Depends(get_db)):
    job = _get_job(db, job_id)
    if job.status != "planned" or not job.edit_plan:
        raise HTTPException(400, "任务尚未生成有效剪辑方案")
    try:
        job.status = "rendering"
        db.commit()
        output, size = execute_edit_plan(
            job.edit_plan, subtitle_path=data.subtitle_path, audio_path=data.audio_path
        )
        job.output_path = output
        job.status = "completed"
        job.error = None
        media = MediaFile(
            name=Path(output).name, category="video", file_path=output,
            mime_type="video/mp4", file_size=size
        )
        db.add(media)
        db.commit()
        return {"id": job.id, "status": job.status, "output_path": output, "size": size}
    except Exception as exc:
        job.status = "failed"
        job.error = str(exc)
        db.commit()
        raise HTTPException(500, f"视频剪辑执行失败：{exc}")


@router.get("/health")
def editor_health():
    return {"video_editor": "ready", "engine": "FFmpeg", "web_ai_handoff": "ready"}
