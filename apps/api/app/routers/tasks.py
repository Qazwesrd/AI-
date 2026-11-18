from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.media import MediaTask
from app.models.novel import Novel, NovelAnalysisJob
from app.models.visual import ImageTask, VisualAsset
from app.models.task_runtime import TaskRuntimeControl, TaskExecutionLog, TaskExecutionLease
from app.services.task_runtime import runtime_snapshot, release_task_lease, queue_snapshot, remove_queued_task
from app.services.task_diagnostics import classify_task_error
from app.routers.visual import (
    pause_image_task,
    resume_image_task,
    retry_image_task,
    cancel_image_task,
)
from app.routers.media import refresh_video_task

router = APIRouter(prefix="/api/tasks", tags=["统一任务中心"])


class ConcurrencyInput(BaseModel):
    max_concurrency: int = Field(ge=1, le=8)


def _iso(value):
    return value.isoformat() if value else None


def is_known_remote_3d_task(task) -> bool:
    return (
        str(getattr(task, "task_type", "")).startswith("model3d_")
        and str(getattr(task, "result_url", "") or "").startswith("tripo-task:")
    )


def _analysis_chunk_payload(job):
    raw = dict(job.chunk_results or {})
    states = raw.pop("__chunk_states__", {})
    return raw, dict(states) if isinstance(states, dict) else {}


def _merge_analysis_chunk_payload(results, states):
    return {**dict(results or {}), "__chunk_states__": dict(states or {})}


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
    if value in {"submission_unknown", "awaiting_video_url", "submitting", "interrupted_unknown"}:
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






@router.delete("/queue/{source}/{task_id}")
def cancel_queued_task(source: str, task_id: int, db: Session = Depends(get_db)):
    if source not in {"image", "image3d_asset", "media", "analysis"}:
        raise HTTPException(400, "不支持的任务来源")
    if not remove_queued_task(db, source, task_id):
        raise HTTPException(404, "等待队列中不存在该任务")
    if source == "image":
        task = db.query(ImageTask).filter(ImageTask.id == task_id).first()
        if task and task.status == "pending":
            task.status = "cancelled"
            task.error = "用户从统一等待队列移除；未调用图像服务商"
            db.commit()
    elif source == "media":
        task = db.query(MediaTask).filter(MediaTask.id == task_id).first()
        if task and task.status == "queued":
            task.status = "cancelled"
            task.error = "用户从统一等待队列移除；未调用视频服务商"
            db.commit()
    return {"success": True, "message": "已从等待队列移除；不会调用 AI。"}


@router.get("/queue")
def get_task_queue(db: Session = Depends(get_db)):
    return queue_snapshot(db, 200)


@router.get("/runtime")
def get_task_runtime(db: Session = Depends(get_db)):
    return runtime_snapshot(db)


@router.put("/runtime/concurrency")
def set_task_concurrency(payload: ConcurrencyInput, db: Session = Depends(get_db)):
    control = db.query(TaskRuntimeControl).filter(TaskRuntimeControl.id == 1).first()
    if not control:
        control = TaskRuntimeControl(id=1, max_concurrency=payload.max_concurrency, active_count=0)
        db.add(control)
    else:
        if payload.max_concurrency < control.active_count:
            raise HTTPException(409, "新的并发上限不能低于当前正在执行的任务数")
        control.max_concurrency = payload.max_concurrency
        control.updated_at = datetime.utcnow()
    db.commit()
    return runtime_snapshot(db)





