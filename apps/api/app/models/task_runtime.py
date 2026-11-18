from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Text, UniqueConstraint
from app.database import Base


class TaskRuntimeControl(Base):
    __tablename__ = "task_runtime_controls"

    id = Column(Integer, primary_key=True)
    max_concurrency = Column(Integer, nullable=False, default=2)
    active_count = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class TaskExecutionLease(Base):
    __tablename__ = "task_execution_leases"
    __table_args__ = (UniqueConstraint("source", "task_id", name="uq_task_execution_source_id"),)

    id = Column(Integer, primary_key=True)
    source = Column(String(30), nullable=False)
    task_id = Column(Integer, nullable=False)
    status = Column(String(30), nullable=False, default="active", index=True)
    priority = Column(Integer, nullable=False, default=0)
    owner_token = Column(String(64), nullable=False)
    started_at = Column(DateTime, default=datetime.utcnow)
    heartbeat_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)
    error = Column(Text, default="")


class TaskExecutionLog(Base):
    __tablename__ = "task_execution_logs"

    id = Column(Integer, primary_key=True)
    source = Column(String(30), nullable=False, index=True)
    task_id = Column(Integer, nullable=False, index=True)
    event = Column(String(40), nullable=False)
    message = Column(Text, default="")
    duration_seconds = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class TaskQueueEntry(Base):
    __tablename__ = "task_queue_entries"
    __table_args__ = (UniqueConstraint("source", "task_id", name="uq_task_queue_source_id"),)

    id = Column(Integer, primary_key=True)
    source = Column(String(30), nullable=False, index=True)
    task_id = Column(Integer, nullable=False)
    priority = Column(Integer, nullable=False, default=0)
    status = Column(String(30), nullable=False, default="waiting", index=True)
    queued_at = Column(DateTime, default=datetime.utcnow, index=True)
    ready_at = Column(DateTime, nullable=True)
    reason = Column(Text, default="")
