from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.media import MediaTask
from app.models.novel import Novel, NovelAnalysisJob
from app.models.visual import ImageTask
from app.routers.visual import (
    pause_image_task,
    resume_image_task,
    retry_image_task,
    cancel_image_task,
)
from app.routers.media import refresh_video_task

router = APIRouter(prefix="/api/tasks", tags=["统一任务中心"])


def _iso(value):
    return value.isoformat() if value else None


def _status_group(status: str) -> str:
    value = (status or "unknown").lower()
    if value in {"completed", "success", "succeeded", "finished", "done"}:
        return "completed"
    if value in {"failed", "error", "rejected"}:
        return "failed"
    if value in {"cancelled", "canceled"}:
        return "cancelled"
    if value in {"paused"}:
        return "paused"
    if value in {"submission_unknown", "awaiting_video_url", "submitting"}:
        return "attention"
    if value in {"pending", "queued", "submitted", "processing", "running"}:
        return "active"
    if value == "partial":
        return "partial"
    return "other"


def _group_counts(db: Session, model, status_column):
    rows = db.query(status_column, func.count(model.id)).group_by(status_column).all()
    result = {"total": 0, "active": 0, "completed": 0, "failed": 0,
              "cancelled": 0, "paused": 0, "partial": 0, "attention": 0, "other": 0}
    for status, count in rows:
        group = _status_group(status)
        result[group] += int(count)
        result["total"] += int(count)
    return result


@router.get("")
def list_unified_tasks(
    task_type: Optional[str] = Query(default=None, max_length=60),
    status: Optional[str] = Query(default=None, max_length=40),
    q: Optional[str] = Query(default=None, max_length=120),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """跨小说解析、视觉生成、视频/音频任务的只读聚合视图；不修改任务状态。"""
    media_rows = db.query(MediaTask).order_by(MediaTask.updated_at.desc(), MediaTask.id.desc()).limit(500).all()
    image_rows = db.query(ImageTask).order_by(ImageTask.created_at.desc(), ImageTask.id.desc()).limit(500).all()
    analysis_rows = (
        db.query(NovelAnalysisJob, Novel.title)
        .outerjoin(Novel, Novel.id == NovelAnalysisJob.novel_id)
        .order_by(NovelAnalysisJob.updated_at.desc(), NovelAnalysisJob.id.desc())
        .limit(500)
        .all()
    )

    items = []
    for task in media_rows:
        meta = task.request_data if isinstance(task.request_data, dict) else {}
        kind = task.task_type or "media"
        title_map = {
            "storyboard_video": "分镜视频生成",
            "video": "独立视频生成",
            "episode_speech": "分集配音",
        }
        items.append({
            "id": f"media:{task.id}", "source": "media", "source_id": task.id,
            "type": kind, "title": title_map.get(kind, "媒体任务"),
            "status": task.status or "unknown", "status_group": _status_group(task.status),
            "provider": task.provider or "", "model": task.model or "",
            "summary": (task.prompt or "")[:180],
            "context": f"第 {meta.get('shot_number')} 镜" if meta.get("shot_number") else (
                f"分集 #{meta.get('episode_id')}" if meta.get("episode_id") else ""
            ),
            "error": (task.error or "")[:1200], "result_available": bool(task.result_path),
            "created_at": _iso(task.created_at), "updated_at": _iso(task.updated_at),
            "can_open": True,
        })

    for task in image_rows:
        items.append({
            "id": f"image:{task.id}", "source": "image", "source_id": task.id,
            "type": task.task_type or "image", "title": "视觉素材生成",
            "status": task.status or "unknown", "status_group": _status_group(task.status),
            "provider": task.provider or "", "model": task.model or "",
            "summary": (task.prompt or "")[:180],
            "context": f"素材 #{task.asset_id}" if task.asset_id else "",
            "error": (task.error or "")[:1200], "result_available": bool(task.result_path),
            "created_at": _iso(task.created_at), "updated_at": _iso(task.finished_at or task.created_at),
            "can_open": True,
        })

    for job, novel_title in analysis_rows:
        items.append({
            "id": f"analysis:{job.id}", "source": "analysis", "source_id": job.id,
            "type": "novel_analysis", "title": "小说深度解析",
            "status": job.status or "unknown", "status_group": _status_group(job.status),
            "provider": job.provider or "", "model": job.model or "",
            "summary": f"已完成 {job.completed_chunks}/{job.total_chunks} 个分析批次",
            "context": novel_title or f"小说 #{job.novel_id}",
            "error": (job.last_error or "")[:1200], "result_available": False,
            "progress": round(job.completed_chunks * 100 / job.total_chunks) if job.total_chunks else 0,
            "completed_chunks": job.completed_chunks, "total_chunks": job.total_chunks,
            "created_at": _iso(job.created_at), "updated_at": _iso(job.updated_at),
            "can_open": True,
        })

    if task_type:
        items = [item for item in items if item["type"] == task_type or item["source"] == task_type]
    if status:
        status_value = status.lower()
        items = [item for item in items if item["status"].lower() == status_value or item["status_group"] == status_value]
    if q:
        needle = q.casefold()
        items = [item for item in items if needle in " ".join(str(item.get(k) or "") for k in ("title", "summary", "context", "provider", "model", "error")).casefold()]

    items.sort(key=lambda item: item["updated_at"] or item["created_at"] or "", reverse=True)
    total_filtered = len(items)
    page = items[offset:offset + limit]

    media_counts = _group_counts(db, MediaTask, MediaTask.status)
    image_counts = _group_counts(db, ImageTask, ImageTask.status)
    analysis_counts = _group_counts(db, NovelAnalysisJob, NovelAnalysisJob.status)
    totals = {key: media_counts[key] + image_counts[key] + analysis_counts[key] for key in media_counts}

    return {
        "items": page,
        "total": total_filtered,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(page) < total_filtered,
        "counts": totals,
        "by_source": {
            "media": media_counts,
            "image": image_counts,
            "analysis": analysis_counts,
        },
        "generated_at": datetime.utcnow().isoformat(),
        "note": "任务中心为跨模块只读汇总；各模块原有的暂停、恢复、重试、轮询和付费确认规则保持不变。",
    }

@router.post("/{source}/{task_id}/{action}")
def control_unified_task(
    source: str,
    task_id: int,
    action: str,
    db: Session = Depends(get_db),
):
    """统一任务中心的显式人工操作入口；不自动执行 AI 生成。"""
    if source == "image":
        handlers = {
            "pause": pause_image_task,
            "resume": resume_image_task,
            "retry": retry_image_task,
            "cancel": cancel_image_task,
        }
        handler = handlers.get(action)
        if not handler:
            raise HTTPException(400, "视觉任务仅支持暂停、恢复、重试或取消")
        return handler(task_id, db)

    if source == "media":
        if action != "refresh":
            raise HTTPException(
                409,
                "媒体任务暂不支持本地强制暂停、取消或自动重试；请使用所属模块的安全控制流程",
            )
        return refresh_video_task(task_id, db)

    if source == "analysis":
        raise HTTPException(
            409,
            "小说解析任务请在小说详情页继续或重试，以保留原有模型、服务商和付费确认设置",
        )

    raise HTTPException(404, "不支持的任务来源")

