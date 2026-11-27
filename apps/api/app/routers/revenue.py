from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.revenue import RevenueEntry, WithdrawalRecord

router = APIRouter(prefix="/api/revenue", tags=["收益与提现管理"])

REVENUE_STATUSES = {"estimated", "settled", "paid"}
WITHDRAWAL_STATUSES = {"planned", "submitted", "processing", "paid", "rejected", "cancelled"}
CURRENCIES = {"CNY", "USD", "EUR", "JPY", "GBP", "HKD", "KRW", "TWD"}


class RevenueInput(BaseModel):
    platform: str = Field(min_length=1, max_length=60)
    account_label: str = Field(default="", max_length=200)
    source_ref: str = Field(default="", max_length=200)
    period_start: str = Field(default="", max_length=20)
    period_end: str = Field(default="", max_length=20)
    gross_amount: float = Field(ge=0)
    currency: str = "CNY"
    fee_amount: float = Field(default=0, ge=0)
    tax_amount: float = Field(default=0, ge=0)
    status: str = "estimated"
    notes: str = Field(default="", max_length=5000)

    @model_validator(mode="after")
    def validate_values(self):
        if self.currency not in CURRENCIES:
            raise ValueError("不支持的币种")
        if self.status not in REVENUE_STATUSES:
            raise ValueError("无效的收入状态")
        if self.fee_amount + self.tax_amount > self.gross_amount:
            raise ValueError("手续费与税费不能超过收入金额")
        return self


class WithdrawalInput(BaseModel):
    platform: str = Field(min_length=1, max_length=60)
    account_label: str = Field(default="", max_length=200)
    amount: float = Field(gt=0)
    currency: str = "CNY"
    fee_amount: float = Field(default=0, ge=0)
    tax_amount: float = Field(default=0, ge=0)
    status: str = "planned"
    requested_at: str = Field(default="", max_length=40)
    arrived_at: str = Field(default="", max_length=40)
    receipt_reference: str = Field(default="", max_length=2000)
    notes: str = Field(default="", max_length=5000)

    @model_validator(mode="after")
    def validate_values(self):
        if self.currency not in CURRENCIES:
            raise ValueError("不支持的币种")
        if self.status not in WITHDRAWAL_STATUSES:
            raise ValueError("无效的提现状态")
        if self.fee_amount + self.tax_amount >= self.amount:
            raise ValueError("手续费与税费必须小于提现金额")
        if self.status == "paid" and not self.arrived_at:
            raise ValueError("标记为已到账时必须填写到账日期")
        return self


class WithdrawalStatusInput(BaseModel):
    status: str
    arrived_at: str = Field(default="", max_length=40)
    receipt_reference: str = Field(default="", max_length=2000)
    notes: str = Field(default="", max_length=5000)


