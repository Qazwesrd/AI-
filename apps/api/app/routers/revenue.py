import hashlib
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse, HTMLResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.revenue import (
    RevenueEntry, WithdrawalRecord, CreatorOAuthConnection, OAuthAuthorizationState,
)
from app.services.google_creator_oauth import (
    OAuthConfigurationError, OAuthProviderError, decrypt_token, encrypt_token,
    exchange_code, get_email, google_get_json, new_authorization,
    parse_money, refresh_access_token, token_expiry, save_google_client_id, OAUTH_KEY_FILE, GOOGLE_CONFIG_FILE,
)

router = APIRouter(prefix="/api/revenue", tags=["收益与提现管理"])

REVENUE_STATUSES = {"estimated", "settled", "payable", "paid"}
WITHDRAWAL_STATUSES = {"planned", "submitted", "processing", "paid", "rejected", "cancelled"}
CURRENCIES = {"CNY", "USD", "EUR", "JPY", "GBP", "HKD", "KRW", "TWD", "CAD", "AUD", "INR", "SGD", "CHF", "NZD"}


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
        item = totals.setdefault(row.currency, {"gross": 0.0, "fees": 0.0, "taxes": 0.0, "net": 0.0, "settled_net": 0.0, "payable_net": 0.0, "paid_net": 0.0})
        net = row.gross_amount - row.fee_amount - row.tax_amount
        item["gross"] += row.gross_amount
        item["fees"] += row.fee_amount
        item["taxes"] += row.tax_amount
        item["net"] += net
        if row.status == "settled":
            item["settled_net"] += net
        elif row.status == "payable":
            item["payable_net"] += net
        elif row.status == "paid":
            item["paid_net"] += net
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
        item["payable_net"] = round(item["payable_net"], 2)
        item["paid_net"] = round(item["paid_net"], 2)
        item["withdrawn"] = round(withdrawn.get(currency, 0.0), 2)
        item["settled_balance_estimate"] = round(max(0, item["settled_net"] + item["payable_net"]-item["withdrawn"]), 2)
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
    {"platform": "番茄小说", "official_portal": "https://fanqienovel.com/", "revenue_api": "未确认开放给本软件的个人作者收益 API", "payout_api": False, "payout_label": "前往番茄官方作者入口", "note": "作者后台查看作品数据、稿费与提现；请以作者端实时规则为准。"},
    {"platform": "七猫中文网", "official_portal": "https://zuozhe.qimao.com/front/register-login/login?query=hasNavBar%3Dtrue&redirect=%2Ffront%2Findex", "revenue_api": "未确认开放给本软件的个人作者收益 API", "payout_api": False, "payout_label": "前往七猫作者中心", "note": "签约、作品授权、稿费和提现受作者协议及账号资格约束。"},
    {"platform": "晋江文学城", "official_portal": "https://www.jjwxc.net/", "revenue_api": "未确认开放给本软件的个人作者收益 API", "payout_api": False, "payout_label": "前往晋江官方作者入口", "note": "作者账务、签约和提现以作者后台实际信息及合同为准。"},
    {"platform": "起点中文网", "official_portal": "https://www.qidian.com/", "revenue_api": "未确认开放给本软件的个人作者收益 API", "payout_api": False, "payout_label": "前往起点官方入口", "note": "请登录官方作家专区查看稿费、结算与提现。"},
    {"platform": "Amazon KDP", "official_portal": "https://kdp.amazon.com/", "revenue_api": "未确认可用于本软件的公开作者版税 API", "payout_api": False, "payout_label": "前往 KDP 官方账户", "note": "作者需在 KDP 官方账户完成税务和收款设置并查看版税付款记录。"},
    {"platform": "Royal Road", "official_portal": "https://www.royalroad.com/author-dashboard/submissions", "revenue_api": "未确认开放的通用作者收入 API", "payout_api": False, "payout_label": "前往 Royal Road 作者后台", "note": "平台支持作者提交作品；具体变现和收款资格以官方账户为准。"},
    {"platform": "Inkitt", "official_portal": "https://www.inkitt.com/writers", "revenue_api": "未确认开放的通用作者收入 API", "payout_api": False, "payout_label": "前往 Inkitt 作者入口", "note": "出版合同与收入取决于平台项目筛选和签约，不保证所有作品获得付费资格。"},
]