@router.post("/runtime/leases/{source}/{task_id}/release")
def manually_release_stale_lease(
    source: str,
    task_id: int,
    confirm_no_remote_job: bool = False,
    db: Session = Depends(get_db),
):
    if not confirm_no_remote_job:
        raise HTTPException(409, "必须先核对服务商控制台，并明确确认没有仍在运行的远程任务")
    if source == "image":
        task = db.query(ImageTask).filter(ImageTask.id == task_id).first()
        if not task:
            raise HTTPException(404, "图片任务不存在")
        if task.status == "running":
            raise HTTPException(409, "请先执行中断扫描，将任务标记为待核实；不能释放仍标记为运行中的租约")
    elif source == "image3d_asset":
        asset = db.query(VisualAsset).filter(VisualAsset.id == task_id).first()
        if not asset:
            raise HTTPException(404, "3D 素材不存在")
        task = db.query(ImageTask).filter(
            ImageTask.asset_id == task_id,
            ImageTask.task_type == "model3d_multiview",
        ).order_by(ImageTask.id.desc()).first()
        if task and task.status not in ("submitting", "interrupted_unknown"):
            raise HTTPException(409, "只有提交中断或结果不确定的 3D 任务可以人工核实释放")
        if task and (task.result_url or "").startswith("tripo-task:"):
            raise HTTPException(409, "该 3D 任务已有远程任务编号；请刷新远程状态，不能释放提交锁")
        asset_data = asset.data if isinstance(asset.data, dict) else {}
        model_task_data = asset_data.get("model3d_task") if isinstance(asset_data.get("model3d_task"), dict) else {}
        if model_task_data.get("remote_task_id"):
            raise HTTPException(409, "素材记录中已有远程 3D 任务编号；请刷新远程状态，不能释放提交锁")
    elif source == "analysis":
        task = db.query(NovelAnalysisJob).filter(NovelAnalysisJob.id == task_id).first()
        if not task:
            raise HTTPException(404, "小说分析任务不存在")
    elif source == "media":
        task = db.query(MediaTask).filter(MediaTask.id == task_id).first()
        if not task or task.task_type not in ("video", "storyboard_video", "speech", "episode_speech"):
            raise HTTPException(404, "媒体任务不存在")
        if task.remote_task_id:
            raise HTTPException(409, "该媒体任务已有服务商远程任务编号，不能释放提交租约")
        if task.status not in ("submission_unknown", "submitting"):
            raise HTTPException(409, "只有提交状态不确定的媒体任务可以人工核实释放")
    else:
        raise HTTPException(400, "当前仅支持人工核实后释放视觉图片、3D 模型提交、视频提交或小说解析任务租约")
    lease = db.query(TaskExecutionLease).filter(
        TaskExecutionLease.source == source,
        TaskExecutionLease.task_id == task_id,
        TaskExecutionLease.status == "active",
    ).first()
    if not lease:
        raise HTTPException(404, "没有找到活动执行租约")
    age = (datetime.utcnow() - (lease.heartbeat_at or lease.started_at)).total_seconds() if (lease.heartbeat_at or lease.started_at) else 0
    if age < 1800:
        raise HTTPException(409, "租约未超过30分钟保护期，暂不允许人工释放")
    if source == "analysis":
        if task.status not in ("running", "interrupted_unknown"):
            raise HTTPException(409, "只有运行中断或结果不确定的小说分析任务可以人工核实释放")
        saved, states = _analysis_chunk_payload(task)
        for key, state in list(states.items()):
            if state in ("submitting", "submission_unknown", "interrupted_unknown"):
                states[key] = "pending"
        if not states and task.completed_chunks < task.total_chunks:
            states[str(task.completed_chunks + 1)] = "pending"
        task.chunk_results = _merge_analysis_chunk_payload(saved, states)
        task.status = "partial"
        task.last_error = "用户已核对文本模型服务商记录并确认没有对应请求或待结算费用；可返回小说详情页手动续作。"
        db.commit()
    elif source == "media":
        if task.task_type in ("speech", "episode_speech"):
            task.status = "failed"
            task.error = "用户已核对语音服务商记录并确认没有远程任务或待结算请求；本地提交锁已释放，可返回配音页面重新生成。"
        else:
            task.status = "submission_unknown"
            task.error = "用户核对视频服务商后人工释放本地遗留租约；未自动重新提交，请先确认远程任务确实不存在。"
        db.commit()
    elif source == "image3d_asset":
        message = "用户已核对 Tripo 控制台并确认没有远程任务；提交锁已人工释放，可在素材卡片重新发起并再次确认费用。"
        if task:
            task.status = "failed"
            task.error = message
        asset_data = dict(asset.data) if isinstance(asset.data, dict) else {}
        model_task_data = dict(asset_data.get("model3d_task") or {})
        model_task_data["status"] = "failed"
        model_task_data["error"] = message
        model_task_data["manually_reconciled_at"] = datetime.utcnow().isoformat()
        asset_data["model3d_task"] = model_task_data
        asset.data = asset_data
        asset.updated_at = datetime.utcnow()
        db.commit()
    release_task_lease(db, source, task_id, False, "用户核对服务商后人工释放遗留租约")
    return {"success": True, "message": "遗留租约已释放；任务不会自动重试或调用 AI。请在所属模块确认后再手动执行。"}