def revenue_dict(row):
    return {
        "id": row.id, "platform": row.platform, "account_label": row.account_label,
        "source_ref": row.source_ref, "period_start": row.period_start,
        "period_end": row.period_end, "gross_amount": row.gross_amount,
        "currency": row.currency, "fee_amount": row.fee_amount,
        "tax_amount": row.tax_amount,
        "net_amount": round(row.gross_amount-row.fee_amount-row.tax_amount, 2),
        "status": row.status, "notes": row.notes,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def withdrawal_dict(row):
    return {
        "id": row.id, "platform": row.platform, "account_label": row.account_label,
        "amount": row.amount, "currency": row.currency, "fee_amount": row.fee_amount,
        "tax_amount": row.tax_amount,
        "net_amount": round(row.amount-row.fee_amount-row.tax_amount, 2),
        "status": row.status, "requested_at": row.requested_at,
        "arrived_at": row.arrived_at, "receipt_reference": row.receipt_reference,
        "notes": row.notes,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


@router.get("/summary")
def summary(db: Session = Depends(get_db)):
    revenues = db.query(RevenueEntry).all()
    withdrawals = db.query(WithdrawalRecord).all()
    totals = {}
    for row in revenues:
        item = totals.setdefault(row.currency, {"gross": 0.0, "fees": 0.0, "taxes": 0.0, "net": 0.0, "settled_net": 0.0})
        net = row.gross_amount - row.fee_amount - row.tax_amount
        item["gross"] += row.gross_amount
        item["fees"] += row.fee_amount
        item["taxes"] += row.tax_amount
        item["net"] += net
        if row.status in {"settled", "paid"}:
            item["settled_net"] += net
    withdrawn = {}
    for row in withdrawals:
        if row.status == "paid":
            withdrawn[row.currency] = withdrawn.get(row.currency, 0.0) + row.amount
    for currency, item in totals.items():
        item["gross"] = round(item["gross"], 2)
        item["fees"] = round(item["fees"], 2)
        item["taxes"] = round(item["taxes"], 2)
        item["net"] = round(item["net"], 2)
        item["settled_net"] = round(item["settled_net"], 2)
        item["withdrawn"] = round(withdrawn.get(currency, 0.0), 2)
        item["settled_balance_estimate"] = round(max(0, item["settled_net"]-item["withdrawn"]), 2)
    return {"currency_totals": totals, "revenue_count": len(revenues), "withdrawal_count": len(withdrawals),
            "disclaimer": "余额为根据手动登记数据计算的估算值，不是平台实时余额或官方可提现金额。"}


@router.get("/entries")
def list_revenue(status: Optional[str] = None, platform: Optional[str] = None, db: Session = Depends(get_db)):
    query = db.query(RevenueEntry)
    if status:
        if status not in REVENUE_STATUSES:
            raise HTTPException(400, "无效的收入状态")
        query = query.filter(RevenueEntry.status == status)
    if platform:
        query = query.filter(RevenueEntry.platform == platform)
    rows = query.order_by(RevenueEntry.created_at.desc()).limit(1000).all()
    return {"items": [revenue_dict(x) for x in rows], "total": len(rows)}


@router.post("/entries")
def create_revenue(data: RevenueInput, db: Session = Depends(get_db)):
    row = RevenueEntry(**data.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"success": True, "item": revenue_dict(row)}


@router.get("/withdrawals")
def list_withdrawals(status: Optional[str] = None, platform: Optional[str] = None, db: Session = Depends(get_db)):
    query = db.query(WithdrawalRecord)
    if status:
        if status not in WITHDRAWAL_STATUSES:
            raise HTTPException(400, "无效的提现状态")
        query = query.filter(WithdrawalRecord.status == status)
    if platform:
        query = query.filter(WithdrawalRecord.platform == platform)
    rows = query.order_by(WithdrawalRecord.created_at.desc()).limit(1000).all()
    return {"items": [withdrawal_dict(x) for x in rows], "total": len(rows)}


@router.post("/withdrawals")
def create_withdrawal(data: WithdrawalInput, db: Session = Depends(get_db)):
    row = WithdrawalRecord(**data.model_dump(), updated_at=datetime.utcnow())
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"success": True, "item": withdrawal_dict(row),
            "message": "已记录提现计划；请在平台官方账户自行提交提现，本软件不会发起资金操作。"}


@router.patch("/withdrawals/{withdrawal_id}")
def update_withdrawal(withdrawal_id: int, data: WithdrawalStatusInput, db: Session = Depends(get_db)):
    row = db.query(WithdrawalRecord).filter(WithdrawalRecord.id == withdrawal_id).first()
    if not row:
        raise HTTPException(404, "提现记录不存在")
    if data.status not in WITHDRAWAL_STATUSES:
        raise HTTPException(400, "无效的提现状态")
    if data.status == "paid" and not (data.arrived_at or row.arrived_at):
        raise HTTPException(400, "标记为已到账时必须填写到账日期")
    row.status = data.status
    if data.arrived_at:
        row.arrived_at = data.arrived_at
    if data.receipt_reference:
        row.receipt_reference = data.receipt_reference
    if data.notes:
        row.notes = data.notes
    row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return {"success": True, "item": withdrawal_dict(row)}


# 官方平台入口与接口能力边界。只有经官方文档确认并完成 OAuth 授权的执行适配器才允许提供软件内提现操作。
PLATFORM_CAPABILITIES = [
    {"platform": "YouTube", "official_portal": "https://studio.youtube.com/", "revenue_api": "官方只读收入分析 API（OAuth 授权；需频道/内容所有者权限）", "payout_api": False, "payout_label": "前往官方收益/AdSense 页面", "note": "Analytics monetary scope 可读取预估收入，不代表拥有提现执行 API。"},
    {"platform": "Facebook", "official_portal": "https://www.facebook.com/monetization/", "revenue_api": "部分页面/内容变现 Insights 指标需相应权限；金额可能为估算", "payout_api": False, "payout_label": "前往 Meta 官方变现页面", "note": "Insights 估算收入不等于最终付款金额。"},
    {"platform": "TikTok", "official_portal": "https://www.tiktok.com/creator-center", "revenue_api": "普通创作者账户通用收入 API 未确认；开发者组织小程序变现接口不等同创作者 API", "payout_api": False, "payout_label": "前往 TikTok 创作者中心", "note": "通过官方账户查看余额、结算与收款设置。"},
    {"platform": "抖音", "official_portal": "https://creator.douyin.com/", "revenue_api": "未确认通用个人创作者收入 API", "payout_api": False, "payout_label": "前往抖音创作者服务平台", "note": "软件不模拟登录或抓取私密账户页面。"},
    {"platform": "快手", "official_portal": "https://cp.kuaishou.com/", "revenue_api": "未接入官方个人创作者收入 API", "payout_api": False, "payout_label": "前往快手创作者服务平台", "note": "在官方账户内确认可提现余额和收款方式。"},
    {"platform": "哔哩哔哩", "official_portal": "https://member.bilibili.com/", "revenue_api": "未接入官方个人创作者收入 API", "payout_api": False, "payout_label": "前往哔哩哔哩创作中心", "note": "在官方账户内查看收益与提现。"},
    {"platform": "红果短剧", "official_portal": "", "revenue_api": "未找到可确认的通用公开个人收益 API", "payout_api": False, "payout_label": "请在红果 App 内查看收益/结算", "note": "未确认可公开验证的创作者网页入口；不提供猜测链接。请以 App 内官方结算规则为准。"},
]


@router.get("/platforms")
def list_platform_capabilities():
    return {
        "items": PLATFORM_CAPABILITIES,
        "global_notice": "当前没有已完成 OAuth 接入并经官方确认的提现执行适配器，因此不会显示软件内直接提现按钮；官方入口只负责跳转，资金操作仍在平台完成。",
    }