@router.get("/platforms")
def list_platform_capabilities():
    return {
        "items": PLATFORM_CAPABILITIES,
        "global_notice": "Google 已支持官方只读 OAuth 收入与付款记录同步；其他平台按官方接口能力逐步接入。当前没有经官方确认且账户权限可用的资金发起/提现执行适配器，因此不会显示软件内直接提现按钮；实际资金操作仍在平台官方账户完成。",
    }


# Google is the first real OAuth provider. This is read-only reporting, not a payout API.
GOOGLE_PROVIDER = "google_youtube_adsense"
GOOGLE_ANALYTICS_SCOPE = "https://www.googleapis.com/auth/yt-analytics-monetary.readonly"


def _connection_dict(row):
    return {
        "id": row.id, "provider": row.provider, "account_label": row.account_label,
        "external_account_id": row.external_account_id, "status": row.status,
        "granted_scopes": [scope for scope in (row.granted_scopes or "").split() if scope],
        "token_expires_at": row.token_expires_at.isoformat() if row.token_expires_at else None,
        "last_sync_at": row.last_sync_at.isoformat() if row.last_sync_at else None,
        "last_sync_status": row.last_sync_status,
        "last_sync_error": row.last_sync_error or "",
    }


def _google_access_token(connection, db):
    refresh = decrypt_token(connection.refresh_token_encrypted)
    access = decrypt_token(connection.access_token_encrypted)
    if connection.token_expires_at and connection.token_expires_at <= datetime.utcnow() + timedelta(seconds=30):
        if not refresh:
            connection.status = "reauthorization_required"
            db.commit()
            raise HTTPException(409, "Google 授权已过期且没有可用刷新令牌，请重新授权。")
        try:
            token_data = refresh_access_token(refresh)
        except OAuthProviderError as exc:
            connection.status = "reauthorization_required"
            connection.last_sync_error = str(exc)[:1000]
            db.commit()
            raise HTTPException(502, str(exc))
        access = token_data.get("access_token", "")
        connection.access_token_encrypted = encrypt_token(access)
        if token_data.get("refresh_token"):
            connection.refresh_token_encrypted = encrypt_token(token_data["refresh_token"])
        connection.token_expires_at = token_expiry(token_data)
        connection.status = "connected"
        db.commit()
    if not access:
        raise HTTPException(409, "Google 授权令牌缺失，请重新授权。")
    return access


@router.get("/oauth/google/start")
def google_oauth_start(db: Session = Depends(get_db)):
    try:
        url, state, verifier = new_authorization()
    except OAuthConfigurationError as exc:
        raise HTTPException(503, str(exc))
    db.query(OAuthAuthorizationState).filter(
        OAuthAuthorizationState.created_at < datetime.utcnow() - timedelta(minutes=15)
    ).delete(synchronize_session=False)
    db.add(OAuthAuthorizationState(
        provider=GOOGLE_PROVIDER,
        state_hash=hashlib.sha256(state.encode("utf-8")).hexdigest(),
        code_verifier=verifier,
    ))
    db.commit()
    return RedirectResponse(url, status_code=302)


