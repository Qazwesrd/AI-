"""Google OAuth and read-only creator revenue integration.

OAuth tokens are encrypted at rest with Fernet. The desktop app stores the public
Google Client ID locally and creates a local key protected by Windows DPAPI.
AI_COMIC_TOKEN_ENCRYPTION_KEY can optionally override the generated key.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import secrets
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timedelta
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from app.database import STORAGE_DIR


GOOGLE_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/yt-analytics-monetary.readonly",
    "https://www.googleapis.com/auth/adsense.readonly",
]
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API_CALLBACK_URI = "http://127.0.0.1:8000/api/revenue/oauth/google/callback"


class OAuthConfigurationError(RuntimeError):
    pass


class OAuthProviderError(RuntimeError):
    pass


SECRET_DIR = STORAGE_DIR / "secrets"
SECRET_DIR.mkdir(parents=True, exist_ok=True)
OAUTH_KEY_FILE = SECRET_DIR / "oauth-token.key"
GOOGLE_CONFIG_FILE = SECRET_DIR / "google-oauth.json"


def save_google_client_id(value: str):
    value = (value or "").strip()
    if not value or len(value) > 500 or not value.endswith(".apps.googleusercontent.com"):
        raise OAuthConfigurationError("Google Client ID 格式无效。请填写 Google Cloud 中创建的 Desktop app OAuth Client ID。")
    GOOGLE_CONFIG_FILE.write_text(json.dumps({"client_id": value}, ensure_ascii=False), encoding="utf-8")
    os.environ["GOOGLE_OAUTH_CLIENT_ID"] = value
    return value


def _client_id() -> str:
    value = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "").strip()
    if not value and GOOGLE_CONFIG_FILE.exists():
        try:
            value = str(json.loads(GOOGLE_CONFIG_FILE.read_text(encoding="utf-8")).get("client_id") or "").strip()
        except (OSError, ValueError, AttributeError):
            value = ""
    if not value:
        raise OAuthConfigurationError("尚未配置 Google Desktop OAuth Client ID；请先在收益中心填写你自己的 Google OAuth 客户端 ID。")
    return value


def _dpapi_transform(data: bytes, protect: bool) -> bytes:
    if os.name != "nt":
        return data
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

    source_buffer = ctypes.create_string_buffer(data)
    source = DATA_BLOB(len(data), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_byte)))
    output = DATA_BLOB()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    if protect:
        ok = crypt32.CryptProtectData(ctypes.byref(source), "AI Comic Studio OAuth key", None, None, None, 0x1, ctypes.byref(output))
    else:
        ok = crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(output))
    if not ok:
        raise OAuthConfigurationError("Windows DPAPI 无法保护或读取本机 OAuth 加密密钥。")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)


def _load_or_create_key() -> bytes:
    if OAUTH_KEY_FILE.exists():
        stored = OAUTH_KEY_FILE.read_bytes()
        if stored.startswith(b"DPAPI1:"):
            return _dpapi_transform(stored[len(b"DPAPI1:"):], False)
        return stored
    key = Fernet.generate_key()
    stored = b"DPAPI1:" + _dpapi_transform(key, True) if os.name == "nt" else key
    temporary = OAUTH_KEY_FILE.with_suffix(".tmp")
    temporary.write_bytes(stored)
    try:
        temporary.chmod(0o600)
    except OSError:
        pass
    temporary.replace(OAUTH_KEY_FILE)
    return key


def _client_secret() -> str:
    return os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()


def token_cipher() -> Fernet:
    key = os.environ.get("AI_COMIC_TOKEN_ENCRYPTION_KEY", "").strip()
    try:
        key_bytes = key.encode("ascii") if key else _load_or_create_key()
        return Fernet(key_bytes)
    except (ValueError, TypeError, OSError, binascii.Error, UnicodeError) as exc:
        raise OAuthConfigurationError("无法初始化本机 OAuth 令牌加密密钥；请检查 storage/secrets 目录权限。") from exc


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


def start_loopback_listener():
    """Listen once on a random loopback port; Google desktop redirects must have no path."""
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.path not in {"", "/"}:
                self.send_error(404)
                return
            target = API_CALLBACK_URI + ("?" + parsed.query if parsed.query else "")
            self.send_response(302)
            self.send_header("Location", target)
            self.end_headers()

        def log_message(self, format, *args):
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    server.timeout = 300
    port = server.server_address[1]
    import threading

    def serve_once():
        try:
            server.handle_request()
        finally:
            server.server_close()

    threading.Thread(target=serve_once, daemon=True).start()
    return f"http://127.0.0.1:{port}"


def new_authorization(redirect_uri: str):
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    state = secrets.token_urlsafe(32)
    params = {
        "client_id": _client_id(),
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(GOOGLE_SCOPES),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",
        "prompt": "consent",
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


def exchange_code(code: str, verifier: str, redirect_uri: str) -> dict:
    fields = {
        "client_id": _client_id(),
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": redirect_uri,
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


def parse_money(value: str, fallback_currency: str | None = None):
    import re
    raw = str(value or "").strip()
    match = re.search(r"-?[0-9][0-9,]*(?:\.[0-9]+)?", raw)
    if not match:
        return None, None
    amount = float(match.group(0).replace(",", ""))
    upper = raw.upper()
    currency = next((code for code in ("USD", "EUR", "GBP", "JPY", "CNY", "HKD", "CAD", "AUD", "INR", "SGD", "CHF", "NZD", "KRW", "TWD", "BRL", "MXN", "ARS", "CLP", "COP", "PEN", "PLN", "SEK", "NOK", "DKK", "CZK", "HUF", "RON", "TRY", "RUB", "UAH", "AED", "SAR", "QAR", "ILS", "ZAR", "EGP", "THB", "VND", "IDR", "MYR", "PHP", "PKR", "BDT") if code in upper), None)
    if currency is None and fallback_currency in {"USD","EUR","GBP","JPY","CNY","HKD","CAD","AUD","INR","SGD","CHF","NZD","KRW","TWD","BRL","MXN","ARS","CLP","COP","PEN","PLN","SEK","NOK","DKK","CZK","HUF","RON","TRY","RUB","UAH","AED","SAR","QAR","ILS","ZAR","EGP","THB","VND","IDR","MYR","PHP","PKR","BDT"}:
        currency = fallback_currency
    if currency is None:
        # Infer only symbols that identify one currency unambiguously.
        # Bare "$" and "¥" are intentionally not guessed.
        for symbol, code in (("R$", "BRL"), ("C$", "CAD"), ("A$", "AUD"), ("HK$", "HKD"), ("NT$", "TWD"),
                             ("€", "EUR"), ("£", "GBP"), ("₹", "INR"), ("₩", "KRW"),
                             ("₽", "RUB"), ("₺", "TRY"), ("฿", "THB")):
            if symbol in raw:
                currency = code
                break
    return amount, currency
