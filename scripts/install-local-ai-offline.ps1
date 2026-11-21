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

function Get-ZipExpandedSize([string]$Path) {
    Add-Type -AssemblyName System.IO.Compression
    $stream = [System.IO.File]::OpenRead($Path)
    $archive = $null
    try {
        $archive = [System.IO.Compression.ZipArchive]::new($stream, [System.IO.Compression.ZipArchiveMode]::Read, $false)
        if ($archive.Entries.Count -gt 100000) {
            throw "Ollama 运行包条目数超过安全上限。"
        }
        [long]$expanded = ($archive.Entries | Measure-Object -Property Length -Sum).Sum
        if ($expanded -le 0 -or $expanded -gt 8GB) {
            throw "Ollama 运行包解压体积无效或超过 8 GiB 安全上限。"
        }
        return $expanded
    } finally {
        if ($archive) { $archive.Dispose() }
        $stream.Dispose()
    }
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
    $bundleManifest.local_ai.model_manifest_sha256 -ne $manifest.model.manifest_sha256 -or
    $bundleManifest.local_ai.cloud_features_disabled -ne $true -or
    $manifest.cloud_features_disabled -ne $true) {
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
$uniqueModelPaths = @($modelFiles | ForEach-Object { [string]$_.path } | Sort-Object -Unique)
if ($uniqueModelPaths.Count -ne $modelFiles.Count) {
    throw "本地 AI 模型文件清单包含重复路径。"
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
if (@($modelFiles | Where-Object { $_.path -ceq $manifest.model.manifest_path }).Count -ne 1) {
    throw "模型文件清单未包含 qwen3:4b manifest 本身。"
}
$modelManifestObject = Get-Content -LiteralPath $modelManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
$modelDescriptors = @()
if ($modelManifestObject.config) { $modelDescriptors += $modelManifestObject.config }
if ($modelManifestObject.layers) { $modelDescriptors += @($modelManifestObject.layers) }
if ($modelDescriptors.Count -lt 1) {
    throw "qwen3:4b 模型 manifest 不包含任何内容层。"
}
foreach ($descriptor in $modelDescriptors) {
    $digestMatch = [regex]::Match([string]$descriptor.digest, '^sha256:([0-9a-fA-F]{64})foreach ($notice in @($manifest.notices)) {
    $noticePath = Resolve-SafeRelativePath $payload ([string]$notice)
    if (!(Test-Path -LiteralPath $noticePath -PathType Leaf)) {
        throw "本地 AI 离线载荷缺少许可证/NOTICE 文件：$notice"
    }
}

$installFullPath = [System.IO.Path]::GetFullPath($InstallRoot)
if (Test-Path -LiteralPath $installFullPath) {
    $existingOllama = Join-Path $installFullPath "runtime\ollama.exe"
    $existingRuntime = Join-Path $installFullPath "runtime"
    $existingManifest = Resolve-SafeRelativePath $installFullPath ([string]$manifest.model.manifest_path)
    $existingStatePath = Join-Path $installFullPath "install-manifest.json"
    if ((Test-Path -LiteralPath $existingOllama -PathType Leaf) -and
        (Test-Path -LiteralPath $existingManifest -PathType Leaf) -and
        (Test-Path -LiteralPath $existingStatePath -PathType Leaf)) {
        $existingState = Get-Content -LiteralPath $existingStatePath -Raw -Encoding UTF8 | ConvertFrom-Json
        $existingModelHash = Get-Sha256 $existingManifest
        if ($existingState.model_manifest_sha256 -ne $existingModelHash) {
            throw "现有 Qwen3 模型与安装记录不一致。为保护用户模型数据，拒绝自动升级。"
        }
        $existingModelManifest = Get-Content -LiteralPath $existingManifest -Raw -Encoding UTF8 | ConvertFrom-Json
        $existingDescriptors = @()
        if ($existingModelManifest.config) { $existingDescriptors += $existingModelManifest.config }
        if ($existingModelManifest.layers) { $existingDescriptors += @($existingModelManifest.layers) }
        foreach ($descriptor in $existingDescriptors) {
            $digestMatch = [regex]::Match([string]$descriptor.digest, '^sha256:([0-9a-fA-F]{64})$')
            if (!$digestMatch.Success -or [long]$descriptor.size -le 0) {
                throw "现有 Qwen3 模型 manifest 包含无效的 blob 描述符。"
            }
            $digest = $digestMatch.Groups[1].Value.ToLowerInvariant()
            $existingBlob = Join-Path (Join-Path $installFullPath "models\blobs") ("sha256-" + $digest)
            if (!(Test-Path -LiteralPath $existingBlob -PathType Leaf) -or
                (Get-Item -LiteralPath $existingBlob).Length -ne [long]$descriptor.size -or
                (Get-Sha256 $existingBlob) -ne $digest) {
                throw "现有 Qwen3 模型 blob 校验失败；为保护用户数据，拒绝升级。"
            }
        }
        if ($existingState.ollama_version -eq $manifest.ollama.version) {
            Write-Host "检测到 Ollama 和 Qwen3 4B 已是当前载荷版本，为保护已有模型及配置，本次不覆盖。"
            if ($transcriptStarted) {
                Stop-Transcript | Out-Null
                $transcriptStarted = $false
            }
            return
        }

        $upgradeParent = Split-Path -Parent $installFullPath
        $upgradeDrive = [System.IO.DriveInfo]::new([System.IO.Path]::GetPathRoot($installFullPath))
        $runtimeExpandedBytes = Get-ZipExpandedSize $runtimeZip
        $upgradeRequired = $runtimeExpandedBytes + 1GB
        if ($upgradeDrive.AvailableFreeSpace -lt $upgradeRequired) {
            throw ("升级 Ollama 运行环境至少需要 {0:N2} GiB 可用空间。" -f ($upgradeRequired / 1GB))
        }
        $upgradeStage = Join-Path $upgradeParent (".Ollama-upgrade-" + [Guid]::NewGuid().ToString("N"))
        $upgradeExtract = Join-Path $upgradeStage "extract"
        $upgradeRuntime = Join-Path $upgradeStage "runtime"
        $runtimeBackup = Join-Path $installFullPath (".runtime-backup-" + [Guid]::NewGuid().ToString("N"))
        New-Item -ItemType Directory -Path $upgradeStage -Force | Out-Null
        try {
            Expand-Archive -LiteralPath $runtimeZip -DestinationPath $upgradeExtract -Force
            $upgradeExe = Get-ChildItem -LiteralPath $upgradeExtract -Filter "ollama.exe" -File -Recurse | Select-Object -First 1
            if (!$upgradeExe) {
                throw "新版本 Ollama 运行包解压后缺少 ollama.exe。"
            }
            Move-Item -LiteralPath $upgradeExe.Directory.FullName -Destination $upgradeRuntime
            Move-Item -LiteralPath $existingRuntime -Destination $runtimeBackup
            try {
                Move-Item -LiteralPath $upgradeRuntime -Destination $existingRuntime
                if (!(Test-Path -LiteralPath (Join-Path $existingRuntime "ollama.exe") -PathType Leaf)) {
                    throw "Ollama 新运行环境部署后缺少 ollama.exe。"
                }
                $updatedState = @{
                    schema_version = 1
                    ollama_version = $manifest.ollama.version
                    model = $manifest.model.name
                    model_manifest_sha256 = $existingModelHash
                    installed_utc = [DateTime]::UtcNow.ToString("o")
                    distribution_approved = $false
                }
                $stateTemp = Join-Path $installFullPath "install-manifest.json.new"
                $updatedState | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $stateTemp -Encoding UTF8
                Move-Item -LiteralPath $stateTemp -Destination $existingStatePath -Force
                Remove-Item -LiteralPath $runtimeBackup -Recurse -Force -ErrorAction SilentlyContinue
            } catch {
                if (Test-Path -LiteralPath $existingRuntime) {
                    Remove-Item -LiteralPath $existingRuntime -Recurse -Force -ErrorAction SilentlyContinue
                }
                if (Test-Path -LiteralPath $runtimeBackup) {
                    Move-Item -LiteralPath $runtimeBackup -Destination $existingRuntime -Force
                }
                throw
            }
            Write-Host "Ollama 运行环境已升级到 $($manifest.ollama.version)；现有 Qwen3 模型和用户数据保持不变。"
        } finally {
            if (Test-Path -LiteralPath $upgradeStage) {
                Remove-Item -LiteralPath $upgradeStage -Recurse -Force -ErrorAction SilentlyContinue
            }
        }
        if ($transcriptStarted) {
            Stop-Transcript | Out-Null
            $transcriptStarted = $false
        }
        return
    }
    throw "检测到已有本地 AI 目录但未找到完整且可验证的 Ollama/Qwen3 安装。为保护已有数据，安装已停止；请先备份并手动处理该目录。"
}

$installParent = Split-Path -Parent $installFullPath
New-Item -ItemType Directory -Path $installParent -Force | Out-Null
$driveRoot = [System.IO.Path]::GetPathRoot($installFullPath)
$drive = [System.IO.DriveInfo]::new($driveRoot)
$runtimeExpandedBytes = Get-ZipExpandedSize $runtimeZip
$requiredBytes = $runtimeExpandedBytes + $modelBytes + 1GB
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
        'set "OLLAMA_NO_CLOUD=1"',
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
        cloud_features_disabled = $true
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
)
            if (!$digestMatch.Success -or [long]$descriptor.size -le 0) {
                throw "现有 Qwen3 模型 manifest 包含无效的 blob 描述符。"
            }
            $digest = $digestMatch.Groups[1].Value.ToLowerInvariant()
            $existingBlob = Join-Path (Join-Path $installFullPath "models\blobs") ("sha256-" + $digest)
            if (!(Test-Path -LiteralPath $existingBlob -PathType Leaf) -or
                (Get-Item -LiteralPath $existingBlob).Length -ne [long]$descriptor.size -or
                (Get-Sha256 $existingBlob) -ne $digest) {
                throw "现有 Qwen3 模型 blob 校验失败；为保护用户数据，拒绝升级。"
            }
        }
        if ($existingState.ollama_version -eq $manifest.ollama.version) {
            Write-Host "检测到 Ollama 和 Qwen3 4B 已是当前载荷版本，为保护已有模型及配置，本次不覆盖。"
            if ($transcriptStarted) {
                Stop-Transcript | Out-Null
                $transcriptStarted = $false
            }
            return
        }

        $upgradeParent = Split-Path -Parent $installFullPath
        $upgradeDrive = [System.IO.DriveInfo]::new([System.IO.Path]::GetPathRoot($installFullPath))
        $upgradeRequired = [long]$manifest.ollama.size_bytes + 1GB
        if ($upgradeDrive.AvailableFreeSpace -lt $upgradeRequired) {
            throw ("升级 Ollama 运行环境至少需要 {0:N2} GiB 可用空间。" -f ($upgradeRequired / 1GB))
        }
        $upgradeStage = Join-Path $upgradeParent (".Ollama-upgrade-" + [Guid]::NewGuid().ToString("N"))
        $upgradeExtract = Join-Path $upgradeStage "extract"
        $upgradeRuntime = Join-Path $upgradeStage "runtime"
        $runtimeBackup = Join-Path $installFullPath (".runtime-backup-" + [Guid]::NewGuid().ToString("N"))
        New-Item -ItemType Directory -Path $upgradeStage -Force | Out-Null
        try {
            Expand-Archive -LiteralPath $runtimeZip -DestinationPath $upgradeExtract -Force
            $upgradeExe = Get-ChildItem -LiteralPath $upgradeExtract -Filter "ollama.exe" -File -Recurse | Select-Object -First 1
            if (!$upgradeExe) {
                throw "新版本 Ollama 运行包解压后缺少 ollama.exe。"
            }
            Move-Item -LiteralPath $upgradeExe.Directory.FullName -Destination $upgradeRuntime
            Move-Item -LiteralPath $existingRuntime -Destination $runtimeBackup
            try {
                Move-Item -LiteralPath $upgradeRuntime -Destination $existingRuntime
                if (!(Test-Path -LiteralPath (Join-Path $existingRuntime "ollama.exe") -PathType Leaf)) {
                    throw "Ollama 新运行环境部署后缺少 ollama.exe。"
                }
                $updatedState = @{
                    schema_version = 1
                    ollama_version = $manifest.ollama.version
                    model = $manifest.model.name
                    model_manifest_sha256 = $existingModelHash
                    installed_utc = [DateTime]::UtcNow.ToString("o")
                    distribution_approved = $false
                }
                $stateTemp = Join-Path $installFullPath "install-manifest.json.new"
                $updatedState | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $stateTemp -Encoding UTF8
                Move-Item -LiteralPath $stateTemp -Destination $existingStatePath -Force
                Remove-Item -LiteralPath $runtimeBackup -Recurse -Force -ErrorAction SilentlyContinue
            } catch {
                if (Test-Path -LiteralPath $existingRuntime) {
                    Remove-Item -LiteralPath $existingRuntime -Recurse -Force -ErrorAction SilentlyContinue
                }
                if (Test-Path -LiteralPath $runtimeBackup) {
                    Move-Item -LiteralPath $runtimeBackup -Destination $existingRuntime -Force
                }
                throw
            }
            Write-Host "Ollama 运行环境已升级到 $($manifest.ollama.version)；现有 Qwen3 模型和用户数据保持不变。"
        } finally {
            if (Test-Path -LiteralPath $upgradeStage) {
                Remove-Item -LiteralPath $upgradeStage -Recurse -Force -ErrorAction SilentlyContinue
            }
        }
        if ($transcriptStarted) {
            Stop-Transcript | Out-Null
            $transcriptStarted = $false
        }
        return
    }
    throw "检测到已有本地 AI 目录但未找到完整且可验证的 Ollama/Qwen3 安装。为保护已有数据，安装已停止；请先备份并手动处理该目录。"
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
)
    if (!$digestMatch.Success -or [long]$descriptor.size -le 0) {
        throw "qwen3:4b 模型 manifest 包含无效的内容层描述。"
    }
    $digest = $digestMatch.Groups[1].Value.ToLowerInvariant()
    $blobRelative = "models/blobs/sha256-$digest"
    $blobRecords = @($modelFiles | Where-Object { $_.path -ceq $blobRelative })
    $layerRecords = @($manifest.model.layers | Where-Object { $_.digest -ceq "sha256:$digest" })
    if ($blobRecords.Count -ne 1 -or [long]$blobRecords[0].size_bytes -ne [long]$descriptor.size -or
        $layerRecords.Count -ne 1 -or [long]$layerRecords[0].size_bytes -ne [long]$descriptor.size) {
        throw "模型清单未完整登记 manifest 引用的 blob：$digest"
    }
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
    $existingRuntime = Join-Path $installFullPath "runtime"
    $existingManifest = Resolve-SafeRelativePath $installFullPath ([string]$manifest.model.manifest_path)
    $existingStatePath = Join-Path $installFullPath "install-manifest.json"
    if ((Test-Path -LiteralPath $existingOllama -PathType Leaf) -and
        (Test-Path -LiteralPath $existingManifest -PathType Leaf) -and
        (Test-Path -LiteralPath $existingStatePath -PathType Leaf)) {
        $existingState = Get-Content -LiteralPath $existingStatePath -Raw -Encoding UTF8 | ConvertFrom-Json
        $existingModelHash = Get-Sha256 $existingManifest
        if ($existingState.model_manifest_sha256 -ne $existingModelHash) {
            throw "现有 Qwen3 模型与安装记录不一致。为保护用户模型数据，拒绝自动升级。"
        }
        $existingModelManifest = Get-Content -LiteralPath $existingManifest -Raw -Encoding UTF8 | ConvertFrom-Json
        $existingDescriptors = @()
        if ($existingModelManifest.config) { $existingDescriptors += $existingModelManifest.config }
        if ($existingModelManifest.layers) { $existingDescriptors += @($existingModelManifest.layers) }
        foreach ($descriptor in $existingDescriptors) {
            $digestMatch = [regex]::Match([string]$descriptor.digest, '^sha256:([0-9a-fA-F]{64})        if ($existingState.ollama_version -eq $manifest.ollama.version) {
            Write-Host "检测到 Ollama 和 Qwen3 4B 已是当前载荷版本，为保护已有模型及配置，本次不覆盖。"
            if ($transcriptStarted) {
                Stop-Transcript | Out-Null
                $transcriptStarted = $false
            }
            return
        }

        $upgradeParent = Split-Path -Parent $installFullPath
        $upgradeDrive = [System.IO.DriveInfo]::new([System.IO.Path]::GetPathRoot($installFullPath))
        $runtimeExpandedBytes = Get-ZipExpandedSize $runtimeZip
        $upgradeRequired = $runtimeExpandedBytes + 1GB
        if ($upgradeDrive.AvailableFreeSpace -lt $upgradeRequired) {
            throw ("升级 Ollama 运行环境至少需要 {0:N2} GiB 可用空间。" -f ($upgradeRequired / 1GB))
        }
        $upgradeStage = Join-Path $upgradeParent (".Ollama-upgrade-" + [Guid]::NewGuid().ToString("N"))
        $upgradeExtract = Join-Path $upgradeStage "extract"
        $upgradeRuntime = Join-Path $upgradeStage "runtime"
        $runtimeBackup = Join-Path $installFullPath (".runtime-backup-" + [Guid]::NewGuid().ToString("N"))
        New-Item -ItemType Directory -Path $upgradeStage -Force | Out-Null
        try {
            Expand-Archive -LiteralPath $runtimeZip -DestinationPath $upgradeExtract -Force
            $upgradeExe = Get-ChildItem -LiteralPath $upgradeExtract -Filter "ollama.exe" -File -Recurse | Select-Object -First 1
            if (!$upgradeExe) {
                throw "新版本 Ollama 运行包解压后缺少 ollama.exe。"
            }
            Move-Item -LiteralPath $upgradeExe.Directory.FullName -Destination $upgradeRuntime
            Move-Item -LiteralPath $existingRuntime -Destination $runtimeBackup
            try {
                Move-Item -LiteralPath $upgradeRuntime -Destination $existingRuntime
                if (!(Test-Path -LiteralPath (Join-Path $existingRuntime "ollama.exe") -PathType Leaf)) {
                    throw "Ollama 新运行环境部署后缺少 ollama.exe。"
                }
                $updatedState = @{
                    schema_version = 1
                    ollama_version = $manifest.ollama.version
                    model = $manifest.model.name
                    model_manifest_sha256 = $existingModelHash
                    installed_utc = [DateTime]::UtcNow.ToString("o")
                    distribution_approved = $false
                }
                $stateTemp = Join-Path $installFullPath "install-manifest.json.new"
                $updatedState | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $stateTemp -Encoding UTF8
                Move-Item -LiteralPath $stateTemp -Destination $existingStatePath -Force
                Remove-Item -LiteralPath $runtimeBackup -Recurse -Force -ErrorAction SilentlyContinue
            } catch {
                if (Test-Path -LiteralPath $existingRuntime) {
                    Remove-Item -LiteralPath $existingRuntime -Recurse -Force -ErrorAction SilentlyContinue
                }
                if (Test-Path -LiteralPath $runtimeBackup) {
                    Move-Item -LiteralPath $runtimeBackup -Destination $existingRuntime -Force
                }
                throw
            }
            Write-Host "Ollama 运行环境已升级到 $($manifest.ollama.version)；现有 Qwen3 模型和用户数据保持不变。"
        } finally {
            if (Test-Path -LiteralPath $upgradeStage) {
                Remove-Item -LiteralPath $upgradeStage -Recurse -Force -ErrorAction SilentlyContinue
            }
        }
        if ($transcriptStarted) {
            Stop-Transcript | Out-Null
            $transcriptStarted = $false
        }
        return
    }
    throw "检测到已有本地 AI 目录但未找到完整且可验证的 Ollama/Qwen3 安装。为保护已有数据，安装已停止；请先备份并手动处理该目录。"
}

