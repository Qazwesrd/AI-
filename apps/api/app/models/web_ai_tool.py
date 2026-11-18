from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class WebAITool(Base):
    __tablename__ = "web_ai_tools"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    tool_key: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    category: Mapped[str] = mapped_column(String(32), index=True)
    url: Mapped[str] = mapped_column(String(2048))
    note: Mapped[str] = mapped_column(Text, default="")
    is_custom: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class WebAITask(Base):
    __tablename__ = "web_ai_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    task_key: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(240))
    category: Mapped[str] = mapped_column(String(32), index=True)
    tool_key: Mapped[str] = mapped_column(String(120), index=True)
    status: Mapped[str] = mapped_column(String(32), default="prepared", index=True)
    prompt: Mapped[str] = mapped_column(Text)
    source_text: Mapped[str] = mapped_column(Text, default="")
    result_text: Mapped[str] = mapped_column(Text, default="")
    result_url: Mapped[str] = mapped_column(String(2048), default="")
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    novel_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    chapter_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    character_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    scene_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class WebAIResultFile(Base):
    __tablename__ = "web_ai_result_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    task_key: Mapped[str] = mapped_column(String(120), index=True)
    original_name: Mapped[str] = mapped_column(String(255))
    stored_name: Mapped[str] = mapped_column(String(80), unique=True)
    content_type: Mapped[str] = mapped_column(String(160), default="application/octet-stream")
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
