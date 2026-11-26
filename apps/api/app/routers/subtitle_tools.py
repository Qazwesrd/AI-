import json
import re
import uuid
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.media import MediaFile, SubtitleTrack
from app.models.studio import AIUsageLog
from app.services.media_service import (
    SUBTITLE_DIR, VIDEO_DIR, probe_media, run_ffmpeg, safe_storage_file,
    validate_rendered_video,
)

router = APIRouter(prefix="/api/subtitle-tools", tags=["字幕翻译与配音"])

SRT_BLOCK = re.compile(
    r"(?P<index>\d+)\s*\r?\n"
    r"(?P<start>\d{2}:\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*"
    r"(?P<end>\d{2}:\d{2}:\d{2}[,.]\d{1,3})[^\r\n]*\r?\n"
    r"(?P<text>.*?)(?=\r?\n\s*\r?\n|\Z)", re.S
)


def parse_srt(content: str):
    normalized = content.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").strip()
    cues = []
    for match in SRT_BLOCK.finditer(normalized):
        start = parse_time(match.group("start"))
        end = parse_time(match.group("end"))
        text = match.group("text").strip()
        if end <= start or not text:
            raise ValueError(f"字幕序号 {match.group('index')} 的时间或文本无效")
        cues.append({"index": int(match.group("index")), "start": start, "end": end, "text": text})
    if not cues:
        raise ValueError("无法解析 SRT 文件，请确认文件包含标准时间轴和字幕文本")
    if len(cues) > 500:
        raise ValueError("单个字幕文件最多处理 500 条字幕，请拆分后再翻译或配音")
    return cues


def parse_time(value: str) -> float:
    hours, minutes, seconds = value.replace(",", ".").split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def format_time(seconds: float) -> str:
    milliseconds = int(round(seconds * 1000))
    hours, milliseconds = divmod(milliseconds, 3600000)
    minutes, milliseconds = divmod(milliseconds, 60000)
    secs, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{milliseconds:03}"


def render_srt(cues):
    rows = []
    for i, cue in enumerate(cues, 1):
        rows.extend([str(i), f"{format_time(cue['start'])} --> {format_time(cue['end'])}", cue["text"], ""])
    return "\ufeff" + "\n".join(rows)


def _read_text(path: Path):
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("字幕文件编码无法识别，请另存为 UTF-8 格式")


def _subtitle_local_model():
    from app.routers.local_ai_runtime import _installed_models

    installed = _installed_models()
    names = [item["name"] for item in installed if isinstance(item, dict) and item.get("name")]
    for preferred in ("qwen3.5:4b", "qwen3:4b"):
        if preferred in names:
            return preferred
    raise HTTPException(503, "没有已安装的 Qwen 本地文本模型；离线字幕翻译不会联网下载。")


