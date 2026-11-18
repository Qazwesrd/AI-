from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, Float, JSON, Boolean
from app.database import Base


class StoryProject(Base):
    __tablename__ = "story_projects"

    id = Column(Integer, primary_key=True, index=True)
    novel_id = Column(Integer, nullable=False, index=True)
    name = Column(String(200), nullable=False)
    adaptation_style = Column(String(100), default="3D动漫电影风格")
    target_duration = Column(Integer, default=120)
    min_duration = Column(Integer, default=90)
    max_duration = Column(Integer, default=150)
    status = Column(String(40), default="draft")
    settings = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class EpisodePlan(Base):
    __tablename__ = "episode_plans"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, nullable=False, index=True)
    episode_number = Column(Integer, nullable=False)
    title = Column(String(300), default="")
    source_chapter_ids = Column(JSON, default=list)
    source_text = Column(Text, default="")
    synopsis = Column(Text, default="")
    screenplay = Column(Text, default="")
    duration_seconds = Column(Integer, default=120)
    status = Column(String(40), default="planned")
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class StoryboardShot(Base):
    __tablename__ = "storyboard_shots"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, nullable=False, index=True)
    episode_id = Column(Integer, nullable=False, index=True)
    shot_number = Column(Integer, default=1)
    scene_name = Column(String(300), default="")
    shot_type = Column(String(100), default="中景")
    camera_movement = Column(String(100), default="固定镜头")
    duration_seconds = Column(Float, default=5)
    visual_description = Column(Text, default="")
    character_actions = Column(Text, default="")
    dialogue = Column(Text, default="")
    sound_effects = Column(Text, default="")
    image_prompt = Column(Text, default="")
    character_ids = Column(JSON, default=list)
    scene_asset_id = Column(Integer, nullable=True)
    prop_ids = Column(JSON, default=list)
    status = Column(String(40), default="draft")
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class StoryAssetLink(Base):
    __tablename__ = "story_asset_links"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, nullable=False, index=True)
    episode_id = Column(Integer, nullable=True, index=True)
    shot_id = Column(Integer, nullable=True, index=True)
    asset_id = Column(Integer, nullable=False, index=True)
    role = Column(String(40), default="character")
    chapter_number = Column(Integer, nullable=True, index=True)
    state_snapshot = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
