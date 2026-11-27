from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, Float, JSON, ForeignKey
from app.database import Base


class PromptCategory(Base):
    __tablename__ = "prompt_categories"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, unique=True)
    description = Column(String(500), default="")
    is_system = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class PromptTemplate(Base):
    __tablename__ = "prompt_templates"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    category_id = Column(Integer, ForeignKey("prompt_categories.id"), nullable=True, index=True)
    content = Column(Text, nullable=False, default="")
    notes = Column(Text, default="")
    tags = Column(JSON, default=list)
    current_version = Column(Integer, default=1)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class PromptVersion(Base):
    __tablename__ = "prompt_versions"

    id = Column(Integer, primary_key=True, index=True)
    prompt_id = Column(Integer, ForeignKey("prompt_templates.id"), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    name = Column(String(200), nullable=False)
    content = Column(Text, nullable=False)
    notes = Column(Text, default="")
    change_reason = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)


class PromptTestRun(Base):
    __tablename__ = "prompt_test_runs"

    id = Column(Integer, primary_key=True, index=True)
    prompt_id = Column(Integer, ForeignKey("prompt_templates.id"), nullable=False, index=True)
    prompt_version = Column(Integer, nullable=False)
    dataset_name = Column(String(200), default="手动实测")
    model_name = Column(String(200), default="未注明")
    score_overall = Column(Float, nullable=False, default=0)
    score_story = Column(Float, nullable=False, default=0)
    score_consistency = Column(Float, nullable=False, default=0)
    score_executability = Column(Float, nullable=False, default=0)
    score_visual = Column(Float, nullable=False, default=0)
    notes = Column(Text, default="")
    evaluator = Column(String(100), default="用户")
    created_at = Column(DateTime, default=datetime.utcnow)


class PromptRankingSnapshot(Base):
    __tablename__ = "prompt_ranking_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    dimension = Column(String(50), nullable=False, default="overall")
    ranking_data = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime, default=datetime.utcnow)