def _chat_completion(model, cues, target_language):
    from app.routers.local_ai_runtime import _ollama_request

    instructions = {
        "zh": "Translate every subtitle into natural Simplified Chinese.",
        "en": "Translate every subtitle into natural English.",
        "zh-en": "For every subtitle, output two lines: first natural Simplified Chinese, then natural English.",
    }[target_language]
    payload = {
        "model": model,
        "system": (
            "You are a professional subtitle translator. " + instructions +
            " Preserve meaning, names, tone and concise subtitle style. "
            "Return only valid JSON with a translations array; each item has id and text."
        ),
        "prompt": json.dumps(
            [{"id": i + 1, "text": cue["text"]} for i, cue in enumerate(cues)],
            ensure_ascii=False,
        ),
        "stream": False,
        "format": "json",
        "think": False,
        "options": {"temperature": 0.1, "num_ctx": 8192, "num_predict": 4096},
    }
    try:
        result = _ollama_request("/api/generate", payload, timeout=600.0)
    except HTTPException:
        raise
    except Exception as exc:
        raise RuntimeError("本地字幕翻译失败：" + str(exc)[:1000]) from exc
    content = result.get("response") if isinstance(result, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("本地模型未返回字幕翻译内容")
    parsed = json.loads(content.strip())
    translations = parsed.get("translations") if isinstance(parsed, dict) else None
    if not isinstance(translations, list) or len(translations) != len(cues):
        raise ValueError("本地模型返回的翻译条数不正确，已阻止生成错位字幕")
    mapped = {}
    for row in translations:
        if not isinstance(row, dict) or not isinstance(row.get("id"), int) or not isinstance(row.get("text"), str):
            raise ValueError("本地模型返回的字幕翻译结构无效")
        mapped[row["id"]] = row["text"].strip()
    if set(mapped) != set(range(1, len(cues) + 1)) or any(not mapped[i] for i in mapped):
        raise ValueError("本地模型返回的字幕编号缺失或文本为空")
    return [mapped[i] for i in range(1, len(cues) + 1)]


class TranslateInput(BaseModel):
    subtitle_path: str
    provider: str = "ollama-local"
    model: str = "auto"
    target_language: Literal["zh", "en", "zh-en"]
    confirm_paid: bool = False
    output_name: Optional[str] = Field(default=None, max_length=180)


class VoiceInput(BaseModel):
    subtitle_path: str
    provider: str
    model: str = Field(min_length=1, max_length=160)
    voice: str = Field(default="alloy", min_length=1, max_length=120)
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    confirm_paid: bool = False


class StyledRenderInput(BaseModel):
    video_path: str
    subtitle_path: str
    output_name: str = Field(default="字幕样式成片.mp4", max_length=180)
    font_name: str = Field(default="Arial", pattern=r"^[A-Za-z0-9 _-]{1,40}$")
    font_size: int = Field(default=28, ge=12, le=72)
    primary_color: str = Field(default="&H00FFFFFF", pattern=r"^&H[0-9A-Fa-f]{8}$")
    outline_color: str = Field(default="&H00000000", pattern=r"^&H[0-9A-Fa-f]{8}$")
    outline: float = Field(default=2.0, ge=0, le=8)
    shadow: float = Field(default=1.0, ge=0, le=8)
    alignment: int = Field(default=2, ge=1, le=9)
    margin_v: int = Field(default=40, ge=0, le=300)


@router.post("/translate")
def translate_subtitles(data: TranslateInput, db: Session = Depends(get_db)):
    try:
        source = safe_storage_file(data.subtitle_path)
        if source.suffix.lower() != ".srt":
            raise HTTPException(400, "当前版本的 AI 字幕翻译仅支持 SRT；请先将 ASS/SSA/VTT 转换为 SRT")
        cues = parse_srt(_read_text(source))
        local_model = _subtitle_local_model()
        translated = []
        for offset in range(0, len(cues), 10):
            batch = cues[offset:offset + 10]
            values = _chat_completion(local_model, batch, data.target_language)
            translated.extend(values)
        output_cues = [
            {"start": cue["start"], "end": cue["end"], "text": translated[i]}
            for i, cue in enumerate(cues)
        ]
        filename = re.sub(r"[^A-Za-z0-9_\-\u4e00-\u9fff]", "_", data.output_name or (source.stem + "_translated"))
        filename = filename[:140] + "_" + uuid.uuid4().hex[:8] + ".srt"
        destination = SUBTITLE_DIR / filename
        destination.write_text(render_srt(output_cues), encoding="utf-8-sig")
        track = SubtitleTrack(name=filename, file_path=str(destination), format="srt",
                              language={"zh": "zh-CN", "en": "en", "zh-en": "zh-CN+en"}[data.target_language])
        db.add(track)
        db.add(MediaFile(name=filename, category="other", file_path=str(destination),
                         mime_type="application/x-subrip", file_size=destination.stat().st_size,
                         metadata_json={"subtitle_language": data.target_language, "source": str(source)}))
        db.add(AIUsageLog(provider="ollama-local", model=local_model, task="subtitle_translation",
                          status="completed", detail=f"字幕条数：{len(cues)}；目标语言：{data.target_language}"))
        db.commit()
        return {"success": True, "path": str(destination), "name": filename,
                "cue_count": len(cues), "timecodes_preserved": True, "provider": "ollama-local", "model": local_model,
                "message": f"翻译完成，共 {len(cues)} 条；原字幕时间轴和条目顺序保持不变。"}
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(502, "字幕翻译失败：" + str(exc)[:1500]) from exc


@router.post("/voice")
def create_subtitle_voice(data: VoiceInput, db: Session = Depends(get_db)):
    # No local TTS voice model is shipped in the current offline resource bundle.
    # Explicitly refuse rather than contacting a remote provider or creating a billable task.
    raise HTTPException(
        503,
        "离线版暂未包含本地 TTS 配音模型；为避免产生费用，本功能不会调用任何云端语音服务。"
    )


@router.post("/render")
def render_styled_subtitles(data: StyledRenderInput, db: Session = Depends(get_db)):
    output = None
    try:
        video = safe_storage_file(data.video_path)
        subtitle = safe_storage_file(data.subtitle_path)
        if subtitle.suffix.lower() != ".srt":
            raise HTTPException(400, "字幕样式烧录当前支持 SRT 文件")
        info = probe_media(video)
        if not any(stream.get("codec_type") == "video" for stream in info.get("streams", [])):
            raise HTTPException(400, "所选文件不是有效视频")
        output_name = re.sub(r"[^A-Za-z0-9_\-\u4e00-\u9fff]", "_", data.output_name)[:140]
        if not output_name.lower().endswith(".mp4"):
            output_name += ".mp4"
        output = VIDEO_DIR / f"{uuid.uuid4().hex}_{output_name}"
        subtitle_filter_path = str(subtitle).replace("\\", "/").replace(":", "\\:").replace("'", "\\'").replace(",", "\\,")
        style = (
            f"FontName={data.font_name},FontSize={data.font_size},"
            f"PrimaryColour={data.primary_color},OutlineColour={data.outline_color},"
            f"Outline={data.outline},Shadow={data.shadow},Alignment={data.alignment},MarginV={data.margin_v}"
        )
        vf = f"subtitles=filename='{subtitle_filter_path}':force_style='{style}'"
        run_ffmpeg(["-i", str(video), "-vf", vf, "-map", "0:v:0", "-map", "0:a?",
                    "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                    "-c:a", "aac", "-movflags", "+faststart", str(output)])
        quality_report = validate_rendered_video(output, require_audio=False)
        db.add(MediaFile(name=output.name, category="video", file_path=str(output),
                         mime_type="video/mp4", file_size=output.stat().st_size,
                         duration=quality_report["duration_seconds"],
                         metadata_json={"subtitle_source": str(subtitle), "styled_render": True,
                                        "quality_checked": True, "quality_report": quality_report}))
        db.commit()
        return {"success": True, "path": str(output), "size": output.stat().st_size,
                "quality_report": quality_report,
                "message": "字幕样式已烧录到视频，可在导出文件中查看。"}
    except HTTPException:
        raise
    except Exception as exc:
        if output:
            output.unlink(missing_ok=True)
        raise HTTPException(500, "字幕样式导出失败：" + str(exc)[:1500]) from exc