@router.get("/oauth/google/callback", response_class=HTMLResponse)
def google_oauth_callback(
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    db: Session = Depends(get_db),
):
    if error:
        return HTMLResponse("<h2>Google 授权未完成</h2><p>你取消了授权或 Google 返回错误。可以关闭此窗口并返回软件。</p>", status_code=400)
    if not code or not state:
        raise HTTPException(400, "OAuth 回调缺少 code 或 state")
    state_hash = hashlib.sha256(state.encode("utf-8")).hexdigest()
    saved = db.query(OAuthAuthorizationState).filter(
        OAuthAuthorizationState.provider == GOOGLE_PROVIDER,
        OAuthAuthorizationState.state_hash == state_hash,
        OAuthAuthorizationState.created_at >= datetime.utcnow() - timedelta(minutes=10),
    ).first()
    if not saved:
        raise HTTPException(400, "OAuth state 无效或已过期，请重新开始授权。")
    verifier = saved.code_verifier
    db.delete(saved)
    db.commit()
    try:
        token_data = exchange_code(code, verifier)
        access_token = token_data.get("access_token", "")
        if not access_token:
            raise OAuthProviderError("Google 未返回 access_token")
        email = get_email(access_token)
        refresh_token = token_data.get("refresh_token", "")
        scopes = token_data.get("scope", "")
        existing = db.query(CreatorOAuthConnection).filter(
            CreatorOAuthConnection.provider == GOOGLE_PROVIDER,
            CreatorOAuthConnection.external_account_id == email,
        ).first()
        values = {
            "account_label": email,
            "access_token_encrypted": encrypt_token(access_token),
            "granted_scopes": scopes,
            "token_expires_at": token_expiry(token_data),
            "status": "connected",
            "last_sync_error": "",
            "updated_at": datetime.utcnow(),
        }
        if refresh_token:
            values["refresh_token_encrypted"] = encrypt_token(refresh_token)
        if existing:
            for key, value in values.items():
                setattr(existing, key, value)
        else:
            existing = CreatorOAuthConnection(
                provider=GOOGLE_PROVIDER,
                external_account_id=email,
                refresh_token_encrypted=values.get("refresh_token_encrypted", ""),
                **{key: value for key, value in values.items() if key != "refresh_token_encrypted"},
            )
            db.add(existing)
        db.commit()
    except (OAuthProviderError, OAuthConfigurationError) as exc:
        raise HTTPException(502, str(exc))
    return HTMLResponse(
        "<html><meta charset='utf-8'><body style='font-family:sans-serif;background:#10101a;color:#fff;padding:40px'>"
        "<h2>Google 账户已完成授权</h2><p>只授予了请求的只读数据权限。可以关闭此窗口，返回 AI 漫剧制作大师收益中心。</p>"
        "<p><a style='color:#8de5ff' href='http://127.0.0.1:3000/revenue'>返回收益中心</a></p></body></html>"
    )


@router.get("/oauth/connections")
def list_oauth_connections(db: Session = Depends(get_db)):
    rows = db.query(CreatorOAuthConnection).order_by(CreatorOAuthConnection.created_at.desc()).all()
    return {"items": [_connection_dict(row) for row in rows],
            "notice": "只返回授权状态和账户标识；访问令牌、刷新令牌永不通过 API 返回。"}


@router.delete("/oauth/connections/{connection_id}")
def revoke_oauth_connection(connection_id: int, db: Session = Depends(get_db)):
    row = db.query(CreatorOAuthConnection).filter(CreatorOAuthConnection.id == connection_id).first()
    if not row:
        raise HTTPException(404, "授权账户不存在")
    try:
        token = decrypt_token(row.refresh_token_encrypted) or decrypt_token(row.access_token_encrypted)
        if token:
            request = urllib.request.Request(
                "https://oauth2.googleapis.com/revoke",
                data=urllib.parse.urlencode({"token": token}).encode("utf-8"),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=15):
                    pass
            except Exception:
                # Local deletion still removes the app's ability to use the token.
                pass
    except OAuthConfigurationError as exc:
        raise HTTPException(503, str(exc))
    db.delete(row)
    db.commit()
    return {"success": True, "message": "本软件已删除该账户授权令牌；如需确保撤销，可同时在 Google 账户安全设置中移除应用授权。"}


