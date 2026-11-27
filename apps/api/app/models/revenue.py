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
