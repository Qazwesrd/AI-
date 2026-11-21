from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, Float, JSON

from app.database import Base


class MediaTask(Base):
    __tablename__ = "media_tasks"

    id = Column(Integer, primary_key=True, index=True)
    task_type = Column(String(40), nullable=False, index=True)
    provider = Column(String(80), nullable=True)
    model = Column(String(160), nullable=True)
    status = Column(String(40), default="queued", index=True)
    prompt = Column(Text, nullable=True)
    remote_task_id = Column(String(300), nullable=True)
    request_data = Column(JSON, nullable=True)
    result_data = Column(JSON, nullable=True)
    result_path = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class MediaFile(Base):
    __tablename__ = "media_files"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(300), nullable=False)
    category = Column(String(40), nullable=False, index=True)
    file_path = Column(Text, nullable=False)
    mime_type = Column(String(160), nullable=True)
    file_size = Column(Integer, default=0)
    duration = Column(Float, nullable=True)
    metadata_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class SubtitleTrack(Base):
    __tablename__ = "subtitle_tracks"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(300), nullable=False)
    file_path = Column(Text, nullable=False)
    format = Column(String(20), default="srt")
    language = Column(String(30), default="zh-CN")
    project_id = Column(Integer, nullable=True)
    episode_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
