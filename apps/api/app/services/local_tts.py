"""Offline Windows SAPI text-to-speech helpers.

This module talks only to the Windows System.Speech assembly installed with
Windows. It never calls a cloud provider and never downloads a voice/model.
"""
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


POWERSHELL_SCRIPT = r"""
param(
    [string]$InputJson = "",
    [string]$OutputDirectory = "",
    [string]$VoiceName = "",
    [int]$Rate = 0,
    [switch]$ListVoices
)
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    if ($ListVoices) {
        $voices = @($synth.GetInstalledVoices() | Where-Object { $_.Enabled } | ForEach-Object {
            [pscustomobject]@{
                name = $_.VoiceInfo.Name
                culture = $_.VoiceInfo.Culture.Name
                gender = [string]$_.VoiceInfo.Gender
                age = [string]$_.VoiceInfo.Age
            }
        })
        ConvertTo-Json -InputObject $voices -Depth 4 -Compress
        exit 0
    }
    if (-not (Test-Path -LiteralPath $InputJson -PathType Leaf)) { throw "Input JSON not found" }
    if (-not (Test-Path -LiteralPath $OutputDirectory -PathType Container)) { throw "Output directory not found" }
    $voice = $synth.GetInstalledVoices() | Where-Object { $_.Enabled -and $_.VoiceInfo.Name -ceq $VoiceName } | Select-Object -First 1
    if (-not $voice) { throw "Requested Windows speech voice is not installed" }
    $synth.SelectVoice($VoiceName)
    $synth.Rate = $Rate
    $synth.Volume = 100
    $payload = Get-Content -LiteralPath $InputJson -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($cue in $payload.cues) {
        $index = [int]$cue.index
        $text = [string]$cue.text
        if ([string]::IsNullOrWhiteSpace($text)) { throw "Empty speech cue" }
        $output = Join-Path $OutputDirectory ("cue-" + $index + ".wav")
        $synth.SetOutputToWaveFile($output)
        $synth.Speak($text)
    }
    $synth.SetOutputToNull()
    Write-Output "SAPI_SYNTHESIS_COMPLETE"
}
finally {
    $synth.Dispose()
}
"""


class LocalTtsUnavailable(RuntimeError):
    pass


def _powershell_executable() -> str:
    if os.name != "nt":
        raise LocalTtsUnavailable("Windows 离线语音仅支持 Windows 桌面版。")
    executable = shutil.which("powershell.exe") or shutil.which("powershell")
    if not executable:
        raise LocalTtsUnavailable("找不到 Windows PowerShell，无法使用系统离线语音。")
    return executable


def _run_script(arguments, timeout: int):
    executable = _powershell_executable()
    with tempfile.TemporaryDirectory(prefix="ai-comic-sapi-") as temporary:
        script = Path(temporary) / "local-sapi.ps1"
        script.write_text(POWERSHELL_SCRIPT, encoding="utf-8-sig")
        command = [
            executable, "-NoLogo", "-NoProfile", "-NonInteractive",
            "-ExecutionPolicy", "Bypass", "-File", str(script), *arguments,
        ]
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "Windows SAPI 执行失败").strip()
            raise LocalTtsUnavailable(detail[-1200:])
        return result.stdout.strip()


def list_local_sapi_voices():
    output = _run_script(["-ListVoices"], timeout=20)
    try:
        payload = json.loads(output or "[]")
    except json.JSONDecodeError as exc:
        raise LocalTtsUnavailable("Windows 系统语音列表返回格式无效。") from exc
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list):
        raise LocalTtsUnavailable("Windows 系统语音列表格式无效。")
    voices = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        culture = item.get("culture")
        if isinstance(name, str) and name.strip() and isinstance(culture, str):
            voices.append({
                "name": name.strip()[:160],
                "culture": culture[:40],
                "gender": str(item.get("gender") or "")[:30],
                "age": str(item.get("age") or "")[:30],
            })
    return voices


def synthesize_local_sapi_cues(cues, voice_name: str, rate: int, output_root: Path):
    if not isinstance(voice_name, str) or not voice_name.strip() or len(voice_name) > 160:
        raise ValueError("请选择有效的 Windows 系统语音。")
    if isinstance(rate, bool) or not isinstance(rate, int) or not -5 <= rate <= 5:
        raise ValueError("语速范围必须为 -5 到 5。")
    if not isinstance(cues, list) or not cues or len(cues) > 500:
        raise ValueError("字幕条数必须为 1 到 500 条。")
    normalized = []
    for index, cue in enumerate(cues, 1):
        text = str(cue.get("text") or "").strip()
        if not text or len(text) > 2000:
            raise ValueError(f"第 {index} 条字幕为空或超过 2000 字。")
        normalized.append({"index": index, "text": text})
    output_root.mkdir(parents=True, exist_ok=True)
    job_dir = Path(tempfile.mkdtemp(prefix="subtitle-sapi-", dir=output_root))
    input_json = job_dir / "cues.json"
    input_json.write_text(json.dumps({"cues": normalized}, ensure_ascii=False), encoding="utf-8")
    try:
        _run_script([
            "-InputJson", str(input_json),
            "-OutputDirectory", str(job_dir),
            "-VoiceName", voice_name.strip(),
            "-Rate", str(rate),
        ], timeout=max(60, min(900, len(normalized) * 12)))
        outputs = []
        for item in normalized:
            path = job_dir / f"cue-{item['index']}.wav"
            if not path.is_file() or path.stat().st_size < 128:
                raise LocalTtsUnavailable(f"Windows 系统语音没有生成第 {item['index']} 条有效音频。")
            with path.open("rb") as audio:
                header = audio.read(12)
            if header[:4] != b"RIFF" or header[8:12] != b"WAVE":
                raise LocalTtsUnavailable(f"第 {item['index']} 条语音不是有效 WAV 文件。")
            outputs.append(path)
        return job_dir, outputs
    except Exception:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise
