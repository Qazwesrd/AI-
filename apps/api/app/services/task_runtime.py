import uuid
from datetime import datetime
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException

from app.models.task_runtime import TaskRuntimeControl, TaskExecutionLease, TaskExecutionLog, TaskQueueEntry


def acquire_task_lease(db, source: str, task_id: int, priority: int = 0):
    control = db.query(TaskRuntimeControl).filter(TaskRuntimeControl.id == 1).first()
    if not control:
        control = TaskRuntimeControl(id=1, max_concurrency=2, active_count=0)
        db.add(control)
        db.commit()
        db.refresh(control)

    active_existing = db.query(TaskExecutionLease).filter(
        TaskExecutionLease.source == source,
        TaskExecutionLease.task_id == task_id,
        TaskExecutionLease.status == "active",
    ).first()
    if active_existing:
        raise HTTPException(409, "该任务已有执行租约，已阻止重复执行")

    queued = db.query(TaskQueueEntry).filter(
        TaskQueueEntry.source == source, TaskQueueEntry.task_id == task_id
    ).first()
    _refresh_queue_ready(db)
    ready_head = db.query(TaskQueueEntry).filter(
        TaskQueueEntry.status == "ready_to_run"
    ).order_by(TaskQueueEntry.priority.desc(), TaskQueueEntry.queued_at.asc(), TaskQueueEntry.id.asc()).first()
    control = db.query(TaskRuntimeControl).filter(TaskRuntimeControl.id == 1).first()
    if control.active_count >= control.max_concurrency or (ready_head and (not queued or ready_head.id != queued.id)):
        queued_info = enqueue_task(db, source, task_id, priority, "等待全局并发名额；不会自动提交 AI 请求")
        raise HTTPException(429, f"任务已进入持久化等待队列，第 {queued_info['position']} 位；名额空出后请回所属模块手动执行")
    updated = db.query(TaskRuntimeControl).filter(
        TaskRuntimeControl.id == 1,
        TaskRuntimeControl.active_count < TaskRuntimeControl.max_concurrency,
    ).update(
        {TaskRuntimeControl.active_count: TaskRuntimeControl.active_count + 1},
        synchronize_session=False,
    )
    if not updated:
        queued_info = enqueue_task(db, source, task_id, priority, "等待全局并发名额")
        raise HTTPException(429, f"任务已进入持久化等待队列，第 {queued_info['position']} 位")

    existing = db.query(TaskExecutionLease).filter(
        TaskExecutionLease.source == source, TaskExecutionLease.task_id == task_id
    ).first()
    if existing and existing.status == "active":
        db.rollback()
        raise HTTPException(409, "该任务已有执行租约，已阻止重复执行")
    token = uuid.uuid4().hex
    now = datetime.utcnow()
    if existing:
        existing.status = "active"
        existing.priority = max(-100, min(100, int(priority)))
        existing.owner_token = token
        existing.started_at = now
        existing.heartbeat_at = now
        existing.finished_at = None
        existing.error = ""
    else:
        db.add(TaskExecutionLease(
            source=source, task_id=task_id, status="active",
            priority=max(-100, min(100, int(priority))), owner_token=token,
            started_at=now, heartbeat_at=now,
        ))
    if queued:\n        db.delete(queued)\n    db.add(TaskExecutionLog(source=source, task_id=task_id, event="started", message="用户手动启动任务"))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "该任务已有执行租约，已阻止重复执行")
    return db.query(TaskExecutionLease).filter(
        TaskExecutionLease.source == source, TaskExecutionLease.task_id == task_id
    ).first()


def release_task_lease(db, source: str, task_id: int, success: bool, error: str = ""):
    lease = db.query(TaskExecutionLease).filter(
        TaskExecutionLease.source == source,
        TaskExecutionLease.task_id == task_id,
        TaskExecutionLease.status == "active",
    ).first()
    if not lease:
        return
    now = datetime.utcnow()
    duration = max(0, int((now - lease.started_at).total_seconds())) if lease.started_at else None
    lease.status = "completed" if success else "failed"
    lease.heartbeat_at = now
    lease.finished_at = now
    lease.error = (error or "")[:2000]
    db.query(TaskRuntimeControl).filter(TaskRuntimeControl.id == 1).update({
        TaskRuntimeControl.active_count: func.max(0, TaskRuntimeControl.active_count - 1),
        TaskRuntimeControl.updated_at: now,
    }, synchronize_session=False)
    db.add(TaskExecutionLog(
        source=source, task_id=task_id,
        event="completed" if success else "failed",
        message=("执行完成" if success else (error or "执行失败"))[:2000],
        duration_seconds=duration,
    ))
    db.commit()
    _refresh_queue_ready(db)


def heartbeat_task_lease(db, source: str, task_id: int):
    now = datetime.utcnow()
    lease = db.query(TaskExecutionLease).filter(
        TaskExecutionLease.source == source,
        TaskExecutionLease.task_id == task_id,
        TaskExecutionLease.status == "active",
    ).first()
    if not lease:
        return False
    lease.heartbeat_at = now
    db.commit()
    return True


def runtime_snapshot(db):
    control = db.query(TaskRuntimeControl).filter(TaskRuntimeControl.id == 1).first()
    if not control:
        return {"max_concurrency": 2, "active_count": 0, "available_slots": 2, "leases": [], "stale_leases": []}
    leases = db.query(TaskExecutionLease).filter(TaskExecutionLease.status == "active").order_by(
        TaskExecutionLease.priority.desc(), TaskExecutionLease.started_at.asc()
    ).all()
    now = datetime.utcnow()
    items = [{
        "source": row.source, "task_id": row.task_id, "priority": row.priority,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "heartbeat_at": row.heartbeat_at.isoformat() if row.heartbeat_at else None,
        "age_seconds": max(0, int((now - (row.heartbeat_at or row.started_at)).total_seconds())) if (row.heartbeat_at or row.started_at) else 0,
    } for row in leases]
    return {
        "max_concurrency": control.max_concurrency,
        "active_count": control.active_count,
        "available_slots": max(0, control.max_concurrency - control.active_count),
        "leases": items,
        "stale_leases": [item for item in items if item["age_seconds"] >= 1800],\n        "queue": queue_snapshot(db, 100),
        "note": "超过30分钟的租约仅标记为待核实，不自动释放，避免远程AI任务仍在运行时重复提交。",
    }



def inspect_startup_leases():
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        snapshot = runtime_snapshot(db)
        return {
            "active_count": snapshot["active_count"],
            "stale_count": len(snapshot.get("stale_leases") or []),
        }
    finally:
        db.close()
