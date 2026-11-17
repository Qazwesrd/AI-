from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, JSON
from app.database import Base


class VisualAsset(Base):
    __tablename__ = "visual_assets"

    id = Column(Integer, primary_key=True, index=True)
    category = Column(String(40), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    description = Column(Text, default="")
    appearance = Column(Text, default="")
    clothing = Column(Text, default="")
    personality = Column(Text, default="")
    consistency_prompt = Column(Text, default="")
    image_path = Column(String(1000), default="")
    reference_path = Column(String(1000), default="")
    style = Column(String(100), default="3D动漫电影风格")
    data = Column(JSON, default=dict)
    confirmed = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ImageTask(Base):
    __tablename__ = "image_tasks"

    id = Column(Integer, primary_key=True, index=True)
    asset_id = Column(Integer, nullable=True, index=True)
    provider = Column(String(80), default="doubao")
    model = Column(String(200), default="")
    task_type = Column(String(80), default="character")
    prompt = Column(Text, nullable=False)
    size = Column(String(40), default="2K")
    status = Column(String(40), default="pending", index=True)
    result_path = Column(String(1000), default="")
    result_url = Column(String(2000), default="")
    error = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)
