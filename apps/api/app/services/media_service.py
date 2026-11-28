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
    """Find a user-provided FFmpeg binary without requiring it to be bundled."""
    import os
    import sys
    from pathlib import Path
    from shutil import which

    is_ffmpeg = filename.lower().startswith("ffmpeg")
    command = "ffmpeg" if is_ffmpeg else "ffprobe"
    configured = os.environ.get("FFMPEG_PATH" if is_ffmpeg else "FFPROBE_PATH")
    if configured:
        configured_path = Path(configured).expanduser()
        if configured_path.is_file():
            return str(configured_path.resolve())
        # Accept a directory in the explicit setting as a convenience.
        directory_candidate = configured_path / filename
        if directory_candidate.is_file():
            return str(directory_candidate.resolve())

    candidates = []
    data_root = os.environ.get("AICOMICS_DATA_DIR") or os.environ.get("APP_DATA_DIR")
    if data_root:
        root = Path(data_root).expanduser()
        candidates.extend([
            root / "engines" / "FFmpeg" / "bin" / filename,
            root / "FFmpeg" / "bin" / filename,
            root / "engines" / "ffmpeg" / "bin" / filename,
            root / "tools" / "ffmpeg" / "bin" / filename,
        ])

    # Common user-managed Windows installations; no files are downloaded or changed.
    home = Path.home()
    candidates.extend([
        home / "ffmpeg" / "bin" / filename,
        home / "Downloads" / "ffmpeg" / "bin" / filename,
        Path("C:/ffmpeg/bin") / filename,
        Path("C:/Program Files/ffmpeg/bin") / filename,
        Path("C:/Program Files (x86)/ffmpeg/bin") / filename,
    ])

    if getattr(sys, "frozen", False):
        executable_dir = Path(sys.executable).resolve().parent
        candidates.extend([
            executable_dir.parent / "media" / filename,
            executable_dir / "media" / filename,
        ])

    for candidate in candidates:
        try:
            if candidate.is_file():
                return str(candidate.resolve())
        except OSError:
            continue

    found = which(command)
    if found:
        return found

    raise FileNotFoundError(
        f"找不到 {command}。请将 FFmpeg 的 bin 目录加入 PATH，"
        "或放入软件数据目录的 engines/FFmpeg/bin；软件不会自动下载。"
    )


def _parse_frame_rate(value):
    """Parse FFprobe rates such as 24/1 or 30000/1001 without raising."""
    try:
        numerator, denominator = str(value or "").split("/", 1)
        denominator = float(denominator)
        if denominator == 0:
            return None
        rate = float(numerator) / denominator
        return round(rate, 3) if rate > 0 else None
    except (TypeError, ValueError, ZeroDivisionError):
        return None


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
    frame_rate = _parse_frame_rate(
        video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate")
    )
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
        "frame_rate": frame_rate,
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


def analyze_shot_media_consistency(shot_media):
    """Compare per-shot technical stream properties; all findings are advisory."""
    from collections import Counter
    def parse_rate(value):
        if value in (None, "", "0/0", "N/A"): return None
        try:
            if isinstance(value, str) and "/" in value:
                numerator, denominator = value.split("/", 1)
                denominator = float(denominator)
                return float(numerator) / denominator if denominator else None
            return float(value)
        except (TypeError, ValueError, ZeroDivisionError): return None
    scanned, skipped = [], []
    for item in shot_media if isinstance(shot_media, list) else []:
        if not isinstance(item, dict) or not item.get("path"): continue
        try:
            streams = (probe_media(item["path"]).get("streams") or [])
            video = next((x for x in streams if x.get("codec_type") == "video"), None)
            audio = next((x for x in streams if x.get("codec_type") == "audio"), None)
            if not video: raise ValueError("缺少视频流")
            sample_rate = audio.get("sample_rate") if audio else None
            scanned.append({
                "shot_id": item.get("shot_id"), "shot_number": item.get("shot_number"),
                "width": int(video.get("width") or 0) or None,
                "height": int(video.get("height") or 0) or None,
                "frame_rate": parse_rate(video.get("avg_frame_rate")) or parse_rate(video.get("r_frame_rate")),
                "video_codec": video.get("codec_name"), "pixel_format": video.get("pix_fmt"),
                "has_audio": bool(audio),
                "audio_sample_rate": int(sample_rate) if sample_rate and str(sample_rate).isdigit() else None,
                "audio_channels": int(audio.get("channels")) if audio and audio.get("channels") else None,
            })
        except Exception as exc:
            skipped.append({"shot_id": item.get("shot_id"), "shot_number": item.get("shot_number"), "reason": str(exc)[:240]})
    def common(field):
        values = [x.get(field) for x in scanned if x.get(field) is not None]
        return Counter(values).most_common(1)[0][0] if values else None
    fields = ["width", "height", "frame_rate", "video_codec", "pixel_format", "has_audio", "audio_sample_rate", "audio_channels"]
    baseline = {field: common(field) for field in fields}
    anomalies = []
    labels = {"width":"画面宽度", "height":"画面高度", "video_codec":"视频编码", "pixel_format":"像素格式", "has_audio":"音轨存在性", "audio_sample_rate":"音频采样率", "audio_channels":"音频声道数"}
    for item in scanned:
        differences = []
        for field, label in labels.items():
            expected, actual = baseline.get(field), item.get(field)
            if expected is not None and actual != expected:
                differences.append({"field":field, "label":label, "expected":expected, "actual":actual})
        expected, actual = baseline.get("frame_rate"), item.get("frame_rate")
        if expected is not None and actual is not None and abs(actual - expected) > 0.01:
            differences.append({"field":"frame_rate", "label":"帧率", "expected":round(expected,3), "actual":round(actual,3)})
        if differences: anomalies.append({"shot_id":item.get("shot_id"), "shot_number":item.get("shot_number"), "differences":differences})
    warnings = []
    if anomalies: warnings.append("发现 %d 个镜头的媒体参数与本集主流参数不一致" % len(anomalies))
    if skipped: warnings.append("有 %d 个镜头无法读取媒体参数" % len(skipped))
    return {"scan_complete":True, "scanned_shots":len(scanned), "skipped_shots":skipped, "baseline":baseline, "shots":scanned, "anomalies":anomalies, "warnings":warnings, "note":"逐镜头参数以本集出现频率最高的参数作为对照；不同创作意图可能导致合理差异，所有提示均需人工复核。"}