$installParent = Split-Path -Parent $installFullPath
New-Item -ItemType Directory -Path $installParent -Force | Out-Null
$driveRoot = [System.IO.Path]::GetPathRoot($installFullPath)
$drive = [System.IO.DriveInfo]::new($driveRoot)
$runtimeExpandedBytes = Get-ZipExpandedSize $runtimeZip
$requiredBytes = $runtimeExpandedBytes + $modelBytes + 1GB
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
        'set "OLLAMA_NO_CLOUD=1"',
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
        cloud_features_disabled = $true
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
)
            if (!$digestMatch.Success -or [long]$descriptor.size -le 0) {
                throw "现有 Qwen3 模型 manifest 包含无效的 blob 描述符。"
            }
            $digest = $digestMatch.Groups[1].Value.ToLowerInvariant()
            $existingBlob = Join-Path (Join-Path $installFullPath "models\blobs") ("sha256-" + $digest)
            if (!(Test-Path -LiteralPath $existingBlob -PathType Leaf) -or
                (Get-Item -LiteralPath $existingBlob).Length -ne [long]$descriptor.size -or
                (Get-Sha256 $existingBlob) -ne $digest) {
                throw "现有 Qwen3 模型 blob 校验失败；为保护用户数据，拒绝升级。"
            }
        }
        if ($existingState.ollama_version -eq $manifest.ollama.version) {
            Write-Host "检测到 Ollama 和 Qwen3 4B 已是当前载荷版本，为保护已有模型及配置，本次不覆盖。"
            if ($transcriptStarted) {
                Stop-Transcript | Out-Null
                $transcriptStarted = $false
            }
            return
        }

        $upgradeParent = Split-Path -Parent $installFullPath
        $upgradeDrive = [System.IO.DriveInfo]::new([System.IO.Path]::GetPathRoot($installFullPath))
        $upgradeRequired = [long]$manifest.ollama.size_bytes + 1GB
        if ($upgradeDrive.AvailableFreeSpace -lt $upgradeRequired) {
            throw ("升级 Ollama 运行环境至少需要 {0:N2} GiB 可用空间。" -f ($upgradeRequired / 1GB))
        }
        $upgradeStage = Join-Path $upgradeParent (".Ollama-upgrade-" + [Guid]::NewGuid().ToString("N"))
        $upgradeExtract = Join-Path $upgradeStage "extract"
        $upgradeRuntime = Join-Path $upgradeStage "runtime"
        $runtimeBackup = Join-Path $installFullPath (".runtime-backup-" + [Guid]::NewGuid().ToString("N"))
        New-Item -ItemType Directory -Path $upgradeStage -Force | Out-Null
        try {
            Expand-Archive -LiteralPath $runtimeZip -DestinationPath $upgradeExtract -Force
            $upgradeExe = Get-ChildItem -LiteralPath $upgradeExtract -Filter "ollama.exe" -File -Recurse | Select-Object -First 1
            if (!$upgradeExe) {
                throw "新版本 Ollama 运行包解压后缺少 ollama.exe。"
            }
            Move-Item -LiteralPath $upgradeExe.Directory.FullName -Destination $upgradeRuntime
            Move-Item -LiteralPath $existingRuntime -Destination $runtimeBackup
            try {
                Move-Item -LiteralPath $upgradeRuntime -Destination $existingRuntime
                if (!(Test-Path -LiteralPath (Join-Path $existingRuntime "ollama.exe") -PathType Leaf)) {
                    throw "Ollama 新运行环境部署后缺少 ollama.exe。"
                }
                $updatedState = @{
                    schema_version = 1
                    ollama_version = $manifest.ollama.version
                    model = $manifest.model.name
                    model_manifest_sha256 = $existingModelHash
                    installed_utc = [DateTime]::UtcNow.ToString("o")
                    distribution_approved = $false
                }
                $stateTemp = Join-Path $installFullPath "install-manifest.json.new"
                $updatedState | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $stateTemp -Encoding UTF8
                Move-Item -LiteralPath $stateTemp -Destination $existingStatePath -Force
                Remove-Item -LiteralPath $runtimeBackup -Recurse -Force -ErrorAction SilentlyContinue
            } catch {
                if (Test-Path -LiteralPath $existingRuntime) {
                    Remove-Item -LiteralPath $existingRuntime -Recurse -Force -ErrorAction SilentlyContinue
                }
                if (Test-Path -LiteralPath $runtimeBackup) {
                    Move-Item -LiteralPath $runtimeBackup -Destination $existingRuntime -Force
                }
                throw
            }
            Write-Host "Ollama 运行环境已升级到 $($manifest.ollama.version)；现有 Qwen3 模型和用户数据保持不变。"
        } finally {
            if (Test-Path -LiteralPath $upgradeStage) {
                Remove-Item -LiteralPath $upgradeStage -Recurse -Force -ErrorAction SilentlyContinue
            }
        }
        if ($transcriptStarted) {
            Stop-Transcript | Out-Null
            $transcriptStarted = $false
        }
        return
    }
    throw "检测到已有本地 AI 目录但未找到完整且可验证的 Ollama/Qwen3 安装。为保护已有数据，安装已停止；请先备份并手动处理该目录。"
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
