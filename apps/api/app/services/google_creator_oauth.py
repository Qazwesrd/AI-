"""Google OAuth and read-only creator revenue integration.

Tokens are encrypted at rest with Fernet. Configure GOOGLE_OAUTH_CLIENT_ID and
AI_COMIC_TOKEN_ENCRYPTION_KEY in the desktop app environment before connecting.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet, InvalidToken


GOOGLE_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/yt-analytics-monetary.readonly",
    "https://www.googleapis.com/auth/adsense.readonly",
]
REDIRECT_URI = "http://127.0.0.1:8000/api/revenue/oauth/google/callback"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"


class OAuthConfigurationError(RuntimeError):
    pass


class OAuthProviderError(RuntimeError):
    pass


def _client_id() -> str:
    value = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "").strip()
    if not value:
        raise OAuthConfigurationError("尚未配置 GOOGLE_OAUTH_CLIENT_ID；请先在本机设置 Google Desktop OAuth 客户端 ID。")
    return value


def _client_secret() -> str:
    return os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()


def token_cipher() -> Fernet:
    key = os.environ.get("AI_COMIC_TOKEN_ENCRYPTION_KEY", "").strip()
    if not key:
        raise OAuthConfigurationError("尚未配置 AI_COMIC_TOKEN_ENCRYPTION_KEY，出于安全原因不会以明文保存 OAuth 令牌。")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise OAuthConfigurationError("AI_COMIC_TOKEN_ENCRYPTION_KEY 格式无效；需要 Fernet URL-safe Base64 密钥。") from exc


def encrypt_token(value: str) -> str:
    if not value:
        return ""
    return token_cipher().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_token(value: str) -> str:
    if not value:
        return ""
    try:
        return token_cipher().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeError) as exc:
        raise OAuthConfigurationError("无法解密已保存的 OAuth 令牌；请检查本机加密密钥是否与授权时一致。") from exc


def new_authorization():
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    state = secrets.token_urlsafe(32)
    params = {
        "client_id": _client_id(),
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(GOOGLE_SCOPES),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
    }
    return AUTH_URL + "?" + urllib.parse.urlencode(params), state, verifier


def _post_form(url: str, fields: dict) -> dict:
    body = urllib.parse.urlencode(fields).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read(1024 * 1024).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read(4096).decode("utf-8", "replace")
        raise OAuthProviderError("Google OAuth 请求失败：" + detail[:1000]) from exc
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise OAuthProviderError("无法连接 Google OAuth 服务，请检查网络后重试。") from exc
    if not isinstance(data, dict) or data.get("error"):
        raise OAuthProviderError(str(data.get("error_description") or data.get("error") or "Google OAuth 返回无效响应"))
    return data


def exchange_code(code: str, verifier: str) -> dict:
    fields = {
        "client_id": _client_id(),
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    }
    secret = _client_secret()
    if secret:
        fields["client_secret"] = secret
    return _post_form(TOKEN_URL, fields)


def refresh_access_token(refresh_token: str) -> dict:
    fields = {
        "client_id": _client_id(),
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    secret = _client_secret()
    if secret:
        fields["client_secret"] = secret
    return _post_form(TOKEN_URL, fields)


def google_get_json(url: str, access_token: str) -> dict:
    request = urllib.request.Request(url, headers={"Authorization": "Bearer " + access_token, "Accept": "application/json"}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            data = json.loads(response.read(4 * 1024 * 1024).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read(4096).decode("utf-8", "replace")
        raise OAuthProviderError("Google API 请求失败：" + detail[:1000]) from exc
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise OAuthProviderError("Google API 网络请求失败或响应无效。") from exc
    if not isinstance(data, dict):
        raise OAuthProviderError("Google API 返回了非对象响应。")
    return data


def token_expiry(token_data: dict) -> datetime:
    seconds = int(token_data.get("expires_in") or 3600)
    return datetime.utcnow() + timedelta(seconds=max(60, seconds - 60))


def get_email(access_token: str) -> str:
    data = google_get_json("https://www.googleapis.com/oauth2/v2/userinfo", access_token)
    return str(data.get("email") or data.get("id") or "Google账户")


def parse_money(value: str):
    import re
    raw = str(value or "").strip()
    match = re.search(r"-?[0-9][0-9,]*(?:\\.[0-9]+)?", raw)
    if not match:
        return None, None
    amount = float(match.group(0).replace(",", ""))
    upper = raw.upper()
    currency = next((code for code in ("USD", "EUR", "GBP", "JPY", "CNY", "HKD", "CAD", "AUD", "INR") if code in upper), None)
    if currency is None:
        currency = "USD" if "$" in raw else ("EUR" if "€" in raw else ("GBP" if "£" in raw else ("JPY" if "¥" in raw else None)))
    return amount, currency
