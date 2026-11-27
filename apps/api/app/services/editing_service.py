import json
import re
import uuid
import math
import shutil
import subprocess
import urllib.request
import urllib.error
from pathlib import Path

from app.services.media_service import (
    STORAGE, VIDEO_DIR, safe_storage_file, run_ffmpeg, probe_media
)

from app.services.cost_safety import require_cloud_ai_api_disabled

EDIT_DIR = STORAGE / "edit-jobs"
EDIT_DIR.mkdir(parents=True, exist_ok=True)


def decrypt_key(config):
    from app.services.media_service import decrypt_api_key

    encrypted = (
        getattr(config, "api_key_encrypted", None)
        or getattr(config, "encrypted_api_key", None)
    )
    if not encrypted:
        raise RuntimeError("AI 服务商没有保存 API Key")
    return decrypt_api_key(encrypted)


def get_ai_config(db, provider_name):
    from app.models.studio import AIProviderConfig, AIRuntimeSetting

    config = db.query(AIProviderConfig).filter(
        AIProviderConfig.provider == provider_name
    ).first()

    if not config:
        raise RuntimeError(f"AI 服务商 {provider_name} 尚未配置")

    if not getattr(config, "enabled", False):
        raise RuntimeError("该 AI 服务商尚未启用")

    runtime = db.query(AIRuntimeSetting).first()
    if not runtime or not getattr(runtime, "network_enabled", False):
        raise RuntimeError("全局网络调用开关已关闭")

    # 只有明确标记为付费的服务才要求付费总授权。
    # 缺失计费信息时默认按付费处理，避免未知费用被放行。
    paid = bool(getattr(config, "paid", True))
    if paid and not getattr(runtime, "paid_api_allowed", False):
        raise RuntimeError("付费 API 总授权未开启")

    base_url = getattr(config, "base_url", None)
    if not base_url:
        raise RuntimeError("AI 服务商缺少 Base URL")

    return config, decrypt_key(config), base_url.rstrip("/")


def call_ai_for_edit_plan(db, provider, model, instruction, source_files,
                          confirm_paid=False):
    # This desktop build is strict zero-cost: cloud API editing is blocked.
    # Local Ollama analysis and explicitly user-driven browser handoff remain separate.
    require_cloud_ai_api_disabled()
    config, key, base = get_ai_config(db, provider)

    if bool(getattr(config, "paid", True)) and not confirm_paid:
        raise RuntimeError("本次 AI 剪辑分析可能产生费用，请先明确确认")

    system_prompt = """
你是专业影视剪辑师。你必须把用户的剪辑要求转换成严格 JSON。
只能使用用户提供的视频文件路径，不得编造路径。
只返回 JSON，不要 Markdown，不要解释。

JSON 格式：
{
  "title": "剪辑项目名称",
  "summary": "剪辑方案说明",
  "segments": [
    {
      "file_path": "必须是输入文件中的路径",
      "start": 0,
      "end": 10,
      "description": "该片段的用途"
    }
  ],
  "width": 1920,
  "height": 1080,
  "subtitle_text": "",
  "notes": []
}

规则：
1. start 和 end 是视频原始时间，单位秒。
2. end 必须大于 start。
3. 片段必须来自用户提供的文件。
4. 按用户要求排序 segments。
5. 不要编造画面内容；无法判断的内容写入 notes。
6. 用户没有要求删除内容时，尽量保留相关内容。
7. 不要在 JSON 外输出任何文字。
"""

    user_prompt = (
        "用户剪辑要求：\n" + instruction +
        "\n\n允许使用的视频文件：\n" +
        json.dumps(source_files, ensure_ascii=False)
    )

    payload = {
        "model": model,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
    }

    request = urllib.request.Request(
        base + "/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json"
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"AI 剪辑分析 API 错误 HTTP {exc.code}: {detail[:2000]}")
    except Exception as exc:
        raise RuntimeError(f"AI 剪辑分析请求失败：{exc}")

    content = (
        result.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
    )

    if isinstance(content, list):
        content = "".join(
            part.get("text", "") for part in content
            if isinstance(part, dict)
        )

    content = str(content).strip()
    content = re.sub(r"^```(?:json)?\s*", "", content)
    content = re.sub(r"\s*```$", "", content)

    try:
        plan = json.loads(content)
    except Exception as exc:
        raise RuntimeError("AI 返回的剪辑方案不是有效 JSON：" + content[:1500]) from exc

    if not isinstance(plan, dict) or not isinstance(plan.get("segments"), list):
        raise RuntimeError("AI 返回的剪辑方案缺少 segments")

    return plan, result


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