def analyze_subtitle_timing(cues, video_duration, tolerance=0.05):
    """Validate subtitle cue ranges and overlaps against the rendered video duration."""
    try:
        duration = float(video_duration)
    except (TypeError, ValueError):
        raise ValueError("无法确认成片时长，不能校验字幕时间")
    if not duration > 0:
        raise ValueError("成片时长必须大于 0 秒")
    if not isinstance(cues, list):
        cues = []
    invalid_ranges, normalized = [], []
    for index, cue in enumerate(cues):
        if not isinstance(cue, dict):
            invalid_ranges.append({"index": index, "reason": "字幕条目格式无效"})
            continue
        try:
            start, end = float(cue.get("start")), float(cue.get("end"))
        except (TypeError, ValueError):
            invalid_ranges.append({"index": index, "reason": "开始或结束时间不是有效数字"})
            continue
        if not (start >= 0 and end > start):
            invalid_ranges.append({"index": index, "start_seconds": start, "end_seconds": end,
                                   "reason": "字幕时间范围无效，开始必须非负且结束必须晚于开始"})
            continue
        normalized.append({"index": index, "start": start, "end": end})
        if end > duration + float(tolerance):
            invalid_ranges.append({"index": index, "start_seconds": round(start, 3),
                                   "end_seconds": round(end, 3),
                                   "reason": f"字幕结束时间超出成片时长 {duration:.3f} 秒"})
    ordered = sorted(normalized, key=lambda item: (item["start"], item["end"]))
    overlaps = []
    for previous, current in zip(ordered, ordered[1:]):
        overlap = previous["end"] - current["start"]
        if overlap > float(tolerance):
            overlaps.append({"previous_index": previous["index"], "current_index": current["index"],
                             "overlap_seconds": round(overlap, 3)})
    warnings = []
    if invalid_ranges:
        warnings.append(f"发现 {len(invalid_ranges)} 条字幕时间范围无效或超出成片时长")
    if overlaps:
        warnings.append(f"发现 {len(overlaps)} 处字幕时间重叠，请确认是否为有意叠字")
    return {"total_cues": len(cues), "invalid_ranges": invalid_ranges,
            "overlaps": overlaps, "warnings": warnings}


def scan_final_cut_quality(path, subtitle_cues=None):
    """Create an opt-in local report combining stream, black-frame, silence and subtitle checks."""
    file_path = Path(path)
    if not file_path.is_file():
        raise ValueError("待检测的成片文件不存在")
    info = probe_media(file_path)
    validation = validate_rendered_video(file_path)
    sync = analyze_audio_video_sync(info)
    media_quality = scan_media_quality(file_path)
    subtitles = analyze_subtitle_timing(subtitle_cues or [], validation["duration_seconds"])
    warnings = []
    warnings.extend(validation.get("warnings") or [])
    warnings.extend(sync.get("warnings") or [])
    warnings.extend(media_quality.get("warnings") or [])
    warnings.extend(subtitles.get("warnings") or [])
    return {
        "scan_complete": True, "status": "warning" if warnings else "ok",
        "duration_seconds": validation["duration_seconds"],
        "resolution": {"width": validation["width"], "height": validation["height"]},
        "streams": {"video_codec": validation.get("video_codec"),
                    "audio_codec": validation.get("audio_codec"), "has_audio": validation.get("has_audio")},
        "audio_video_sync": sync, "media_quality": media_quality,
        "subtitle_timing": subtitles, "warnings": warnings,
        "note": "本报告只提供本地媒体技术检查和字幕时间范围检查；不会自动修改字幕或成片，也不能判断人物口型与对白语义是否一致。所有提示均需人工复核。",
    }