@router.get("/runtime/logs")
def get_task_execution_logs(
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    rows = db.query(TaskExecutionLog).order_by(
        TaskExecutionLog.created_at.desc(), TaskExecutionLog.id.desc()
    ).limit(limit).all()
    return [{
        "id": row.id, "source": row.source, "task_id": row.task_id,
        "event": row.event, "message": row.message,
        "duration_seconds": row.duration_seconds,
        "created_at": _iso(row.created_at),
    } for row in rows]


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
            "speech": "独立配音",
            "episode_speech": "分镜对白配音",
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
            "error": (task.error or "")[:1200], "failure_diagnostic": classify_task_error(task.error), "result_available": bool(task.result_path),
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
            "error": (task.error or "")[:1200], "failure_diagnostic": classify_task_error(task.error), "result_available": bool(task.result_path),
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
            "error": (job.last_error or "")[:1200], "failure_diagnostic": classify_task_error(job.last_error), "result_available": False,
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


@router.post("/reconcile-stale")
def reconcile_stale_tasks(
    minutes: int = Query(default=60, ge=15, le=1440),
    db: Session = Depends(get_db),
):
    """仅将长时间无更新的执行中任务标记为待人工核实；绝不自动重试或再次调用 AI。"""
    cutoff = datetime.utcnow() - timedelta(minutes=minutes)
    image_tasks = db.query(ImageTask).filter(
        ImageTask.status.in_(("running", "submitting")),
        ImageTask.created_at < cutoff,
    ).all()
    media_tasks = db.query(MediaTask).filter(
        MediaTask.status == "submitting",
        MediaTask.updated_at < cutoff,
    ).all()
    analysis_jobs = db.query(NovelAnalysisJob).filter(
        NovelAnalysisJob.status == "running",
        NovelAnalysisJob.updated_at < cutoff,
    ).all()
    image_marked = 0
    analysis_marked = 0
    for task in image_tasks:
        # A 3D task with a persisted remote ID is known to have been accepted;
        # it must remain refreshable rather than being mislabeled as unknown.
        if is_known_remote_3d_task(task):
            continue
        task.status = "interrupted_unknown"
        task.error = (
            f"任务已运行超过 {minutes} 分钟，可能在应用关闭或网络中断时失去执行结果。"
            "请先核对图像服务商记录；确认未创建任务后，才能手动重新排队。"
        )
        task.finished_at = datetime.utcnow()
        image_marked += 1
    for task in media_tasks:
        task.status = "submission_unknown"
        task.error = (
            f"任务提交超过 {minutes} 分钟仍未确认。请先在服务商控制台核对，"
            "不得直接重复提交。"
        )
    for job in analysis_jobs:
        saved, states = _analysis_chunk_payload(job)
        for key, state in list(states.items()):
            if state == "submitting":
                states[key] = "submission_unknown"
        if not states and job.completed_chunks < job.total_chunks:
            states[str(job.completed_chunks + 1)] = "submission_unknown"
        job.chunk_results = _merge_analysis_chunk_payload(saved, states)
        if any(state in ("submission_unknown", "interrupted_unknown") for state in states.values()):
            job.status = "interrupted_unknown"
            job.last_error = f"小说分析任务超过 {minutes} 分钟未更新；请核对文本模型服务商用量记录，不能自动重试。"
        else:
            job.status = "partial"
            job.last_error = "小说分析任务在模型请求前中断，可安全续作。"
        analysis_marked += 1
    db.commit()
    return {
        "success": True,
        "image_marked_for_review": image_marked,
        "media_marked_for_review": len(media_tasks),
        "analysis_marked_for_review": analysis_marked,
        "message": "已标记需要人工核实的任务；没有自动重试、重新提交或产生新的 AI 调用。",
    }

@router.post("/{source}/{task_id}/{action}")
def control_unified_task(
    source: str,
    task_id: int,
    action: str,
    confirm_no_remote_job: bool = False,
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
        if action == "retry":
            task = db.query(ImageTask).filter(ImageTask.id == task_id).first()
            if task and task.status == "interrupted_unknown":
                if task.task_type.startswith("model3d_"):
                    raise HTTPException(409, "3D任务不能从不确定状态自动重试，请先核对服务商并回到素材卡片重新提交")
                if not confirm_no_remote_job:
                    raise HTTPException(409, "请先核对图像服务商记录；确认没有创建远程任务且不会重复扣费后，再明确确认重新排队")
                task.status = "pending"
                task.error = ""
                task.result_path = ""
                task.result_url = ""
                task.finished_at = None
                db.commit()
                return {"success": True, "task_id": task.id, "status": task.status,
                        "message": "已重新排队；不会自动调用 AI，请返回视觉素材模块手动执行。"}
        return handler(task_id, db)

    if source == "media":
        if action == "retry":
            task = db.query(MediaTask).filter(MediaTask.id == task_id).first()
            if not task or task.task_type not in ("speech", "episode_speech"):
                raise HTTPException(409, "只有独立配音或分镜对白配音任务支持人工核实后重新生成")
            if task.status not in ("submission_unknown", "interrupted_unknown", "submitting"):
                raise HTTPException(409, "只有提交结果不确定的配音任务需要人工核实")
            if not confirm_no_remote_job:
                raise HTTPException(409, "请先核对语音服务商控制台/用量记录，确认没有对应远程任务或待结算请求后，再确认重新生成")
            if task.remote_task_id:
                raise HTTPException(409, "该配音任务已有远程任务编号，不能作为未提交任务重新生成")
            lease = db.query(TaskExecutionLease).filter(
                TaskExecutionLease.source == "media",
                TaskExecutionLease.task_id == task_id,
                TaskExecutionLease.status == "active",
            ).first()
            if lease:
                age = (datetime.utcnow() - (lease.heartbeat_at or lease.started_at)).total_seconds() if (lease.heartbeat_at or lease.started_at) else 0
                if age < 1800:
                    raise HTTPException(409, "配音任务仍有活动执行租约；请等待任务结束或先扫描中断任务")
            task.status = "failed"
            task.error = "用户已核对语音服务商记录并确认没有远程任务或待结算请求；可返回配音页面重新生成。"
            db.commit()
            if lease:
                release_task_lease(db, "media", task_id, False, "用户核实后释放配音任务租约")
            return {
                "success": True, "task_id": task_id, "status": task.status,
                "message": "已完成配音任务人工核实并解除重试保护；请返回配音页面重新发起，系统不会自动调用 AI。",
            }
        if action != "refresh":
            raise HTTPException(
                409,
                "媒体任务暂不支持本地强制暂停或取消；视频请刷新远程状态，配音任务请走人工核实流程",
            )
        result = refresh_video_task(task_id, db)
        return {
            "task_id": task_id,
            "status": result.get("status", "unknown"),
            "result_available": bool(result.get("result_path")),
            "polling_skipped": bool(result.get("polling_skipped")),
            "error": result.get("error"),
        }

    if source == "analysis":
        if action != "retry":
            raise HTTPException(409, "小说解析任务只支持人工核实后续作，不支持远程暂停或取消")
        job = db.query(NovelAnalysisJob).filter(NovelAnalysisJob.id == task_id).first()
        if not job:
            raise HTTPException(404, "小说解析任务不存在")
        saved, states = _analysis_chunk_payload(job)
        unknown = [
            key for key, state in states.items()
            if state in ("submitting", "submission_unknown", "interrupted_unknown")
        ]
        if job.status != "interrupted_unknown" and not unknown:
            raise HTTPException(409, "该小说分析任务没有待核实的片段")
        if not confirm_no_remote_job:
            raise HTTPException(409, "请先核对文本模型服务商用量记录，确认没有对应请求或待结算费用后，再确认续作")
        lease = db.query(TaskExecutionLease).filter(
            TaskExecutionLease.source == "analysis",
            TaskExecutionLease.task_id == task_id,
            TaskExecutionLease.status == "active",
        ).first()
        if lease:
            age = (datetime.utcnow() - (lease.heartbeat_at or lease.started_at)).total_seconds() if (lease.heartbeat_at or lease.started_at) else 0
            if age < 1800:
                raise HTTPException(409, "小说分析任务仍有活动执行租约；请等待任务结束或先扫描中断任务")
        for key in unknown:
            states[key] = "pending"
        if not unknown and not states and job.completed_chunks < job.total_chunks:
            states[str(job.completed_chunks + 1)] = "pending"
        job.chunk_results = _merge_analysis_chunk_payload(saved, states)
        job.status = "partial"
        job.last_error = "用户已核对文本模型服务商用量记录并确认无对应请求或待结算费用；可返回小说详情页手动续作。"
        db.commit()
        if lease:
            release_task_lease(db, "analysis", task_id, False, "用户核实后释放小说分析任务租约")
        return {
            "success": True, "task_id": task_id, "status": job.status,
            "message": "已解除不确定片段的重试保护；没有自动调用 AI，请返回小说详情页手动继续分析。",
        }

    raise HTTPException(404, "不支持的任务来源")