def _upsert_synced_revenue(db, *, platform, account_label, source_ref, period_start, period_end,
                           amount, currency, status, notes):
    if amount is None or not currency:
        return False
    existing = db.query(RevenueEntry).filter(
        RevenueEntry.platform == platform,
        RevenueEntry.source_ref == source_ref,
    ).first()
    if existing:
        existing.gross_amount = max(0, float(amount))
        existing.currency = currency
        existing.status = status
        existing.period_start = period_start
        existing.period_end = period_end
        existing.notes = notes[:5000]
        return False
    db.add(RevenueEntry(
        platform=platform, account_label=account_label, source_ref=source_ref,
        period_start=period_start, period_end=period_end,
        gross_amount=max(0, float(amount)), currency=currency,
        fee_amount=0, tax_amount=0, status=status, notes=notes[:5000],
    ))
    return True


@router.post("/oauth/connections/{connection_id}/sync")
def sync_google_creator_revenue(connection_id: int, db: Session = Depends(get_db)):
    connection = db.query(CreatorOAuthConnection).filter(
        CreatorOAuthConnection.id == connection_id,
        CreatorOAuthConnection.provider == GOOGLE_PROVIDER,
    ).first()
    if not connection:
        raise HTTPException(404, "Google 授权账户不存在")
    try:
        access = _google_access_token(connection, db)
        today = datetime.utcnow().date()
        end_day = today - timedelta(days=1)
        start_day = end_day - timedelta(days=29)
        query = urllib.parse.urlencode({
            "ids": "channel==MINE",
            "startDate": start_day.isoformat(),
            "endDate": end_day.isoformat(),
            "metrics": "estimatedRevenue",
            "dimensions": "day",
            "sort": "day",
            "currency": "USD",
        })
        analytics = google_get_json("https://youtubeanalytics.googleapis.com/v2/reports?" + query, access)
        inserted = 0
        updated = 0
        for values in analytics.get("rows", []):
            if len(values) < 2:
                continue
            day = str(values[0])
            amount = float(values[1] or 0)
            existed = db.query(RevenueEntry.id).filter(
                RevenueEntry.platform == "YouTube",
                RevenueEntry.source_ref == f"youtube-analytics:{connection.id}:{day}",
            ).first()
            created = _upsert_synced_revenue(
                db, platform="YouTube", account_label=connection.account_label,
                source_ref=f"youtube-analytics:{connection.id}:{day}",
                period_start=day, period_end=day, amount=amount, currency="USD",
                status="estimated",
                notes="Google YouTube Analytics API · estimatedRevenue · USD · 只读同步；不是最终结算或已付款金额。",
            )
            inserted += int(created)
            updated += int(bool(existed))

        # AdSense Payments is the authoritative API source for unpaid/paid payment records.
        payment_count = 0
        skipped_currency_count = 0
        adsense_warning = ""
        try:
            adsense_accounts = google_get_json("https://adsense.googleapis.com/v2/accounts?pageSize=100", access)
        except OAuthProviderError as exc:
            adsense_accounts = {"accounts": []}
            adsense_warning = str(exc)[:1000]
        for account in adsense_accounts.get("accounts", []):
            account_name = str(account.get("name") or "")
            account_id = account_name.rsplit("/", 1)[-1]
            if not account_id:
                continue
            try:
                payments = google_get_json(
                    f"https://adsense.googleapis.com/v2/accounts/{urllib.parse.quote(account_id, safe='')}/payments",
                    access,
                )
            except OAuthProviderError as exc:
                adsense_warning = str(exc)[:1000]
                continue
            for payment in payments.get("payments", []):
                payment_name = str(payment.get("name") or "")
                amount, currency = parse_money(payment.get("amount", ""), account.get("currencyCode"))
                if amount is None or not currency:
                    skipped_currency_count += 1
                    continue
                is_unpaid = payment_name.endswith("/unpaid") or payment_name.endswith("/youtube-unpaid")
                payment_date = str((payment.get("date") or {}).get("year", "")) + "-" + str((payment.get("date") or {}).get("month", "")).zfill(2) + "-" + str((payment.get("date") or {}).get("day", "")).zfill(2)
                status = "payable" if is_unpaid else "paid"
                ref = f"adsense-payment:{connection.id}:{payment_name}"
                existed = db.query(RevenueEntry.id).filter(
                    RevenueEntry.platform == "AdSense/YouTube付款",
                    RevenueEntry.source_ref == ref,
                ).first()
                created = _upsert_synced_revenue(
                    db, platform="AdSense/YouTube付款", account_label=connection.account_label,
                    source_ref=ref, period_start=payment_date if not is_unpaid else "",
                    period_end=payment_date if not is_unpaid else "",
                    amount=amount, currency=currency, status=status,
                    notes="Google AdSense Management API accounts.payments.list · " + ("未付款/当前应付款记录" if is_unpaid else "平台付款记录") + "；软件只读取状态，不执行付款。",
                )
                inserted += int(created)
                updated += int(bool(existed))
                payment_count += 1
        connection.last_sync_at = datetime.utcnow()
        connection.last_sync_status = "success"
        connection.last_sync_error = ""
        connection.status = "connected"
        db.commit()
        return {
            "success": True, "account": connection.account_label,
            "analytics_days": len(analytics.get("rows", [])),
            "payment_records": payment_count, "skipped_currency_count": skipped_currency_count, "inserted": inserted, "updated": updated,
            "adsense_warning": adsense_warning,
            "period": {"start": start_day.isoformat(), "end": end_day.isoformat()},
            "message": "已同步 YouTube 预估收入；" + ("AdSense 付款记录同步遇到权限或账户限制：" + adsense_warning if adsense_warning else "已读取 Google AdSense 付款记录。") + "预估收入、应付款与已付款分开保存；无法可靠识别币种的付款金额会跳过并计数，避免猜测；此同步不提供提现执行。",
        }
    except HTTPException:
        raise
    except (OAuthProviderError, OAuthConfigurationError, ValueError, KeyError) as exc:
        connection.last_sync_at = datetime.utcnow()
        connection.last_sync_status = "failed"
        connection.last_sync_error = str(exc)[:2000]
        db.commit()
        raise HTTPException(502, str(exc))


