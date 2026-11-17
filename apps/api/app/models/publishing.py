from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, JSON, ForeignKey
from app.database import Base


class PublishAccount(Base):
    __tablename__ = "publish_accounts"

    id = Column(Integer, primary_key=True, index=True)
    platform = Column(String(50), nullable=False, index=True)
    account_name = Column(String(200), default="")
    authorization_type = Column(String(30), default="manual")
    access_token_encrypted = Column(Text, default="")
    refresh_token_encrypted = Column(Text, default="")
    expires_at = Column(DateTime, nullable=True)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class PublishJob(Base):
    __tablename__ = "publish_jobs"

    id = Column(Integer, primary_key=True, index=True)
    platform = Column(String(50), nullable=False, index=True)
    account_id = Column(Integer, ForeignKey("publish_accounts.id"), nullable=True)
    media_file_id = Column(Integer, nullable=True)
    video_path = Column(Text, nullable=False)
    title = Column(String(200), default="")
    description = Column(Text, default="")
    tags = Column(JSON, default=list)
    cover_path = Column(Text, default="")
    scheduled_at = Column(DateTime, nullable=True)
    status = Column(String(30), default="pending", index=True)
    publish_url = Column(Text, default="")
    remote_id = Column(String(200), default="")
    error = Column(Text, default="")
    retry_count = Column(Integer, default=0)
    idempotency_key = Column(String(100), unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
