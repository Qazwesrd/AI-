import json
import os
import subprocess
import urllib.request
import urllib.error
import ipaddress
import socket
import re
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


def http_download(url, destination, api_key=None, max_bytes=2 * 1024 * 1024 * 1024):
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    _validate_public_https_url(url)
    request = urllib.request.Request(url, headers=headers)
    opener = urllib.request.build_opener(_SafeRedirectHandler)
    try:
        with opener.open(request, timeout=600) as response:
            with open(destination, "wb") as output:
                total = 0
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError("远程媒体文件超过允许的下载大小")
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


def validate_rendered_video(path, expected_duration=None, duration_tolerance=2.0, require_audio=False):
    """Validate a completed MP4 with FFprobe before it is registered as a deliverable."""
    file_path = Path(path)
    if not file_path.is_file():
        raise ValueError("导出文件不存在")
    size = file_path.stat().st_size
    if size < 1024:
        raise ValueError("导出文件过小，可能是空文件或不完整文件")
    info = probe_media(file_path)
    streams = info.get("streams") or []
    video_stream = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio_stream = next((item for item in streams if item.get("codec_type") == "audio"), None)
    if not video_stream:
        raise ValueError("导出文件中没有有效的视频流")
    raw_duration = (info.get("format") or {}).get("duration") or video_stream.get("duration") or 0
    try:
        duration = float(raw_duration)
    except (TypeError, ValueError):
        duration = 0.0
    if duration <= 0:
        raise ValueError("FFprobe 无法确认导出视频时长")
    if expected_duration is not None:
        tolerance = max(float(duration_tolerance), float(expected_duration) * 0.01)
        if abs(duration - float(expected_duration)) > tolerance:
            raise ValueError(
                f"导出视频时长异常：预期约 {float(expected_duration):.2f} 秒，实际 {duration:.2f} 秒"
            )
    if require_audio and not audio_stream:
        raise ValueError("导出文件缺少音频流")
    width = int(video_stream.get("width") or 0)
    height = int(video_stream.get("height") or 0)
    warnings = []
    if not audio_stream:
        warnings.append("文件没有音频流；如果这是静音视频则无需处理")
    if width < 720 or height < 480:
        warnings.append("视频分辨率较低，发布前建议检查画质")
    return {
        "valid": True,
        "size_bytes": size,
        "duration_seconds": round(duration, 3),
        "width": width,
        "height": height,
        "video_codec": video_stream.get("codec_name"),
        "audio_codec": audio_stream.get("codec_name") if audio_stream else None,
        "has_audio": bool(audio_stream),
        "warnings": warnings,
    }




def analyze_audio_video_sync(info, start_tolerance=0.25, duration_tolerance=0.5):
    """Compare container stream timing metadata; this cannot verify visual lip sync."""
    streams = info.get("streams") or []
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    if not video:
        raise ValueError("文件中没有有效的视频流")

    format_duration = (info.get("format") or {}).get("duration")

    def number(value, fallback=0.0):
        try:
            return float(value) if value is not None else float(fallback)
        except (TypeError, ValueError):
            return float(fallback)

    video_start = number(video.get("start_time"))
    video_duration = number(video.get("duration"), format_duration or 0)
    if video_duration <= 0:
        video_duration = number(format_duration)
    if video_duration <= 0:
        raise ValueError("FFprobe 无法确认视频流时长")

    warnings = []
    audio_result = None
    start_offset = None
    duration_delta = None
    if audio:
        audio_start = number(audio.get("start_time"))
        audio_duration = number(audio.get("duration"), format_duration or video_duration)
        if audio_duration <= 0:
            audio_duration = number(format_duration, video_duration)
        start_offset = audio_start - video_start
        duration_delta = audio_duration - video_duration
        if abs(start_offset) > float(start_tolerance):
            warnings.append(f"音轨与视频流起始时间相差 {start_offset:+.3f} 秒，请检查是否存在整体偏移")
        if abs(duration_delta) > float(duration_tolerance):
            warnings.append(f"音轨与视频流时长相差 {duration_delta:+.3f} 秒，请检查尾部音画是否错位")
        audio_result = {"start_seconds": round(audio_start, 3), "duration_seconds": round(audio_duration, 3)}
    else:
        warnings.append("成片没有音轨，无法进行音画同步元数据比较")

    return {
        "scan_complete": True,
        "metadata_only": True,
        "status": "warning" if warnings else "ok",
        "video": {"start_seconds": round(video_start, 3), "duration_seconds": round(video_duration, 3)},
        "audio": audio_result,
        "audio_video_start_offset_seconds": round(start_offset, 3) if start_offset is not None else None,
        "audio_video_duration_delta_seconds": round(duration_delta, 3) if duration_delta is not None else None,
        "start_tolerance_seconds": float(start_tolerance),
        "duration_tolerance_seconds": float(duration_tolerance),
        "warnings": warnings,
        "note": "本检查只比较 FFprobe 音视频流的起始时间和时长，不能判断人物口型、对白内容或逐帧同步；请结合成片试听复核。",
    }


