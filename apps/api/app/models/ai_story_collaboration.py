from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, Float, JSON
from app.database import Base


class AIStoryProposal(Base):
    __tablename__ = "ai_story_proposals"

    id = Column(Integer, primary_key=True, index=True)
    novel_id = Column(Integer, nullable=False, index=True)
    chapter_number = Column(Integer, nullable=False, index=True)
    proposal_type = Column(String(40), nullable=False, default="asset_evolution")
    asset_name = Column(String(200), nullable=False)
    asset_category = Column(String(40), nullable=False, default="characters")
    change_type = Column(String(80), default="状态变化")
    event = Column(Text, default="")
    appearance_after = Column(Text, default="")
    clothing_after = Column(Text, default="")
    holder = Column(String(300), default="")
    evidence = Column(Text, nullable=False)
    confidence = Column(Float, default=0.5)
    status = Column(String(30), default="pending_review", index=True)
    review_note = Column(Text, default="")
    applied_asset_id = Column(Integer, nullable=True, index=True)
    source_task_key = Column(String(120), default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    reviewed_at = Column(DateTime, nullable=True)
