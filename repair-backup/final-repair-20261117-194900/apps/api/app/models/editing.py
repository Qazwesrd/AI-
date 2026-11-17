from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, JSON
from app.database import Base


class VideoEditJob(Base):
    __tablename__ = "video_edit_jobs"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(300), default="视频剪辑任务")
    provider = Column(String(80), nullable=False)
    model = Column(String(160), nullable=False)
    instruction = Column(Text, nullable=False)
    status = Column(String(40), default="created", index=True)
    source_files = Column(JSON, nullable=True)
    edit_plan = Column(JSON, nullable=True)
    output_path = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
