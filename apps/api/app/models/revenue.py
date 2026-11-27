from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, Float, ForeignKey
from app.database import Base


class RevenueEntry(Base):
    __tablename__ = "revenue_entries"

    id = Column(Integer, primary_key=True, index=True)
    platform = Column(String(60), nullable=False, index=True)
    account_label = Column(String(200), default="")
    source_ref = Column(String(200), default="", index=True)
    period_start = Column(String(20), default="")
    period_end = Column(String(20), default="")
    gross_amount = Column(Float, nullable=False, default=0)
    currency = Column(String(12), nullable=False, default="CNY")
    fee_amount = Column(Float, nullable=False, default=0)
    tax_amount = Column(Float, nullable=False, default=0)
    status = Column(String(24), nullable=False, default="estimated", index=True)
    notes = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)


class WithdrawalRecord(Base):
    __tablename__ = "withdrawal_records"

    id = Column(Integer, primary_key=True, index=True)
    platform = Column(String(60), nullable=False, index=True)
    account_label = Column(String(200), default="")
    amount = Column(Float, nullable=False)
    currency = Column(String(12), nullable=False, default="CNY")
    fee_amount = Column(Float, nullable=False, default=0)
    tax_amount = Column(Float, nullable=False, default=0)
    status = Column(String(24), nullable=False, default="planned", index=True)
    requested_at = Column(String(40), default="")
    arrived_at = Column(String(40), default="")
    receipt_reference = Column(Text, default="")
    notes = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class CreatorOAuthConnection(Base):
    __tablename__ = "creator_oauth_connections"

    id = Column(Integer, primary_key=True, index=True)
    provider = Column(String(60), nullable=False, index=True)
    account_label = Column(String(200), default="")
    external_account_id = Column(String(200), default="", index=True)
    access_token_encrypted = Column(Text, nullable=False)
    refresh_token_encrypted = Column(Text, default="")
    token_expires_at = Column(DateTime, nullable=True)
    granted_scopes = Column(Text, default="")
    status = Column(String(30), nullable=False, default="connected", index=True)
    last_sync_at = Column(DateTime, nullable=True)
    last_sync_status = Column(String(30), default="never")
    last_sync_error = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class OAuthAuthorizationState(Base):
    __tablename__ = "oauth_authorization_states"

    id = Column(Integer, primary_key=True, index=True)
    provider = Column(String(60), nullable=False, index=True)
    state_hash = Column(String(128), nullable=False, unique=True, index=True)
    code_verifier = Column(Text, nullable=False)
    redirect_uri = Column(String(500), nullable=False, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
