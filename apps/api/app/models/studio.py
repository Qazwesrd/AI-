from datetime import datetime
from sqlalchemy import DateTime, Integer, String, Text, JSON, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base

class StudioResource(Base):
    __tablename__ = "studio_resources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    category: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(300), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    image_path: Mapped[str] = mapped_column(String(1000), default="")
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

class AIProviderConfig(Base):
    __tablename__ = "ai_provider_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(100))
    base_url: Mapped[str] = mapped_column(String(1000), default="")
    model: Mapped[str] = mapped_column(String(300), default="")
    encrypted_api_key: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    paid: Mapped[bool] = mapped_column(Boolean, default=True)
    purpose: Mapped[str] = mapped_column(String(50), default="text")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

class AIRuntimeSetting(Base):
    __tablename__ = "ai_runtime_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    network_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    paid_api_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

class AIUsageLog(Base):
    __tablename__ = "ai_usage_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(80))
    model: Mapped[str] = mapped_column(String(300))
    task: Mapped[str] = mapped_column(String(100), default="chat")
    status: Mapped[str] = mapped_column(String(40), default="success")
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class MediaAsset(Base):
    __tablename__ = "media_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    filename: Mapped[str] = mapped_column(String(500))
    stored_name: Mapped[str] = mapped_column(String(500), unique=True)
    category: Mapped[str] = mapped_column(String(50), index=True)
    mime_type: Mapped[str] = mapped_column(String(200), default="")
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    file_path: Mapped[str] = mapped_column(String(1500))
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