def scan_audio_video_sync(path, start_tolerance=0.25, duration_tolerance=0.5):
    """Probe a local media file and compare audio/video stream timing metadata."""
    file_path = Path(path)
    if not file_path.is_file():
        raise ValueError("待检测的视频文件不存在")
    return analyze_audio_video_sync(
        probe_media(file_path),
        start_tolerance=start_tolerance,
        duration_tolerance=duration_tolerance,
    )



def scan_media_quality(path, black_min_duration=0.5, silence_min_duration=1.0, silence_noise="-50dB"):
    """Detect sustained black frames and digital silence; findings are advisory, not automatic failures."""
    file_path = Path(path)
    if not file_path.is_file():
        raise ValueError("待检测的视频文件不存在")

    info = probe_media(file_path)
    streams = info.get("streams") or []
    video_stream = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio_stream = next((item for item in streams if item.get("codec_type") == "audio"), None)
    if not video_stream:
        raise ValueError("文件中没有可检测的视频流")

    executable = _bundled_media_path("ffmpeg.exe")

    def run_filter(arguments):
        result = subprocess.run(
            [executable, "-hide_banner", "-loglevel", "info", "-i", str(file_path), *arguments],
            capture_output=True,
            text=True,
            timeout=7200,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "")[-2000:]
            raise RuntimeError("媒体质量扫描失败：" + detail)
        return (result.stderr or "") + "\n" + (result.stdout or "")

    black_log = run_filter([
        "-vf", f"blackdetect=d={float(black_min_duration)}:pix_th=0.10",
        "-an", "-f", "null", "-"
    ])
    black_segments = []
    for match in re.finditer(
        r"black_start:([0-9.]+)\s+black_end:([0-9.]+)\s+black_duration:([0-9.]+)",
        black_log,
    ):
        black_segments.append({
            "start_seconds": round(float(match.group(1)), 3),
            "end_seconds": round(float(match.group(2)), 3),
            "duration_seconds": round(float(match.group(3)), 3),
        })

    silence_segments = []
    audio_levels = None
    if audio_stream:
        levels_log = run_filter(["-vn", "-af", "volumedetect", "-f", "null", "-"])
        mean_match = re.search(r"mean_volume:\s*(-?inf|-?[0-9.]+)\s*dB", levels_log, re.IGNORECASE)
        max_match = re.search(r"max_volume:\s*(-?inf|-?[0-9.]+)\s*dB", levels_log, re.IGNORECASE)
        def parse_db(match):
            if not match:
                return None
            value = match.group(1).lower()
            return None if value == "-inf" else round(float(value), 2)
        audio_levels = {
            "mean_volume_db": parse_db(mean_match),
            "max_volume_db": parse_db(max_match),
        }
        silence_log = run_filter([
            "-vn", "-af", f"silencedetect=noise={silence_noise}:d={float(silence_min_duration)}",
            "-f", "null", "-"
        ])
        for match in re.finditer(
            r"silence_start:([0-9.]+).*?silence_end:([0-9.]+)\s*\|\s*silence_duration:([0-9.]+)",
            silence_log,
            re.DOTALL,
        ):
            silence_segments.append({
                "start_seconds": round(float(match.group(1)), 3),
                "end_seconds": round(float(match.group(2)), 3),
                "duration_seconds": round(float(match.group(3)), 3),
            })

    warnings = []
    if black_segments:
        warnings.append(f"检测到 {len(black_segments)} 段持续黑屏，请检查是否为转场或意外空镜")
    if silence_segments:
        warnings.append(f"检测到 {len(silence_segments)} 段持续静音，请确认是否符合剧情需要")
    if not audio_stream:
        warnings.append("文件没有音轨，已跳过静音检测")
    elif audio_levels:
        mean_level = audio_levels.get("mean_volume_db")
        peak_level = audio_levels.get("max_volume_db")
        if mean_level is None:
            warnings.append("音频平均音量接近数字静音，请检查配音或音轨")
        elif mean_level < -35:
            warnings.append(f"音频平均音量偏低（{mean_level} dB），请检查对白和背景音乐响度")
        if peak_level is not None and peak_level > -0.5:
            warnings.append(f"音频峰值接近或达到削波风险（{peak_level} dB），建议降低混音增益")
    return {
        "scan_complete": True,
        "black_frame_threshold_seconds": float(black_min_duration),
        "silence_threshold_seconds": float(silence_min_duration),
        "silence_noise_threshold": silence_noise,
        "has_audio": bool(audio_stream),
        "audio_levels": audio_levels,
        "black_segments": black_segments,
        "silence_segments": silence_segments,
        "warnings": warnings,
        "note": "黑屏和静音检测结果仅供复核；有意的转场、片头片尾或静音镜头可能被识别为提示。",
    }
