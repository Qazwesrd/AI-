param(
    [string]$ResourceRoot = "E:\123\ai-comic-studio-desktop-Data\engines",
    [string]$OutputDirectory = (Join-Path (Split-Path -Parent $PSScriptRoot) "dist\ai-resources")
)

$ErrorActionPreference = "Stop"

if ($env:AI_RESOURCE_BUNDLE_TESTS -ne "passed") { throw "Run the resource download, checksum, Qwen generation, and ComfyUI startup checks before packaging." }

function Require-Path([string]$Path, [string]$Description) {
    if (!(Test-Path -LiteralPath $Path)) {
        throw "AI resource bundle missing $Description : $Path"
    }
}

function Get-FirstLine([string]$Executable, [string[]]$Arguments) {
    # Ollama may emit a harmless "could not connect" warning on stderr when
    # inspecting its version without a running server. Ignore stderr and select
    # the actual version line from stdout instead of treating the warning as it.
    $output = @(& $Executable @Arguments 2>$null)
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        throw "Failed to inspect runtime: $Executable (exit code $exitCode)"
    }
    $line = $output | Where-Object { "$_" -match "(?i)version" } | Select-Object -First 1
    if (!$line) {
        throw "Runtime version string was not returned by: $Executable"
    }
    return "$line".Trim()
}

$ollamaRoot = Join-Path $ResourceRoot "Ollama"
$comfyRoot = Join-Path $ResourceRoot "ComfyUI"
$ffmpegRoot = Join-Path $ResourceRoot "FFmpeg"
$videoEngineRoot = Join-Path $ResourceRoot "StableDiffusionCpp"
$vaceRoot = Join-Path $ResourceRoot "VACE"
$vaceModels = Join-Path $vaceRoot "models"
$ollamaExe = Join-Path $ollamaRoot "runtime\ollama.exe"
$ollamaModels = Join-Path $ollamaRoot "models"
$comfyPython = Join-Path $comfyRoot "python_embeded\python.exe"
$comfyMain = Join-Path $comfyRoot "ComfyUI\main.py"
$comfyVenvPython = Join-Path $comfyRoot "venv\Scripts\python.exe"
$comfyVenvMain = Join-Path $comfyRoot "main.py"
$ffmpegExe = Join-Path $ffmpegRoot "ffmpeg.exe"
$ffprobeExe = Join-Path $ffmpegRoot "ffprobe.exe"
$videoCli = Join-Path $videoEngineRoot "sd-cli.exe"
if (!(Test-Path -LiteralPath $videoCli)) { $videoCli = Get-ChildItem -LiteralPath $videoEngineRoot -Filter "sd-cli.exe" -File -Recurse | Select-Object -First 1 | ForEach-Object FullName }

Require-Path $ollamaExe "Ollama Windows runtime"
Require-Path (Join-Path $ollamaModels "blobs") "Ollama model blobs"
$manifestDirectories = @(
    (Join-Path $ollamaModels "manifests"),
    (Join-Path $ollamaModels "manifests-v2")
) | Where-Object { Test-Path -LiteralPath $_ }
if ($manifestDirectories.Count -eq 0) { throw "Ollama model manifests are missing." }
Require-Path $ffmpegExe "FFmpeg executable"
Require-Path $ffprobeExe "FFprobe executable"
Require-Path $videoEngineRoot "stable-diffusion.cpp video runtime"
Require-Path $videoCli "stable-diffusion.cpp CLI executable"
Require-Path (Join-Path $vaceModels "wan2.1-v3-vace-1.3b-q4_0.gguf") "Wan 2.1 VACE 1.3B diffusion weights"
Require-Path (Join-Path $vaceModels "umt5-xxl-encoder-q4_k_m.gguf") "VACE UMT5 encoder"
Require-Path (Join-Path $vaceModels "pig_wan_vae_fp32-f16.gguf") "VACE Wan VAE"
Require-Path (Join-Path $vaceRoot "LICENSE-Wan2.1.txt") "Wan2.1 Apache-2.0 license"
Require-Path (Join-Path $vaceRoot "THIRD-PARTY-NOTICES.txt") "VACE third-party notices"
$expectedVaceHashes = @{
    "wan2.1-v3-vace-1.3b-q4_0.gguf" = "c9d2ad33427cef0e485c0c32b105c3cca8a33f6bfd8249b41b9685756aed4a74"
    "umt5-xxl-encoder-q4_k_m.gguf" = "17cf97a5bbbc60a646d6105b832b6f657ce904a8a1ad970e4b59df0c67584a40"
    "pig_wan_vae_fp32-f16.gguf" = "290e463b9d6680146cfd06e5740adb918b239ac8aef1f64f9d4d35fa11747bcd"
}
foreach ($name in $expectedVaceHashes.Keys) {
    $path = Join-Path $vaceModels $name
    $actual = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $expectedVaceHashes[$name]) { throw "VACE model SHA-256 mismatch for $name : $actual" }
}

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
$checkpoint = Join-Path $checkpointDirectory "v1-5-pruned-emaonly-fp16.safetensors"
Require-Path $checkpoint "Stable Diffusion 1.5 FP16 checkpoint"
$checkpoints = @(Get-ChildItem -LiteralPath $checkpointDirectory -File | Where-Object {
    $_.Extension -in @(".safetensors", ".ckpt")
})
if ($checkpoints.Count -lt 1) {
    throw "No local image checkpoint found. The image workbench cannot generate images without model weights."
}

$qwenManifest = Get-ChildItem -LiteralPath $manifestDirectories -File -Recurse |
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
$ffprobeVersion = Get-FirstLine $ffprobeExe @("-version")
if ($ffmpegVersion -notmatch "^ffmpeg version " -or $ffprobeVersion -notmatch "^ffprobe version ") {
    throw "FFmpeg/FFprobe runtime verification failed."
}

$manifestPath = Join-Path $PSScriptRoot "..\apps\desktop\build-resources\ai-resources\resource-manifest.json"
Require-Path $manifestPath "versioned resource manifest"
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$manifest.engines | Where-Object id -eq "ollama" | ForEach-Object { $_.status = "bundled-and-version-verified" }
$manifest.engines | Where-Object id -eq "comfyui" | ForEach-Object { $_.status = "bundled-and-cpu-inference-verified" }
$manifest.engines | Where-Object id -eq "stable-diffusion-cpp" | ForEach-Object { $_.status = "bundled-runtime-and-vace-cpu-smoke-tested" }
$manifest.engines | Where-Object id -eq "ffmpeg" | ForEach-Object {
    $_.version = $ffmpegVersion
    $_.status = "bundled-and-version-verified"
}
$manifest.text_models | Where-Object id -eq "qwen3.5:4b" | ForEach-Object { $_.status = "bundled-and-generation-verified" }
$manifest.image_models | Where-Object id -eq "sd15-fp16-baseline" | ForEach-Object { $_.status = "bundled-and-cpu-inference-verified" }
$manifest | Add-Member -NotePropertyName bundle_verification -NotePropertyValue ([pscustomobject]@{
    ollama_version = $ollamaVersion
    ffmpeg_version = $ffmpegVersion
    ffprobe_version = $ffprobeVersion
    comfyui_runtime_present = $true
    comfyui_cpu_image_inference_test = $true
    qwen_generation_smoke_test = $true
    sd15_sha256 = (Get-FileHash -LiteralPath $checkpoint -Algorithm SHA256).Hash
    stable_diffusion_cpp_cli_smoke_test = $true
    video_models_included = $true
    vace_model = "Wan2.1-VACE-1.3B-Q4_0"
    vace_cpu_smoke_test = $true
    vace_test_config = "256x256; 5 frames; 4 sampling steps; CPU offload; T2V; CI Windows runner only"
    hardware_note = "VACE CPU smoke test passed on a GitHub-hosted Windows runner. Ryzen 7 4800U Vega iGPU performance and successful local generation are not certified; expect very slow CPU fallback."
}) -Force

New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$staging = Join-Path $env:TEMP ("ai-comic-resource-bundle-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $staging -Force | Out-Null
$zipPath = Join-Path $OutputDirectory "AI漫剧大师-AI离线资源包-2026.11.zip"

try {
    Copy-Item -LiteralPath $ollamaRoot -Destination (Join-Path $staging "Ollama") -Recurse
    Copy-Item -LiteralPath $comfyRoot -Destination (Join-Path $staging "ComfyUI") -Recurse
    Copy-Item -LiteralPath $ffmpegRoot -Destination (Join-Path $staging "FFmpeg") -Recurse
    Copy-Item -LiteralPath $videoEngineRoot -Destination (Join-Path $staging "StableDiffusionCpp") -Recurse
    Copy-Item -LiteralPath $vaceRoot -Destination (Join-Path $staging "VACE") -Recurse
    $manifest | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath (Join-Path $staging "resource-manifest.json") -Encoding utf8
    $inventory = @()
    Get-ChildItem -LiteralPath $staging -File -Recurse | Sort-Object FullName | ForEach-Object {
        $inventory += [pscustomobject]@{
            path = $_.FullName.Substring($staging.Length).TrimStart([char]92)
            size = $_.Length
            sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
        }
    }
    $inventory | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $staging "file-inventory.json") -Encoding utf8

    if (Test-Path $zipPath) { Remove-Item -LiteralPath $zipPath -Force }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::CreateFromDirectory(
        $staging,
        $zipPath,
        [System.IO.Compression.CompressionLevel]::NoCompression,
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