class GoogleOAuthConfigInput(BaseModel):
    client_id: str = Field(min_length=20, max_length=500)


@router.put("/oauth/config")
def save_google_oauth_config(data: GoogleOAuthConfigInput):
    try:
        client_id = save_google_client_id(data.client_id)
    except OAuthConfigurationError as exc:
        raise HTTPException(400, str(exc))
    return {"success":True,"configured":True,"client_id_hint":client_id[:12] + "…","message":"Google Desktop OAuth Client ID 已保存在本机配置目录，不包含客户端密钥。"}

@router.get("/oauth/status")
def google_oauth_status():
    return {
        "google_oauth_configured": bool(os.environ.get("GOOGLE_OAUTH_CLIENT_ID")) or GOOGLE_CONFIG_FILE.exists(),
        "token_encryption_configured": bool(os.environ.get("AI_COMIC_TOKEN_ENCRYPTION_KEY")) or OAUTH_KEY_FILE.exists(),
        "provider": "Google / YouTube Analytics / AdSense",
        "read_only": True,
        "payout_execution_enabled": False,
        "required_environment": [],
        "optional_environment": ["GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "AI_COMIC_TOKEN_ENCRYPTION_KEY"],
        "note": "可在收益中心保存 Google Desktop OAuth Client ID；Windows 使用 DPAPI 保护本机 Fernet 密钥，其他系统使用本机权限受限密钥文件。YouTube Analytics 提供预估收入；AdSense Payments 可读取未付款和已付款记录。API 不执行提现。",
    }
