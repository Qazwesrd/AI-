param(
    [string]$ResourceRoot = "E:\123\ai-comic-studio-desktop-Data\engines",
    [string]$OutputDirectory = (Join-Path (Split-Path -Parent $PSScriptRoot) "dist\ai-resources")
)

$ErrorActionPreference = "Stop"

function Require-Path([string]$Path, [string]$Description) {
    if (!(Test-Path -LiteralPath $Path)) {
        throw "AI resource bundle missing $Description : $Path"
    }
}

function Get-FirstLine([string]$Executable, [string[]]$Arguments) {
    $output = & $Executable @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to inspect runtime: $Executable"
    }
    return (($output | Select-Object -First 1) -join "").Trim()
}

$ollamaRoot = Join-Path $ResourceRoot "Ollama"
$comfyRoot = Join-Path $ResourceRoot "ComfyUI"
$ffmpegRoot = Join-Path $ResourceRoot "FFmpeg"
$ollamaExe = Join-Path $ollamaRoot "runtime\ollama.exe"
$ollamaModels = Join-Path $ollamaRoot "models"
$comfyPython = Join-Path $comfyRoot "python_embeded\python.exe"
$comfyMain = Join-Path $comfyRoot "ComfyUI\main.py"
$comfyVenvPython = Join-Path $comfyRoot "venv\Scripts\python.exe"
$comfyVenvMain = Join-Path $comfyRoot "main.py"
$ffmpegExe = Join-Path $ffmpegRoot "ffmpeg.exe"
$ffprobeExe = Join-Path $ffmpegRoot "ffprobe.exe"

Require-Path $ollamaExe "Ollama Windows runtime"
Require-Path (Join-Path $ollamaModels "blobs") "Ollama model blobs"
Require-Path (Join-Path $ollamaModels "manifests-v2") "Ollama model manifests"
Require-Path $ffmpegExe "FFmpeg executable"
Require-Path $ffprobeExe "FFprobe executable"

$comfyPythonSelected = $null
$comfyMainSelected = $null
if ((Test-Path $comfyPython) -and (Test-Path $comfyMain)) {
    $comfyPythonSelected = $comfyPython
    $comfyMainSelected = $comfyMain
} elseif ((Test-Path $comfyVenvPython) -and (Test-Path $comfyVenvMain)) {
    $comfyPythonSelected = $comfyVenvPython
    $comfyMainSelected = $comfyVenvMain
} else {
    throw "ComfyUI portable runtime is incomplete. Expected python_embeded\python.exe + ComfyUI\main.py or venv\Scripts\python.exe + main.py."
}

$checkpointDirectory = Join-Path $comfyRoot "ComfyUI\models\checkpoints"
if (!(Test-Path $checkpointDirectory)) {
    $checkpointDirectory = Join-Path $comfyRoot "models\checkpoints"
}
Require-Path $checkpointDirectory "ComfyUI checkpoint directory"
$checkpoints = @(Get-ChildItem -LiteralPath $checkpointDirectory -File | Where-Object {
    $_.Extension -in @(".safetensors", ".ckpt")
})
if ($checkpoints.Count -lt 1) {
    throw "No local image checkpoint found. The image workbench cannot generate images without model weights."
}

$qwenManifest = Get-ChildItem -LiteralPath (Join-Path $ollamaModels "manifests-v2") -File -Recurse |
    Where-Object { $_.FullName -match "qwen3\.5[\\/]4b$" } |
    Select-Object -First 1
if (!$qwenManifest) {
    throw "Qwen3.5:4b is not present in the offline Ollama model manifest. Existing Qwen3:4b is preserved, but the current target model must be staged before packaging."
}

$ollamaVersion = Get-FirstLine $ollamaExe @("--version")
if ($ollamaVersion -notmatch "0\.40\.18") {
    throw "Expected Ollama 0.40.18; found: $ollamaVersion"
}
$ffmpegVersion = Get-FirstLine $ffmpegExe @("-version")
if ($ffmpegVersion -notmatch "ffmpeg version 9\.1") {
    throw "Expected FFmpeg 9.1; found: $ffmpegVersion"
}

$manifestPath = Join-Path $PSScriptRoot "..\apps\desktop\build-resources\ai-resources\resource-manifest.json"
Require-Path $manifestPath "versioned resource manifest"

New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$staging = Join-Path $env:TEMP ("ai-comic-resource-bundle-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $staging -Force | Out-Null
$zipPath = Join-Path $OutputDirectory "AI漫剧大师-AI离线资源包-2026.11.zip"

try {
    Copy-Item -LiteralPath $ollamaRoot -Destination (Join-Path $staging "Ollama") -Recurse
    Copy-Item -LiteralPath $comfyRoot -Destination (Join-Path $staging "ComfyUI") -Recurse
    Copy-Item -LiteralPath $ffmpegRoot -Destination (Join-Path $staging "FFmpeg") -Recurse
    Copy-Item -LiteralPath $manifestPath -Destination (Join-Path $staging "resource-manifest.json")

    if (Test-Path $zipPath) { Remove-Item -LiteralPath $zipPath -Force }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::CreateFromDirectory(
        $staging,
        $zipPath,
        [System.IO.Compression.CompressionLevel]::Optimal,
        $false
    )

    $zipHash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash
    "$zipHash  $(Split-Path -Leaf $zipPath)" |
        Set-Content -LiteralPath "$zipPath.sha256" -Encoding ascii

    Write-Host "Offline AI resource bundle created:"
    Write-Host $zipPath
    Write-Host ("Size: {0:N2} GiB" -f ((Get-Item -LiteralPath $zipPath).Length / 1GB))
    Write-Host "SHA256: $zipHash"
} finally {
    if (Test-Path $staging) {
        Remove-Item -LiteralPath $staging -Recurse -Force
    }
}
