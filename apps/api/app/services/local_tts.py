"""Offline Windows SAPI text-to-speech helpers.

Uses the Windows System.Speech runtime already present on supported Windows
installations. No network access, model download, or paid service is involved.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any


class LocalTtsUnavailable(RuntimeError):
    """Raised when Windows SAPI or a requested voice is unavailable."""


def _powershell(script: str, timeout: int = 120) -> str:
    if os.name != "nt":
        raise LocalTtsUnavailable("本机离线语音当前仅支持 Windows 系统。")
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    command = [
        "powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
        "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded,
    ]
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LocalTtsUnavailable(f"无法启动 Windows 离线语音引擎：{exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "未知错误").strip()
        raise LocalTtsUnavailable("Windows 离线语音引擎执行失败：" + detail[-1200:])
    return completed.stdout.strip()


def list_local_sapi_voices() -> list[dict[str, str]]:
    script = r"""
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
  $items = @($s.GetInstalledVoices() | Where-Object { $_.Enabled } | ForEach-Object {
    $v = $_.VoiceInfo
    [pscustomobject]@{
      name = $v.Name
      culture = $v.Culture.Name
      gender = [string]$v.Gender
      age = [string]$v.Age
    }
  })
  ConvertTo-Json -InputObject $items -Compress -Depth 4
} finally { $s.Dispose() }
"""
    output = _powershell(script)
    if not output:
        return []
    try:
        parsed: Any = json.loads(output)
    except json.JSONDecodeError as exc:
        raise LocalTtsUnavailable("Windows 语音列表返回格式无效。") from exc
    if isinstance(parsed, dict):
        parsed = [parsed]
    if not isinstance(parsed, list):
        raise LocalTtsUnavailable("Windows 语音列表格式无效。")
    return [
        {
            "name": str(item.get("name", "")),
            "culture": str(item.get("culture", "")),
            "gender": str(item.get("gender", "")),
            "age": str(item.get("age", "")),
        }
        for item in parsed
        if isinstance(item, dict) and item.get("name")
    ]


def synthesize_local_sapi_cues(
    cues: list[dict[str, Any]],
    voice: str,
    rate: int,
    output_root: Path,
) -> tuple[Path, list[Path]]:
    if not voice or len(voice) > 160:
        raise ValueError("请选择有效的 Windows 离线语音。")
    if not -5 <= int(rate) <= 5:
        raise ValueError("语音速度必须在 -5 到 5 之间。")
    if not cues:
        raise ValueError("没有可配音的字幕内容。")

    voices = list_local_sapi_voices()
    if voice not in {item["name"] for item in voices}:
        raise LocalTtsUnavailable(
            f"本机没有安装语音“{voice}”。请在 Windows 语音设置中安装对应的离线语音。"
        )

    job_dir = Path(tempfile.mkdtemp(prefix="sapi-subtitles-", dir=str(output_root)))
    segment_paths: list[Path] = []
    for index, cue in enumerate(cues, 1):
        text = str(cue.get("text", "")).strip()
        if not text:
            raise ValueError(f"第 {index} 条字幕内容为空。")
        target = job_dir / f"cue-{index}.wav"
        # Serialize user-provided text as JSON, then Base64, to avoid embedding
        # untrusted subtitle text or voice names into executable PowerShell.
        payload = base64.b64encode(json.dumps({
            "text": text, "voice": voice, "rate": int(rate),
            "output": str(target),
        }, ensure_ascii=False).encode("utf-8")).decode("ascii")
        script = r"""
Add-Type -AssemblyName System.Speech
$p = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__PAYLOAD__')) | ConvertFrom-Json
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
  $s.SelectVoice([string]$p.voice)
  $s.Rate = [int]$p.rate
  $s.SetOutputToWaveFile([string]$p.output)
  $s.Speak([string]$p.text)
  $s.SetOutputToNull()
} finally { $s.Dispose() }
""".replace("__PAYLOAD__", payload)
        try:
            _powershell(script, timeout=max(120, min(600, len(text) * 2)))
        except Exception:
            # The caller owns job_dir cleanup once this function returns; clean
            # it here on partial synthesis failures to avoid orphaned audio.
            import shutil
            shutil.rmtree(job_dir, ignore_errors=True)
            raise
        if not target.is_file() or target.stat().st_size < 44:
            import shutil
            shutil.rmtree(job_dir, ignore_errors=True)
            raise LocalTtsUnavailable(f"第 {index} 条字幕没有生成有效的 WAV 音频。")
        segment_paths.append(target)
    return job_dir, segment_paths
