"""Safe, opt-in RSS/Atom collection for publicly accessible research feeds.

Only HTTPS feeds on explicitly supported platform domains are accepted. Redirects,
private-network destinations, oversized responses and XML entity declarations are rejected.
This module stores feed metadata (title/link/date), not full copyrighted page content.
"""
from __future__ import annotations

import ipaddress
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html.parser import HTMLParser

MAX_FEED_BYTES = 2 * 1024 * 1024
MAX_ITEMS = 100
TIMEOUT_SECONDS = 12
PLATFORM_DOMAINS = {
    "YouTube": {"youtube.com", "youtube.googleapis.com"},
    "B站": {"bilibili.com"},
    "抖音": {"douyin.com"},
    "快手": {"kuaishou.com"},
    "TikTok": {"tiktok.com"},
    "Facebook": {"facebook.com"},
    "红果短剧": {"hongguo.com"},
    "Reddit": {"reddit.com"},
    "Royal Road": {"royalroad.com"},
    "Wattpad": {"wattpad.com"},
    "Tapas": {"tapas.io"},
    "Inkitt": {"inkitt.com"},
    "Pixiv": {"pixiv.net"},
}
ALLOWED_DOMAINS = set().union(*PLATFORM_DOMAINS.values())


class FeedCollectionError(ValueError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        if data and len(" ".join(self.parts)) < 1200:
            self.parts.append(data.strip())


def validate_public_feed_url(url: str, platform: str | None = None) -> str:
    parsed = urllib.parse.urlsplit((url or "").strip())
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise FeedCollectionError("仅允许 HTTPS 公开订阅源")
    if parsed.username or parsed.password or parsed.fragment:
        raise FeedCollectionError("订阅地址不能包含用户名、密码或片段")
    host = parsed.hostname.encode("idna").decode("ascii").lower().rstrip(".")
    if any(part in urllib.parse.parse_qs(parsed.query) for part in ("token", "access_token", "api_key", "key", "secret")):
        raise FeedCollectionError("订阅地址疑似包含密钥参数，请移除后再添加")
    try:
        ipaddress.ip_address(host)
        raise FeedCollectionError("不接受 IP 地址形式的订阅源")
    except ValueError:
        pass
    allowed_domains = PLATFORM_DOMAINS.get(platform, ALLOWED_DOMAINS) if platform else ALLOWED_DOMAINS
    if platform and platform not in PLATFORM_DOMAINS:
        raise FeedCollectionError("当前平台暂不支持该采集适配器")
    if not any(host == domain or host.endswith("." + domain) for domain in allowed_domains):
        raise FeedCollectionError("订阅源域名与所选平台不匹配，或不在允许的公开来源范围内")
    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise FeedCollectionError("无法解析订阅源域名") from exc
    if not addresses:
        raise FeedCollectionError("订阅源域名没有可用地址")
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise FeedCollectionError("订阅源解析到非公网地址，已拒绝访问")
    return urllib.parse.urlunsplit(("https", host, parsed.path or "/", parsed.query, ""))


def _strip_markup(value: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(value or "")
        return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()[:300]
    except Exception:
        return ""


def parse_feed(payload: bytes) -> list[dict]:
    if not payload or len(payload) > MAX_FEED_BYTES:
        raise FeedCollectionError("订阅内容为空或超过 2 MB 限制")
    prefix = payload[:4096].upper()
    if b"<!DOCTYPE" in prefix or b"<!ENTITY" in prefix:
        raise FeedCollectionError("拒绝包含 XML DTD/实体声明的订阅内容")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise FeedCollectionError("响应不是有效的 RSS/Atom XML") from exc

    items = []
    root_name = root.tag.rsplit("}", 1)[-1].lower()
    if root_name == "feed":
        entries = [node for node in root if node.tag.rsplit("}", 1)[-1].lower() == "entry"]
    else:
        channel = next((node for node in root.iter() if node.tag.rsplit("}", 1)[-1].lower() == "channel"), root)
        entries = [node for node in channel if node.tag.rsplit("}", 1)[-1].lower() == "item"]

    for entry in entries[:MAX_ITEMS]:
        values = {}
        for child in entry:
            key = child.tag.rsplit("}", 1)[-1].lower()
            if key in {"title", "link", "pubdate", "published", "updated", "id", "description", "summary"}:
                values[key] = (child.text or "").strip()
                if key == "link" and not values[key]:
                    values[key] = child.attrib.get("href", "")
        link = values.get("link") or values.get("id", "")
        title = values.get("title", "").strip()
        if not title or not link:
            continue
        parsed_link = urllib.parse.urlsplit(link)
        if parsed_link.scheme not in {"https", "http"} or not parsed_link.hostname:
            continue
        published = values.get("pubdate") or values.get("published") or values.get("updated") or ""
        items.append({
            "title": title[:300],
            "url": link[:2000],
            "published": published[:100],
            "summary_excerpt": _strip_markup(values.get("description") or values.get("summary") or ""),
        })
    return items


def fetch_public_feed(url: str, platform: str | None = None) -> list[dict]:
    safe_url = validate_public_feed_url(url, platform)
    request = urllib.request.Request(
        safe_url,
        headers={"User-Agent": "AIComicStudio-PublicResearch/1.0 (+user-configured public RSS reader)", "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml"},
        method="GET",
    )
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            if response.status != 200:
                raise FeedCollectionError(f"订阅源返回 HTTP {response.status}")
            content_type = (response.headers.get("Content-Type") or "").lower()
            if not any(kind in content_type for kind in ("xml", "rss", "atom", "text/plain", "application/octet-stream")):
                raise FeedCollectionError("响应类型不是可接受的 XML 订阅")
            data = response.read(MAX_FEED_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400:
            raise FeedCollectionError("订阅源发生跳转；为防止跳转到不安全地址，已停止采集") from exc
        raise FeedCollectionError(f"订阅源请求失败：HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise FeedCollectionError("订阅源连接失败或超时") from exc
    return parse_feed(data)


def parse_observed_date(value: str) -> str:
    if not value:
        return datetime.now(timezone.utc).date().isoformat()
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(value)
        return dt.date().isoformat()
    except (TypeError, ValueError, OverflowError):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            return datetime.now(timezone.utc).date().isoformat()
