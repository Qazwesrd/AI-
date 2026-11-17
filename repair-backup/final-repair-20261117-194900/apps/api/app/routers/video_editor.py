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
from app.services.media_service import safe_storage_file

router = APIRouter(prefix="/api/video-editor", tags=["AI 视频剪辑"])


class CreateEditInput(BaseModel):
    title: str = "视频剪辑任务"
    provider: str
    model: str
    instruction: str = Field(min_length=2, max_length=12000)
    source_paths: list[str] = Field(min_length=1, max_length=30)
    confirm_paid: bool = False


class ExecuteEditInput(BaseModel):
    subtitle_path: Optional[str] = None
    audio_path: Optional[str] = None


@router.post("/jobs")
def create_edit_job(data: CreateEditInput, db: Session = Depends(get_db)):
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

        return {
            "id": job.id,
            "status": job.status,
            "plan": validated
        }
    except Exception as exc:
        db.rollback()
        if "job" in locals() and job is not None:
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
    rows = db.query(VideoEditJob).order_by(
        VideoEditJob.id.desc()
    ).limit(200).all()

    return [{
        "id": x.id,
        "title": x.title,
        "provider": x.provider,
        "model": x.model,
        "instruction": x.instruction,
        "status": x.status,
        "edit_plan": x.edit_plan,
        "output_path": x.output_path,
        "error": x.error,
        "created_at": x.created_at
    } for x in rows]


@router.get("/jobs/{job_id}")
def get_edit_job(job_id: int, db: Session = Depends(get_db)):
    job = db.query(VideoEditJob).filter(
        VideoEditJob.id == job_id
    ).first()
    if not job:
        raise HTTPException(404, "剪辑任务不存在")

    return {
        "id": job.id,
        "title": job.title,
        "status": job.status,
        "edit_plan": job.edit_plan,
        "output_path": job.output_path,
        "error": job.error
    }


@router.post("/jobs/{job_id}/execute")
def execute_job(job_id: int, data: ExecuteEditInput,
                db: Session = Depends(get_db)):
    job = db.query(VideoEditJob).filter(
        VideoEditJob.id == job_id
    ).first()
    if not job:
        raise HTTPException(404, "剪辑任务不存在")

    if job.status != "planned" or not job.edit_plan:
        raise HTTPException(400, "任务尚未生成有效剪辑方案")

    try:
        job.status = "rendering"
        db.commit()

        output, size = execute_edit_plan(
            job.edit_plan,
            subtitle_path=data.subtitle_path,
            audio_path=data.audio_path
        )

        job.output_path = output
        job.status = "completed"
        job.error = None

        media = MediaFile(
            name=Path(output).name,
            category="video",
            file_path=output,
            mime_type="video/mp4",
            file_size=size
        )
        db.add(media)
        db.commit()

        return {
            "id": job.id,
            "status": job.status,
            "output_path": output,
            "size": size
        }
    except Exception as exc:
        job.status = "failed"
        job.error = str(exc)
        db.commit()
        raise HTTPException(500, f"视频剪辑执行失败：{exc}")


@router.get("/health")
def editor_health():
    return {"video_editor": "ready", "engine": "FFmpeg"}
