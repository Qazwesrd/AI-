param(
    [Parameter(Mandatory = $true)]
    [string]$PayloadRoot,
    [Parameter(Mandatory = $true)]
    [string]$InstallRoot,
    [string]$LogPath = (Join-Path $env:TEMP "AIComicStudio-Ollama-install.log")
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$transcriptStarted = $false
try {
    Start-Transcript -Path $LogPath -Append -ErrorAction Stop | Out-Null
    $transcriptStarted = $true
} catch {}
trap {
    if ($transcriptStarted) {
        try { Stop-Transcript | Out-Null } catch {}
        $transcriptStarted = $false
    }
    break
}

function Get-Sha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Resolve-SafeRelativePath([string]$Root, [string]$RelativePath) {
    if ([string]::IsNullOrWhiteSpace($RelativePath) -or
        [System.IO.Path]::IsPathRooted($RelativePath) -or
        $RelativePath -match '^[A-Za-z]:' -or
        $RelativePath -match '(^|[/\\])\.\.([/\\]|$)') {
        throw "本地 AI 载荷包含不安全的相对路径：$RelativePath"
    }
    $fullRoot = [System.IO.Path]::GetFullPath($Root).TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
    $fullPath = [System.IO.Path]::GetFullPath((Join-Path $Root $RelativePath.Replace("/", "\")))
    if (!$fullPath.StartsWith($fullRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "本地 AI 载荷路径越界：$RelativePath"
    }
    return $fullPath
}

$payload = (Resolve-Path -LiteralPath $PayloadRoot -ErrorAction Stop).Path
$manifestPath = Join-Path $payload "manifest.json"
if (!(Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "本地 AI 离线载荷缺少 manifest.json。"
}
$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
$bundleManifestPath = Join-Path (Split-Path -Parent $payload) "ComfyUI-Payload\manifest.json"
if (!(Test-Path -LiteralPath $bundleManifestPath -PathType Leaf)) {
    throw "完整离线套件缺少 ComfyUI 根清单，拒绝安装未绑定的 LocalAI-Payload。"
}
$bundleManifest = Get-Content -LiteralPath $bundleManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
$payloadManifestHash = Get-Sha256 $manifestPath
if ($bundleManifest.distribution_approved -ne $false -or
    $bundleManifest.local_ai.model -cne "qwen3:4b" -or
    $bundleManifest.local_ai.payload_manifest_sha256 -ne $payloadManifestHash -or
    $bundleManifest.local_ai.runtime_sha256 -ne $manifest.ollama.sha256 -or
    $bundleManifest.local_ai.model_manifest_sha256 -ne $manifest.model.manifest_sha256) {
    throw "LocalAI-Payload 与完整离线套件根清单不匹配，拒绝安装。"
}
if ($manifest.schema_version -ne 1 -or
    $manifest.bundle_type -ne "internal-offline-local-ai-runtime-and-model" -or
    $manifest.distribution_approved -ne $false -or
    $manifest.audit_status -ne "pending") {
    throw "本地 AI 离线载荷版本或许可证审查门禁状态不匹配。"
}
if ($manifest.model.name -cne "qwen3:4b" -or
    $manifest.model.license -cne "Apache-2.0" -or
    $manifest.model.license_review_status -cne "pending") {
    throw "本地 AI 模型名称或许可证登记信息不符合当前内部验证策略。"
}
if ($manifest.ollama.version -notmatch '^v[0-9]+\.[0-9]+\.[0-9]+$') {
    throw "Ollama 运行环境版本标记无效。"
}

$runtimeAssetName = "ollama-windows-amd64.zip"
$expectedRuntimeUrl = "https://github.com/ollama/ollama/releases/download/$($manifest.ollama.version)/$runtimeAssetName"
if ($manifest.ollama.asset -cne $runtimeAssetName -or
    $manifest.ollama.source -cne $expectedRuntimeUrl -or
    [string]$manifest.ollama.sha256 -notmatch '^[0-9a-fA-F]{64}$' -or
    [long]$manifest.ollama.size_bytes -le 0) {
    throw "Ollama 运行包来源、大小或 SHA-256 清单无效。"
}
$runtimeZip = Join-Path $payload $runtimeAssetName
if (!(Test-Path -LiteralPath $runtimeZip -PathType Leaf) -or
    (Get-Item -LiteralPath $runtimeZip).Length -ne [long]$manifest.ollama.size_bytes -or
    (Get-Sha256 $runtimeZip) -ne ([string]$manifest.ollama.sha256).ToLowerInvariant()) {
    throw "Ollama 离线运行包缺失或 SHA-256 校验失败。"
}

$modelFiles = @($manifest.model.files)
if ($modelFiles.Count -lt 1 -or $modelFiles.Count -gt 1000) {
    throw "本地 AI 模型文件清单为空或超过安全上限。"
}
[long]$modelBytes = 0
foreach ($file in $modelFiles) {
    $relative = [string]$file.path
    if (!$relative.StartsWith("models/", [System.StringComparison]::Ordinal)) {
        throw "模型文件路径必须位于 models 目录：$relative"
    }
    $sourcePath = Resolve-SafeRelativePath $payload $relative
    if (!(Test-Path -LiteralPath $sourcePath -PathType Leaf) -or
        (Get-Item -LiteralPath $sourcePath).Length -ne [long]$file.size_bytes -or
        [string]$file.sha256 -notmatch '^[0-9a-fA-F]{64}$' -or
        (Get-Sha256 $sourcePath) -ne ([string]$file.sha256).ToLowerInvariant()) {
        throw "本地 AI 模型文件缺失、大小不匹配或 SHA-256 校验失败：$relative"
    }
    $modelBytes += [long]$file.size_bytes
}
$modelManifestPath = Resolve-SafeRelativePath $payload ([string]$manifest.model.manifest_path)
if (!(Test-Path -LiteralPath $modelManifestPath -PathType Leaf) -or
    (Get-Sha256 $modelManifestPath) -ne ([string]$manifest.model.manifest_sha256).ToLowerInvariant()) {
    throw "qwen3:4b 模型 manifest 缺失或 SHA-256 校验失败。"
}

foreach ($notice in @($manifest.notices)) {
    $noticePath = Resolve-SafeRelativePath $payload ([string]$notice)
    if (!(Test-Path -LiteralPath $noticePath -PathType Leaf)) {
        throw "本地 AI 离线载荷缺少许可证/NOTICE 文件：$notice"
    }
}

$installFullPath = [System.IO.Path]::GetFullPath($InstallRoot)
if (Test-Path -LiteralPath $installFullPath) {
    $existingOllama = Join-Path $installFullPath "runtime\ollama.exe"
    $existingManifest = Resolve-SafeRelativePath $installFullPath ([string]$manifest.model.manifest_path)
    if ((Test-Path -LiteralPath $existingOllama -PathType Leaf) -and
        (Test-Path -LiteralPath $existingManifest -PathType Leaf)) {
        Write-Host "检测到已有本地 Ollama 运行环境和 Qwen3 模型，为保护已有模型及配置，本次不覆盖。"
        if ($transcriptStarted) {
            Stop-Transcript | Out-Null
            $transcriptStarted = $false
        }
        return
    }
    throw "检测到已有本地 AI 目录但未找到完整的 Ollama/Qwen3 安装。为保护已有数据，安装已停止；请先备份并手动处理该目录。"
}

$installParent = Split-Path -Parent $installFullPath
New-Item -ItemType Directory -Path $installParent -Force | Out-Null
$driveRoot = [System.IO.Path]::GetPathRoot($installFullPath)
$drive = [System.IO.DriveInfo]::new($driveRoot)
$requiredBytes = [long]$manifest.ollama.size_bytes + $modelBytes + 1GB
if ($drive.AvailableFreeSpace -lt $requiredBytes) {
    throw ("本地 AI 安装空间不足。至少需要 {0:N2} GiB，可用 {1:N2} GiB。" -f ($requiredBytes / 1GB), ($drive.AvailableFreeSpace / 1GB))
}

$stage = Join-Path $installParent (".Ollama-stage-" + [Guid]::NewGuid().ToString("N"))
$extractRoot = Join-Path $stage "runtime-extract"
$runtimeRoot = Join-Path $stage "runtime"
$modelsRoot = Join-Path $stage "models"
New-Item -ItemType Directory -Path $stage -Force | Out-Null
try {
    Expand-Archive -LiteralPath $runtimeZip -DestinationPath $extractRoot -Force
    $ollamaExe = Get-ChildItem -LiteralPath $extractRoot -Filter "ollama.exe" -File -Recurse | Select-Object -First 1
    if (!$ollamaExe) {
        throw "解压后的 Ollama 运行包缺少 ollama.exe。"
    }
    $runtimeSourceRoot = $ollamaExe.Directory.FullName
    Move-Item -LiteralPath $runtimeSourceRoot -Destination $runtimeRoot
    New-Item -ItemType Directory -Path $modelsRoot -Force | Out-Null
    foreach ($modelItem in Get-ChildItem -LiteralPath (Join-Path $payload "models") -Force) {
        Copy-Item -LiteralPath $modelItem.FullName -Destination $modelsRoot -Recurse
    }

    foreach ($file in $modelFiles) {
        $relative = [string]$file.path
        $installedPath = Resolve-SafeRelativePath $stage $relative
        if (!(Test-Path -LiteralPath $installedPath -PathType Leaf) -or
            (Get-Item -LiteralPath $installedPath).Length -ne [long]$file.size_bytes -or
            (Get-Sha256 $installedPath) -ne ([string]$file.sha256).ToLowerInvariant()) {
            throw "安装暂存区中的模型文件校验失败：$relative"
        }
    }

    $launcher = Join-Path $stage "AI漫剧大师-启动本地AI.bat"
    @(
        "@echo off",
        "setlocal",
        'set "OLLAMA_MODELS=%~dp0models"',
        'set "OLLAMA_HOST=127.0.0.1:11435"',
        'cd /d "%~dp0runtime"',
        '"ollama.exe" serve',
        "endlocal"
    ) | Set-Content -LiteralPath $launcher -Encoding ASCII

    $installedManifest = @{
        schema_version = 1
        ollama_version = $manifest.ollama.version
        model = $manifest.model.name
        model_manifest_sha256 = $manifest.model.manifest_sha256
        installed_utc = [DateTime]::UtcNow.ToString("o")
        distribution_approved = $false
    }
    $installedManifest | ConvertTo-Json -Depth 5 |
        Set-Content -LiteralPath (Join-Path $stage "install-manifest.json") -Encoding UTF8

    Move-Item -LiteralPath $stage -Destination $installFullPath
    Write-Host "本地 Ollama 运行环境和 Qwen3 4B 模型已部署到：$installFullPath"
    Write-Host "本地模型存储路径：$(Join-Path $installFullPath 'models')"
    Write-Host "本地服务地址：http://127.0.0.1:11435"
    Write-Host "安装过程未联网下载模型；许可证审查状态仍为 pending。"
}
finally {
    if (Test-Path -LiteralPath $stage) {
        Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
    }
    if ($transcriptStarted) {
        try { Stop-Transcript | Out-Null } catch {}
        $transcriptStarted = $false
    }
}
