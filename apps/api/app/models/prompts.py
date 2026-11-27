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
    is_system = Column(Boolean, default=False)
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


class TrendEvidence(Base):
    __tablename__ = "trend_evidence"

    id = Column(Integer, primary_key=True, index=True)
    platform = Column(String(100), nullable=False, index=True)
    work_title = Column(String(300), nullable=False)
    work_url = Column(String(2000), default="")
    observed_at = Column(String(40), nullable=False, index=True)
    metric_summary = Column(JSON, nullable=False, default=dict)
    evidence_type = Column(String(100), default="公开榜单/公开页面")
    reliability = Column(String(30), default="待核验")
    verification_note = Column(Text, default="")
    is_verified = Column(Boolean, default=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class PromptUpgradeCandidate(Base):
    __tablename__ = "prompt_upgrade_candidates"

    id = Column(Integer, primary_key=True, index=True)
    prompt_id = Column(Integer, ForeignKey("prompt_templates.id"), nullable=False, index=True)
    base_version = Column(Integer, nullable=False)
    proposed_name = Column(String(200), nullable=False)
    proposed_content = Column(Text, nullable=False)
    change_reason = Column(Text, nullable=False, default="")
    expected_impact = Column(Text, default="")
    evidence_ids = Column(JSON, nullable=False, default=list)
    status = Column(String(20), nullable=False, default="pending", index=True)
    decision_note = Column(Text, default="")
    decided_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class PublicResearchSource(Base):
    __tablename__ = "public_research_sources"

    id = Column(Integer, primary_key=True, index=True)
    platform = Column(String(100), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    feed_url = Column(String(2000), nullable=False)
    source_type = Column(String(20), nullable=False, default="rss")
    enabled = Column(Boolean, nullable=False, default=True, index=True)
    refresh_interval_minutes = Column(Integer, nullable=False, default=360)
    last_collected_at = Column(DateTime, nullable=True)
    last_status = Column(String(30), nullable=False, default="待采集")
    last_error = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)


class PublicResearchCollectionRun(Base):
    __tablename__ = "public_research_collection_runs"

    id = Column(Integer, primary_key=True, index=True)
    source_id = Column(Integer, ForeignKey("public_research_sources.id"), nullable=False, index=True)
    status = Column(String(30), nullable=False)
    discovered_count = Column(Integer, nullable=False, default=0)
    inserted_count = Column(Integer, nullable=False, default=0)
    duplicate_count = Column(Integer, nullable=False, default=0)
    error_message = Column(Text, default="")
    started_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)
