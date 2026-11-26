import json
import re
import uuid
import math
import shutil
import subprocess
from pathlib import Path

from app.services.media_service import (
    STORAGE, VIDEO_DIR, safe_storage_file, run_ffmpeg, probe_media
)

EDIT_DIR = STORAGE / "edit-jobs"
EDIT_DIR.mkdir(parents=True, exist_ok=True)


def call_ai_for_edit_plan(db, provider, model, instruction, source_files,
                          confirm_paid=False):
    """Generate a video edit plan using an already-installed local Ollama model only."""
    from app.routers.local_ai_runtime import _installed_models, _ollama_request

    installed = _installed_models()
    names = [item["name"] for item in installed if isinstance(item, dict) and item.get("name")]
    selected = next((name for name in ("qwen3.5:4b", "qwen3:4b") if name in names), None)
    if not selected:
        raise RuntimeError("没有已安装的 Qwen 本地文本模型；离线剪辑不会联网下载模型。")

    system_prompt = """
你是专业影视剪辑师。请将用户要求转换为严格 JSON。
只允许引用下方输入文件列表中的 file_path，必须逐字复制完整路径，不得生成新路径。
不得假设你看到了视频画面；只依据文件名、时长、分辨率和用户文字要求。每段 end 不得超过对应视频时长。未知画面写入 notes。
只输出 JSON，格式：
{"title":"项目名称","summary":"方案说明","segments":[{"file_path":"完整输入路径","start":0,"end":5,"description":"用途"}],"width":1920,"height":1080,"subtitle_text":"","notes":[]}
segments 必须非空；start/end 为秒，start >= 0 且 end > start；每段路径必须来自输入列表。
"""
    source_manifest = []
    for raw_path in source_files:
        path = safe_storage_file(raw_path)
        info = probe_media(path)
        fmt = info.get("format") or {}
        streams = info.get("streams") or []
        video = next((item for item in streams if item.get("codec_type") == "video"), {})
        source_manifest.append({
            "file_path": str(path),
            "file_name": path.name,
            "duration_seconds": round(float(fmt.get("duration") or 0), 3),
            "width": video.get("width"),
            "height": video.get("height"),
        })
    user_prompt = (
        "剪辑要求：\n" + str(instruction)[:12000] +
        "\n\n允许使用的输入文件及媒体信息（只能引用 file_path）：\n" +
        json.dumps(source_manifest, ensure_ascii=False)
    )
    try:
        result = _ollama_request(
            "/api/generate",
            {
                "model": selected,
                "system": system_prompt,
                "prompt": user_prompt,
                "stream": False,
                "format": "json",
                "think": False,
                "options": {"temperature": 0.2, "num_ctx": 8192, "num_predict": 4096},
            },
            timeout=900.0,
        )
    except Exception as exc:
        raise RuntimeError("本地 AI 剪辑分析失败：" + str(exc)[:1000]) from exc

    content = result.get("response") if isinstance(result, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("本地模型没有返回有效剪辑方案")
    content = re.sub(r"^\x60\x60\x60(?:json)?\s*", "", content.strip(), flags=re.I)
    content = re.sub(r"\s*\x60\x60\x60$", "", content)
    try:
        plan = json.loads(content)
    except Exception as exc:
        raise RuntimeError("本地模型返回的剪辑方案不是有效 JSON：" + content[:1500]) from exc
    if not isinstance(plan, dict) or not isinstance(plan.get("segments"), list):
        raise RuntimeError("本地模型返回的剪辑方案缺少 segments")
    return plan, {"provider": "ollama-local", "model": selected}


def validate_plan(plan, allowed_files):
    if not plan.get("segments"):
        raise ValueError("剪辑方案没有可执行的视频片段")

    allowed = {str(Path(x).resolve()) for x in allowed_files}
    validated = []

    for index, segment in enumerate(plan["segments"], 1):
        if not isinstance(segment, dict):
            raise ValueError(f"第 {index} 个片段格式错误")

        raw_path = segment.get("file_path")
        if not raw_path:
            raise ValueError(f"第 {index} 个片段缺少视频路径")

        path = safe_storage_file(raw_path)
        if str(path.resolve()) not in allowed:
            raise ValueError(f"第 {index} 个片段使用了未授权的输入文件")

        try:
            start = float(segment.get("start", 0))
            end = float(segment.get("end", 0))
        except (TypeError, ValueError):
            raise ValueError(f"第 {index} 个片段时间必须是数字")

        if not math.isfinite(start) or not math.isfinite(end):
            raise ValueError(f"第 {index} 个片段时间不能是 NaN 或无穷大")
        if start < 0 or end <= start:
            raise ValueError(f"第 {index} 个片段时间范围无效")
        if end - start > 7200:
            raise ValueError(f"第 {index} 个片段时长超过限制")

        try:
            media_info = probe_media(path)
            source_duration = float(media_info.get("format", {}).get("duration", 0))
        except Exception as exc:
            raise ValueError(f"第 {index} 个源视频无法读取时长：{exc}") from exc
        if source_duration <= 0 or end > source_duration + 0.05:
            raise ValueError(
                f"第 {index} 个片段结束时间 {end:.2f}s 超出源视频时长 {source_duration:.2f}s"
            )

        validated.append({
            "file_path": str(path),
            "start": start,
            "end": end,
            "description": str(segment.get("description", ""))[:1000]
        })

    width = int(plan.get("width", 1920))
    height = int(plan.get("height", 1080))

    if not (256 <= width <= 3840 and 256 <= height <= 3840):
        raise ValueError("输出分辨率超出允许范围")

    return {
        "title": str(plan.get("title", "视频剪辑"))[:300],
        "summary": str(plan.get("summary", ""))[:5000],
        "segments": validated,
        "width": width,
        "height": height,
        "subtitle_text": str(plan.get("subtitle_text", ""))[:10000],
        "notes": plan.get("notes", []) if isinstance(plan.get("notes", []), list) else []
    }


def execute_edit_plan(plan, subtitle_path=None, audio_path=None, audio_mode="replace"):
    work = EDIT_DIR / uuid.uuid4().hex
    work.mkdir(parents=True, exist_ok=True)
    output = None

    try:
        clips = []
        for index, segment in enumerate(plan["segments"]):
            output_clip = work / f"segment_{index:04d}.mp4"
            duration = float(segment["end"]) - float(segment["start"])
            args = [
                "-ss", str(segment["start"]),
                "-i", segment["file_path"],
                "-t", str(duration),
                "-vf",
                f"scale={plan['width']}:{plan['height']}:force_original_aspect_ratio=decrease,"
                f"pad={plan['width']}:{plan['height']}:(ow-iw)/2:(oh-ih)/2",
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "21",
                "-c:a", "aac",
                "-ar", "48000",
                "-ac", "2",
                "-avoid_negative_ts", "make_zero",
                "-movflags", "+faststart",
                str(output_clip)
            ]
            run_ffmpeg(args)
            clips.append(output_clip)

        concat_file = work / "concat.txt"
        concat_file.write_text(
            "".join(
                "file '" + str(item).replace("\\", "/").replace("'", "'\\''") + "'\n"
                for item in clips
            ),
            encoding="utf-8"
        )

        output = VIDEO_DIR / f"ai_edit_{uuid.uuid4().hex}.mp4"
        args = ["-f", "concat", "-safe", "0", "-i", str(concat_file)]

        audio = safe_storage_file(audio_path) if audio_path else None
        subtitle = safe_storage_file(subtitle_path) if subtitle_path else None
        if audio:
            if audio_mode not in ("replace", "mix"):
                raise ValueError("音轨模式必须是 replace 或 mix")
            args += ["-i", str(audio)]

        if subtitle:
            escaped = str(subtitle).replace("\\", "/").replace(":", "\\:")
            args += ["-vf", f"subtitles='{escaped}'"]

        if audio and audio_mode == "replace":
            # Replace mode intentionally uses only the selected external track.
            args += ["-map", "0:v:0", "-map", "1:a:0", "-shortest"]
        elif audio and audio_mode == "mix":
            # Mix selected audio with the original clip audio when available.
            streams = probe_media(clips[0]).get("streams", [])
            has_original_audio = any(x.get("codec_type") == "audio" for x in streams)
            if has_original_audio:
                args += [
                    "-filter_complex",
                    "[0:a:0]volume=1.0[original];"
                    "[1:a:0]volume=0.25[music];"
                    "[original][music]amix=inputs=2:duration=first:dropout_transition=2[mix]",
                    "-map", "0:v:0", "-map", "[mix]", "-shortest"
                ]
            else:
                args += ["-map", "0:v:0", "-map", "1:a:0", "-shortest"]
        else:
            args += ["-map", "0:v:0", "-map", "0:a?"]

        args += [
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "20",
            "-c:a", "aac",
            "-movflags", "+faststart",
            str(output)
        ]
        run_ffmpeg(args)
        return str(output), output.stat().st_size
    except Exception:
        if output and output.exists():
            output.unlink()
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
