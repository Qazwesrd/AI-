import json
import os
import subprocess
import urllib.request
import urllib.error
import ipaddress
import socket
from urllib.parse import urlparse
from pathlib import Path
from cryptography.fernet import Fernet

ROOT = Path(os.environ.get("APP_DATA_DIR", str(Path(__file__).resolve().parents[4])))
STORAGE = ROOT / "storage"
VIDEO_DIR = STORAGE / "videos"
AUDIO_DIR = STORAGE / "audio"
SUBTITLE_DIR = STORAGE / "subtitles"

for folder in (VIDEO_DIR, AUDIO_DIR, SUBTITLE_DIR):
    folder.mkdir(parents=True, exist_ok=True)


def decrypt_api_key(value):
    if not value:
        return ""
    key_file = STORAGE / "secrets" / "local.key"
    if not key_file.exists():
        raise RuntimeError("API 密钥加密文件不存在，无法解密已保存的密钥")
    try:
        return Fernet(key_file.read_bytes()).decrypt(value.encode()).decode()
    except Exception as exc:
        raise RuntimeError("API 密钥解密失败，请在 AI 设置中重新保存密钥") from exc


def http_json(url, api_key, method="POST", body=None, timeout=300):
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    data = json.dumps(body or {}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url, data=data if method != "GET" else None,
        headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            content_type = response.headers.get("Content-Type", "")
            if "json" in content_type or raw[:1] in (b"{", b"["):
                return json.loads(raw.decode("utf-8"))
            return {"raw_response": raw.decode("utf-8", errors="replace")}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"远程 API 返回 HTTP {exc.code}: {detail[:2000]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"无法连接远程 API: {exc}") from exc


def _validate_public_https_url(url):
    parsed = urlparse(str(url))
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("远程媒体地址必须使用 HTTPS")
    if parsed.username or parsed.password:
        raise ValueError("远程媒体地址不能包含用户名或密码")
    host = parsed.hostname
    if host.lower() in {"localhost", "localhost.localdomain"}:
        raise ValueError("不允许访问本机地址")
    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError("无法解析远程媒体服务器地址") from exc
    if not addresses:
        raise ValueError("远程媒体服务器没有可用地址")
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise ValueError("远程媒体地址解析到非公网 IP，已阻止请求")


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_public_https_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def http_download(url, destination, api_key=None):
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    _validate_public_https_url(url)
    request = urllib.request.Request(url, headers=headers)
    opener = urllib.request.build_opener(_SafeRedirectHandler)
    try:
        with opener.open(request, timeout=600) as response:
            with open(destination, "wb") as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
    except Exception:
        if os.path.exists(destination):
            os.remove(destination)
        raise


def safe_storage_file(path):
    candidate = Path(path).resolve()
    root = STORAGE.resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("文件路径超出素材存储目录")
    if not candidate.is_file():
        raise FileNotFoundError("指定的素材文件不存在")
    return candidate


def run_ffmpeg(args):
    executable = _bundled_media_path("ffmpeg.exe")
    command = [executable, "-y"] + [str(item) for item in args]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=7200
        )
    except FileNotFoundError as exc:
        raise RuntimeError("未找到 FFmpeg，请先安装 FFmpeg 并配置 PATH") from exc
    if result.returncode != 0:
        raise RuntimeError("FFmpeg 执行失败：" + result.stderr[-3000:])
    return result


def probe_media(path):
    executable = _bundled_media_path("ffprobe.exe")
    command = [
        executable, "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", str(path)
    ]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=120
        )
    except FileNotFoundError as exc:
        raise RuntimeError("未找到 FFprobe，请安装 FFmpeg 完整版本") from exc
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-2000:])
    return json.loads(result.stdout)



# Packaged desktop media-tool discovery.
def _bundled_media_path(filename: str) -> str:
    from pathlib import Path
    import os
    import sys

    configured = os.environ.get(
        "FFMPEG_PATH" if filename == "ffmpeg.exe" else "FFPROBE_PATH"
    )
    if configured and Path(configured).is_file():
        return configured

    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(
            Path(sys.executable).resolve().parent.parent / "media" / filename
        )
        candidates.append(
            Path(sys.executable).resolve().parent / "media" / filename
        )

    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)

    from shutil import which
    command = "ffmpeg" if filename == "ffmpeg.exe" else "ffprobe"
    found = which(command)
    if found:
        return found

    raise FileNotFoundError(
        f"找不到 {command}。请检查软件媒体组件是否完整安装。"
    )
