import hashlib
import json
import shutil
import uuid
import re
import urllib.request
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.media import MediaFile, MediaTask, SubtitleTrack
from app.models.studio import AIUsageLog
from app.routers.media import get_provider
from app.services.local_tts import LocalTtsUnavailable, list_local_sapi_voices, synthesize_local_sapi_cues
from app.routers.local_ai_runtime import MODEL_NAME_PATTERN, _installed_models, _ollama_request
from app.services.media_service import (
    AUDIO_DIR, SUBTITLE_DIR, VIDEO_DIR, probe_media, run_ffmpeg, safe_storage_file,
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


def _subtitle_ownership(db, source_path):
    """Carry project/episode ownership forward when a subtitle is translated or dubbed."""
    normalized = str(source_path)
    track = db.query(SubtitleTrack).filter(SubtitleTrack.file_path == normalized).first()
    if track:
        return track.project_id, track.episode_id
    media = db.query(MediaFile).filter(MediaFile.file_path == normalized).first()
    metadata = media.metadata_json if media and isinstance(media.metadata_json, dict) else {}
    project_id = metadata.get("project_id")
    episode_id = metadata.get("episode_id")
    try:
        project_id = int(project_id) if project_id not in (None, "", 0, "0") else None
    except (TypeError, ValueError):
        project_id = None
    try:
        episode_id = int(episode_id) if episode_id not in (None, "", 0, "0") else None
    except (TypeError, ValueError):
        episode_id = None
    return project_id, episode_id


def _chat_completion(base, key, model, cues, target_language):
    endpoint = base.rstrip("/") + "/chat/completions"
    instructions = {
        "zh": "Translate every subtitle into natural Simplified Chinese.",
        "en": "Translate every subtitle into natural English.",
        "zh-en": "For every subtitle, output two lines: first natural Simplified Chinese, then natural English.",
    }[target_language]
    payload = {
        "model": model,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": (
                "You are a professional subtitle translator. " + instructions +
                " Preserve meaning, names, tone and concise subtitle style. "
                "Return only valid JSON in the form {\"translations\":[{\"id\":1,\"text\":\"...\"}]}. "
                "Do not change IDs, omit items, add commentary, or include timestamps."
            )},
            {"role": "user", "content": json.dumps(
                [{"id": i + 1, "text": cue["text"]} for i, cue in enumerate(cues)],
                ensure_ascii=False,
            )},
        ],
    }
    request = urllib.request.Request(
        endpoint, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            result = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError("字幕翻译服务调用失败：" + str(exc)[:1000]) from exc
    content = (((result.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
    content = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", content, flags=re.I)
    parsed = json.loads(content)
    translations = parsed.get("translations") if isinstance(parsed, dict) else None
    if not isinstance(translations, list) or len(translations) != len(cues):
        raise ValueError("AI 返回的翻译条数不正确，已阻止生成错位字幕")
    mapped = {}
    for row in translations:
        if not isinstance(row, dict) or not isinstance(row.get("id"), int) or not isinstance(row.get("text"), str):
            raise ValueError("AI 返回的字幕翻译结构无效")
        mapped[row["id"]] = row["text"].strip()
    if set(mapped) != set(range(1, len(cues) + 1)) or any(not mapped[i] for i in mapped):
        raise ValueError("AI 返回的字幕编号缺失或文本为空")
    return [mapped[i] for i in range(1, len(cues) + 1)]


class TranslateInput(BaseModel):
    subtitle_path: str
    provider: str
    model: str = Field(min_length=1, max_length=160)
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


class LocalTranslateInput(BaseModel):
    subtitle_path: str
    model: str = Field(min_length=1, max_length=128)
    target_language: Literal["zh", "en", "zh-en"]
    confirm_local_generation: bool = False


class LocalVoiceInput(BaseModel):
    subtitle_path: str
    voice: str = Field(min_length=1, max_length=160)
    rate: int = Field(default=0, ge=-5, le=5)
    confirm_local_generation: bool = False


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
        project_id, episode_id = _subtitle_ownership(db, source)
        config, key, base = get_provider(db, data.provider, data.confirm_paid)
        if getattr(config, "purpose", "text") not in ("text", "translation", "general"):
            raise HTTPException(400, "请选择已配置的文本类 AI 服务商进行字幕翻译")
        translated = []
        for offset in range(0, len(cues), 30):
            batch = cues[offset:offset + 30]
            values = _chat_completion(base, key, data.model, batch, data.target_language)
            translated.extend(values)
        output_cues = [
            {"start": cue["start"], "end": cue["end"], "text": translated[i]}
            for i, cue in enumerate(cues)
        ]
        filename = re.sub(r"[^A-Za-z0-9_\-\u4e00-\u9fff]", "_", data.output_name or (source.stem + "_translated"))
        filename = filename[:140] + "_" + uuid.uuid4().hex[:8] + ".srt"
        destination = SUBTITLE_DIR / filename
        destination.write_text(render_srt(output_cues), encoding="utf-8-sig")
        track = SubtitleTrack(
            name=filename, file_path=str(destination), format="srt",
            language={"zh": "zh-CN", "en": "en", "zh-en": "zh-CN+en"}[data.target_language],
            project_id=project_id, episode_id=episode_id,
        )
        db.add(track)
        db.add(MediaFile(name=filename, category="other", file_path=str(destination),
                         mime_type="application/x-subrip", file_size=destination.stat().st_size,
                         metadata_json={
                             "subtitle_language": data.target_language, "source": str(source),
                             "project_id": project_id, "episode_id": episode_id,
                         }))
        db.add(AIUsageLog(provider=data.provider, model=data.model, task="subtitle_translation",
                          status="completed", detail=f"字幕条数：{len(cues)}；目标语言：{data.target_language}"))
        db.commit()
        return {"success": True, "path": str(destination), "name": filename,
                "cue_count": len(cues), "timecodes_preserved": True,
                "message": f"翻译完成，共 {len(cues)} 条；原字幕时间轴和条目顺序保持不变。"}
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(502, "字幕翻译失败：" + str(exc)[:1500]) from exc


@router.post("/voice")
def create_subtitle_voice(data: VoiceInput, db: Session = Depends(get_db)):
    try:
        subtitle = safe_storage_file(data.subtitle_path)
        if subtitle.suffix.lower() != ".srt":
            raise HTTPException(400, "当前版本的字幕配音仅支持 SRT 文件")
        cues = parse_srt(_read_text(subtitle))
        project_id, episode_id = _subtitle_ownership(db, subtitle)
        config, key, base = get_provider(db, data.provider, data.confirm_paid)
        if getattr(config, "purpose", "text") not in ("text", "audio", "speech"):
            raise HTTPException(400, "请选择支持语音合成的服务商")
        fingerprint = hashlib.sha256(json.dumps({
            "subtitle": str(subtitle), "mtime": subtitle.stat().st_mtime,
            "provider": data.provider, "model": data.model, "voice": data.voice,
            "speed": data.speed,
        }, sort_keys=True).encode()).hexdigest()
        prior = db.query(MediaTask).filter(MediaTask.task_type == "subtitle_dubbing").order_by(MediaTask.id.desc()).all()
        for task in prior:
            meta = task.request_data if isinstance(task.request_data, dict) else {}
            if meta.get("fingerprint") != fingerprint:
                continue
            if task.status == "completed" and task.result_path and Path(task.result_path).is_file():
                return {"success": True, "task_id": task.id, "path": task.result_path,
                        "reused": True, "message": "已复用相同字幕和声音参数的配音文件，没有再次调用 AI。"}
            if task.status in ("pending", "submitting", "submission_unknown", "queued"):
                raise HTTPException(409, f"相同配音任务 #{task.id} 正在处理或等待核实，避免重复扣费，本次未再次提交。")
        task = MediaTask(task_type="subtitle_dubbing", provider=data.provider, model=data.model,
                         status="pending", prompt=f"字幕逐条配音：{subtitle.name}",
                         request_data={"fingerprint": fingerprint, "voice": data.voice,
                                       "speed": data.speed, "cue_count": len(cues),
                                       "subtitle_source": str(subtitle),
                                       "project_id": project_id, "episode_id": episode_id,
                                       "confirm_paid": data.confirm_paid})
        db.add(task)
        db.commit()
        db.refresh(task)
        generated = []
        filters = []
        labels = []
        max_end = max(cue["end"] for cue in cues)
        try:
            task.status = "submitting"
            db.commit()
            for index, cue in enumerate(cues):
                payload = {"model": data.model, "input": cue["text"], "voice": data.voice,
                           "response_format": "mp3", "speed": data.speed}
                request = urllib.request.Request(
                    base + "/audio/speech",
                    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
                    method="POST",
                )
                temp = AUDIO_DIR / f"subtitle_voice_{task.id}_{index}_{uuid.uuid4().hex[:6]}.mp3"
                with urllib.request.urlopen(request, timeout=180) as response, temp.open("wb") as output:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
                if temp.stat().st_size < 128:
                    raise RuntimeError(f"第 {index + 1} 条语音文件无效")
                duration = float((probe_media(temp).get("format") or {}).get("duration") or 0)
                slot = cue["end"] - cue["start"]
                ratio = duration / slot if slot > 0 else 1
                if ratio > 2.0:
                    raise RuntimeError(f"第 {index + 1} 条配音需要超过 2 倍语速才能放入字幕时间窗；请缩短文本或延长字幕时长")
                tempo = f"atempo={max(1.0, ratio):.4f}," if ratio > 1.02 else ""
                delay = int(round(cue["start"] * 1000))
                filters.append(
                    f"[{index}:a:0]aresample=48000,{tempo}apad,atrim=duration={slot:.3f},"
                    f"adelay={delay}|{delay}[v{index}]"
                )
                generated.append(temp)
                labels.append(f"[v{index}]")
            filters.append("".join(labels) + f"amix=inputs={len(labels)}:duration=longest:normalize=0,atrim=duration={max_end:.3f}[voice]")
            args = []
            for path in generated:
                args += ["-i", str(path)]
            filename = f"subtitle_dubbing_{task.id}_{uuid.uuid4().hex[:8]}.mp3"
            destination = AUDIO_DIR / filename
            run_ffmpeg(args + ["-filter_complex", ";".join(filters), "-map", "[voice]",
                               "-t", f"{max_end:.3f}", "-c:a", "libmp3lame", "-q:a", "3", str(destination)])
            task.status = "completed"
            task.result_path = str(destination)
            task.error = None
            db.add(MediaFile(name=filename, category="audio", file_path=str(destination),
                             mime_type="audio/mpeg", file_size=destination.stat().st_size,
                             metadata_json={
                                 "subtitle_source": str(subtitle), "voice": data.voice,
                                 "project_id": project_id, "episode_id": episode_id,
                             }))
            db.add(AIUsageLog(provider=data.provider, model=data.model, task="subtitle_dubbing",
                              status="completed", detail=f"字幕条数：{len(cues)}；语音：{data.voice}"))
            db.commit()
            return {"success": True, "task_id": task.id, "path": str(destination),
                    "cue_count": len(cues), "timeline_aligned": True, "reused": False,
                    "message": "已逐条生成语音并按原字幕时间轴合成配音轨。"}
        except Exception as exc:
            task.status = "submission_unknown"
            task.error = ("配音过程中发生错误；部分语音请求可能已计费，请先核对服务商用量后再重试。 " + str(exc))[:2000]
            db.commit()
            raise
        finally:
            for path in generated:
                path.unlink(missing_ok=True)
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(502, "字幕配音失败：" + str(exc)[:1500]) from exc


@router.post("/translate-local")
def translate_subtitles_locally(data: LocalTranslateInput, db: Session = Depends(get_db)):
    if not data.confirm_local_generation:
        raise HTTPException(400, "本机字幕翻译会占用 CPU；请明确确认后再开始。")
    if not MODEL_NAME_PATTERN.fullmatch(data.model.strip()):
        raise HTTPException(400, "本地模型名称格式无效。")
    source = safe_storage_file(data.subtitle_path)
    if source.suffix.lower() != ".srt":
        raise HTTPException(400, "本机字幕翻译当前仅支持 SRT。")
    cues = parse_srt(_read_text(source))
    project_id, episode_id = _subtitle_ownership(db, source)
    try:
        installed = {item["name"] for item in _installed_models()}
    except HTTPException:
        raise
    if data.model not in installed:
        raise HTTPException(404, "所选模型未安装在本机；不会自动下载模型。")
    translated = []
    language_instruction = {
        "zh": "Translate every subtitle into natural Simplified Chinese.",
        "en": "Translate every subtitle into natural English.",
        "zh-en": "For every subtitle, output two lines: first natural Simplified Chinese, then natural English.",
    }[data.target_language]
    try:
        for offset in range(0, len(cues), 20):
            batch = cues[offset:offset + 20]
            prompt = (
                "You are a professional subtitle translator. " + language_instruction
                + " Preserve meaning, names, tone and concise subtitle style. "
                + "Keep every id unchanged, do not omit, merge, reorder or add entries. "
                + "Return JSON only: {\"translations\":[{\"id\":1,\"text\":\"...\"}]}.\n"
                + json.dumps(
                    [{"id": index + 1, "text": cue["text"]} for index, cue in enumerate(batch, start=offset)],
                    ensure_ascii=False,
                )
            )
            result = _ollama_request("/api/generate", {
                "model": data.model,
                "system": "只翻译字幕，不解释，不修改编号。严格输出 JSON。",
                "prompt": prompt,
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.2, "num_predict": 4096},
            }, timeout=300.0)
            raw = result.get("response") if isinstance(result, dict) else None
            if not isinstance(raw, str) or not raw.strip():
                raise ValueError("本地模型没有返回字幕翻译内容")
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                left, right = raw.find("{"), raw.rfind("}")
                if left < 0 or right <= left:
                    raise ValueError("本地模型返回的翻译 JSON 无效")
                parsed = json.loads(raw[left:right + 1])
            rows = parsed.get("translations") if isinstance(parsed, dict) else None
            if not isinstance(rows, list) or len(rows) != len(batch):
                raise ValueError("本地模型返回的翻译条数不正确")
            mapped = {}
            for row in rows:
                if not isinstance(row, dict):
                    continue
                try:
                    row_id = int(row.get("id"))
                except (TypeError, ValueError):
                    continue
                value = str(row.get("text") or "").strip()
                if value and row_id not in mapped:
                    mapped[row_id] = value
            expected_ids = set(range(offset + 1, offset + len(batch) + 1))
            if set(mapped) != expected_ids:
                raise ValueError("本地模型返回的字幕编号缺失、重复或错位")
            translated.extend(mapped[index] for index in sorted(expected_ids))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, "本机字幕翻译失败：" + str(exc)[:1000]) from exc

    output_cues = [
        {"start": cue["start"], "end": cue["end"], "text": translated[index]}
        for index, cue in enumerate(cues)
    ]
    stem = re.sub(r"[^A-Za-z0-9_\-\u4e00-\u9fff]", "_", source.stem + "_local_translated")[:140]
    filename = stem + "_" + uuid.uuid4().hex[:8] + ".srt"
    destination = SUBTITLE_DIR / filename
    destination.write_text(render_srt(output_cues), encoding="utf-8-sig")
    track = SubtitleTrack(
        name=filename,
        file_path=str(destination),
        format="srt",
        language={"zh": "zh-CN", "en": "en", "zh-en": "zh-CN+en"}[data.target_language],
        project_id=project_id,
        episode_id=episode_id,
    )
    media_record = MediaFile(
        name=filename,
        category="other",
        file_path=str(destination),
        mime_type="application/x-subrip",
        file_size=destination.stat().st_size,
        metadata_json={
            "subtitle_language": data.target_language,
            "source": str(source),
            "local_model": data.model,
            "project_id": project_id,
            "episode_id": episode_id,
            "network_used": False,
        },
    )
    db.add(track)
    db.add(media_record)
    db.add(AIUsageLog(
        provider="ollama-local",
        model=data.model,
        task="subtitle_translation_local",
        status="completed",
        detail=f"字幕条数：{len(cues)}；目标语言：{data.target_language}；本机离线处理",
    ))
    db.flush()
    db.commit()
    return {
        "success": True,
        "path": str(destination),
        "name": filename,
        "media_file_id": media_record.id,
        "download_url": f"/api/media/files/{media_record.id}/download",
        "cue_count": len(cues),
        "timecodes_preserved": True,
        "network_used": False,
        "paid_api_used": False,
        "message": f"已使用本机模型 {data.model} 翻译 {len(cues)} 条字幕；原时间轴和顺序保持不变，没有调用云端 API。",
    }


@router.get("/local-voices")
def list_local_subtitle_voices():
    try:
        voices = list_local_sapi_voices()
    except LocalTtsUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    return {
        "engine": "windows-sapi",
        "voices": voices,
        "voice_count": len(voices),
        "network_used": False,
        "paid_api_used": False,
        "message": "已读取本机 Windows 离线语音列表；不会访问网络或下载语音包。",
    }


@router.post("/voice-local")
def create_local_subtitle_voice(data: LocalVoiceInput, db: Session = Depends(get_db)):
    if not data.confirm_local_generation:
        raise HTTPException(400, "本机离线配音会占用 CPU；请明确确认后再开始。")
    job_dir = None
    destination = None
    try:
        source = safe_storage_file(data.subtitle_path)
        if source.suffix.lower() != ".srt":
            raise HTTPException(400, "本机离线配音当前仅支持 SRT 字幕。")
        cues = parse_srt(_read_text(source))
        project_id, episode_id = _subtitle_ownership(db, source)
        job_dir, segment_paths = synthesize_local_sapi_cues(cues, data.voice, data.rate, AUDIO_DIR)
        generated = []
        filters = []
        labels = []
        max_end = max(cue["end"] for cue in cues)
        for index, (cue, path) in enumerate(zip(cues, segment_paths)):
            info = probe_media(path)
            duration = float((info.get("format") or {}).get("duration") or 0)
            slot = cue["end"] - cue["start"]
            ratio = duration / slot if slot > 0 else 1.0
            if duration <= 0 or ratio > 2.0:
                raise HTTPException(422, f"第 {index + 1} 条本机语音无法在字幕时间窗内自然播放；请缩短字幕或延长时间轴。")
            tempo = f"atempo={max(1.0, ratio):.4f}," if ratio > 1.02 else ""
            delay = int(round(cue["start"] * 1000))
            filters.append(
                f"[{index}:a:0]aresample=48000,{tempo}apad,atrim=duration={slot:.3f},"
                f"adelay={delay}|{delay}[v{index}]"
            )
            generated.append(path)
            labels.append(f"[v{index}]")
        filters.append(
            "".join(labels)
            + f"amix=inputs={len(labels)}:duration=longest:normalize=0,atrim=duration={max_end:.3f}[voice]"
        )
        args = []
        for path in generated:
            args += ["-i", str(path)]
        filename = f"subtitle_local_voice_{uuid.uuid4().hex[:12]}.mp3"
        destination = AUDIO_DIR / filename
        filter_script = job_dir / "mix-filter.txt"
        filter_script.write_text(";".join(filters), encoding="utf-8")
        run_ffmpeg(args + [
            "-filter_complex_script", str(filter_script), "-map", "[voice]",
            "-t", f"{max_end:.3f}", "-c:a", "libmp3lame", "-q:a", "3", str(destination),
        ])
        info = probe_media(destination)
        duration = float((info.get("format") or {}).get("duration") or 0)
        if duration <= 0:
            raise RuntimeError("本机配音合成结果时长无效")
        task = MediaTask(
            task_type="subtitle_dubbing_local",
            provider="windows-sapi",
            model=data.voice,
            status="completed",
            prompt=f"本机离线字幕配音：{source.name}",
            request_data={
                "subtitle_source": str(source),
                "project_id": project_id,
                "episode_id": episode_id,
                "voice": data.voice,
                "rate": data.rate,
                "cue_count": len(cues),
                "subtitle_cues": [
                    {"start": cue["start"], "end": cue["end"], "text": cue["text"]}
                    for cue in cues
                ],
                "network_used": False,
                "paid_api_used": False,
            },
            result_path=str(destination),
        )
        db.add(task)
        media_record = MediaFile(
            name=filename,
            category="audio",
            file_path=str(destination),
            mime_type="audio/mpeg",
            file_size=destination.stat().st_size,
            duration=duration,
            metadata_json={
                "subtitle_source": str(source),
                "project_id": project_id,
                "episode_id": episode_id,
                "voice": data.voice,
                "local_tts": True,
                "network_used": False,
            },
        )
        db.add(media_record)
        db.flush()
        db.add(AIUsageLog(
            provider="windows-sapi",
            model=data.voice,
            task="subtitle_dubbing_local",
            status="completed",
            detail=f"字幕条数：{len(cues)}；本机离线合成；语速：{data.rate}",
        ))
        db.commit()
        return {
            "success": True,
            "task_id": task.id,
            "path": str(destination),
            "name": filename,
            "media_file_id": media_record.id,
            "download_url": f"/api/media/files/{media_record.id}/download",
            "cue_count": len(cues),
            "duration_seconds": duration,
            "timeline_aligned": True,
            "network_used": False,
            "paid_api_used": False,
            "message": "已使用 Windows 本机离线语音生成配音，并按原字幕时间轴合成；没有调用云端 TTS。",
        }
    except HTTPException:
        if destination:
            destination.unlink(missing_ok=True)
        raise
    except LocalTtsUnavailable as exc:
        if destination:
            destination.unlink(missing_ok=True)
        raise HTTPException(503, str(exc)) from exc
    except Exception as exc:
        db.rollback()
        if destination:
            destination.unlink(missing_ok=True)
        raise HTTPException(500, "本机离线配音失败：" + str(exc)[:1200]) from exc
    finally:
        if job_dir:
            shutil.rmtree(job_dir, ignore_errors=True)


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
