[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$OutputDirectory,
    [string]$ModelName = "qwen3:4b"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ($env:OS -ne "Windows_NT" -or -not [Environment]::Is64BitOperatingSystem) {
    throw "本地 AI 离线载荷只能在 Windows x64 构建环境中制作。"
}
if ($ModelName -cne "qwen3:4b") {
    throw "当前正式候选只允许固定模型 qwen3:4b，拒绝未经审查的模型名称。"
}

function Get-Sha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Save-OfficialText([string]$Url, [string]$Destination, [string]$RequiredText) {
    $uri = [Uri]$Url
    if ($uri.Scheme -ne "https" -or $uri.Host -notin @("raw.githubusercontent.com", "www.apache.org")) {
        throw "拒绝非官方许可证来源：$Url"
    }
    Invoke-WebRequest -Uri $uri.AbsoluteUri -OutFile $Destination -TimeoutSec 60
    $text = Get-Content -LiteralPath $Destination -Raw -Encoding UTF8
    if ($text -notmatch $RequiredText) {
        throw "许可证文本校验失败：$Url"
    }
}

$output = [System.IO.Path]::GetFullPath($OutputDirectory)
if (Test-Path -LiteralPath $output) {
    throw "本地 AI 离线载荷输出目录已存在，为保护已有文件拒绝覆盖：$output"
}

$temp = Join-Path ([System.IO.Path]::GetTempPath()) ("aicomics-local-ai-" + [Guid]::NewGuid().ToString("N"))
$runtimeExtract = Join-Path $temp "runtime"
$modelStore = Join-Path $temp "models"
$logs = Join-Path $temp "logs"
$server = $null
$oldModels = $env:OLLAMA_MODELS
$oldHost = $env:OLLAMA_HOST
$oldKeepAlive = $env:OLLAMA_KEEP_ALIVE

try {
    New-Item -ItemType Directory -Path $temp, $logs -Force | Out-Null
    $headers = @{
        Accept = "application/vnd.github+json"
        "X-GitHub-Api-Version" = "2022-11-28"
        "User-Agent" = "AI-Comic-Studio-Offline-Bundle"
    }
    $release = Invoke-RestMethod -Uri "https://api.github.com/repos/ollama/ollama/releases/latest" -Headers $headers -TimeoutSec 30
    if (!$release.tag_name -or !$release.assets) {
        throw "无法读取 Ollama 官方 Release 元数据。"
    }
    $asset = @($release.assets | Where-Object { $_.name -ceq "ollama-windows-amd64.zip" }) | Select-Object -First 1
    if (!$asset -or $asset.digest -notmatch '^sha256:([0-9a-fA-F]{64})$') {
        throw "Ollama 官方 Windows x64 运行包缺少可信 SHA-256。"
    }
    $expectedHash = ([regex]::Match($asset.digest, '^sha256:([0-9a-fA-F]{64})$')).Groups[1].Value.ToLowerInvariant()
    $assetUri = [Uri]$asset.browser_download_url
    $expectedPath = "/ollama/ollama/releases/download/$($release.tag_name)/ollama-windows-amd64.zip"
    if ($assetUri.Scheme -ne "https" -or $assetUri.Host -ne "github.com" -or
        $assetUri.AbsolutePath -cne $expectedPath -or $assetUri.Query -or $assetUri.Fragment) {
        throw "Ollama 运行包下载地址与官方版本不匹配。"
    }

    $runtimeZip = Join-Path $temp "ollama-windows-amd64.zip"
    Write-Host ("下载 Ollama 官方 Windows x64 运行包：{0} ({1:N2} GiB)" -f $release.tag_name, ([long]$asset.size / 1GB))
    Invoke-WebRequest -Uri $assetUri.AbsoluteUri -OutFile $runtimeZip -TimeoutSec 900
    if ((Get-Item -LiteralPath $runtimeZip).Length -ne [long]$asset.size -or
        (Get-Sha256 $runtimeZip) -ne $expectedHash) {
        throw "Ollama 官方运行包大小或 SHA-256 校验失败。"
    }

    Expand-Archive -LiteralPath $runtimeZip -DestinationPath $runtimeExtract -Force
    $ollamaExe = Get-ChildItem -LiteralPath $runtimeExtract -Filter "ollama.exe" -File -Recurse | Select-Object -First 1
    if (!$ollamaExe) {
        throw "Ollama 官方运行包中未找到 ollama.exe。"
    }

    New-Item -ItemType Directory -Path $modelStore -Force | Out-Null
    $env:OLLAMA_MODELS = $modelStore
    $env:OLLAMA_HOST = "127.0.0.1:11435"
    $env:OLLAMA_KEEP_ALIVE = "5m"

    $serverOut = Join-Path $logs "ollama-server.log"
    $serverErr = Join-Path $logs "ollama-server-error.log"
    $server = Start-Process -FilePath $ollamaExe.FullName -ArgumentList @("serve") -WorkingDirectory $ollamaExe.DirectoryName -PassThru -WindowStyle Hidden -RedirectStandardOutput $serverOut -RedirectStandardError $serverErr

    $ready = $false
    for ($attempt = 1; $attempt -le 120; $attempt++) {
        if ($server.HasExited) {
            $diagnostics = (Get-Content $serverErr -Raw -ErrorAction SilentlyContinue) + (Get-Content $serverOut -Raw -ErrorAction SilentlyContinue)
            throw "Ollama 临时服务启动失败：$diagnostics"
        }
        try {
            $tags = Invoke-RestMethod -Uri "http://127.0.0.1:11435/api/tags" -TimeoutSec 2
            if ($null -ne $tags.models) { $ready = $true; break }
        } catch {
            Start-Sleep -Seconds 1
        }
    }
    if (!$ready) {
        throw "Ollama 临时服务未能在 120 秒内就绪。"
    }

    Write-Host "正在通过 Ollama 官方模型库准备 qwen3:4b；这一步会下载约 2.5 GiB 模型数据。"
    & $ollamaExe.FullName pull $ModelName
    if ($LASTEXITCODE -ne 0) {
        throw "Ollama 官方模型下载失败，退出代码：$LASTEXITCODE"
    }

    $tags = Invoke-RestMethod -Uri "http://127.0.0.1:11435/api/tags" -TimeoutSec 10
    $installed = @($tags.models | Where-Object { $_.name -ceq $ModelName -or $_.model -ceq $ModelName })
    if ($installed.Count -ne 1) {
        throw "Ollama 本机模型清单中没有唯一的 $ModelName。"
    }

    $manifestCandidates = @(Get-ChildItem -LiteralPath (Join-Path $modelStore "manifests") -File -Recurse | Where-Object { $_.Directory.Name -ceq "qwen3" -and $_.Name -ceq "4b" })
    if ($manifestCandidates.Count -ne 1) {
        throw "本地 Ollama 模型存储中未找到唯一的 qwen3:4b manifest。"
    }
    $modelManifestPath = $manifestCandidates[0].FullName
    $modelManifest = Get-Content -LiteralPath $modelManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $descriptors = @()
    if ($modelManifest.config) { $descriptors += $modelManifest.config }
    if ($modelManifest.layers) { $descriptors += @($modelManifest.layers) }
    if ($descriptors.Count -lt 1) {
        throw "qwen3:4b 模型 manifest 不包含可核验的内容层。"
    }

    $modelLayers = @()
    foreach ($descriptor in $descriptors) {
        $digestMatch = [regex]::Match([string]$descriptor.digest, '^sha256:([0-9a-fA-F]{64})$')
        if (!$digestMatch.Success -or [long]$descriptor.size -le 0) {
            throw "qwen3:4b manifest 包含无效的模型层摘要或大小。"
        }
        $digest = $digestMatch.Groups[1].Value.ToLowerInvariant()
        $blobPath = Join-Path (Join-Path $modelStore "blobs") ("sha256-" + $digest)
        if (!(Test-Path -LiteralPath $blobPath -PathType Leaf)) {
            throw "qwen3:4b 模型层缺失：$digest"
        }
        $blob = Get-Item -LiteralPath $blobPath
        if ($blob.Length -ne [long]$descriptor.size -or (Get-Sha256 $blobPath) -ne $digest) {
            throw "qwen3:4b 模型层大小或 SHA-256 校验失败：$digest"
        }
        $modelLayers += @{
            digest = "sha256:$digest"
            size_bytes = [long]$descriptor.size
            media_type = [string]$descriptor.mediaType
            file_name = "sha256-$digest"
        }
    }

    if ($server -and !$server.HasExited) {
        Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
        $server.WaitForExit(10000) | Out-Null
    }
    $server = $null

    New-Item -ItemType Directory -Path $output -Force | Out-Null
    Copy-Item -LiteralPath $runtimeZip -Destination (Join-Path $output "ollama-windows-amd64.zip")
    Copy-Item -LiteralPath $modelStore -Destination (Join-Path $output "models") -Recurse

    $noticeDir = Join-Path $output "THIRD_PARTY_NOTICES"
    New-Item -ItemType Directory -Path $noticeDir -Force | Out-Null
    Save-OfficialText -Url "https://raw.githubusercontent.com/ollama/ollama/$($release.tag_name)/LICENSE" -Destination (Join-Path $noticeDir "Ollama-MIT-LICENSE.txt") -RequiredText "MIT License"
    Save-OfficialText -Url "https://www.apache.org/licenses/LICENSE-2.0.txt" -Destination (Join-Path $noticeDir "Qwen3-APACHE-2.0-LICENSE.txt") -RequiredText "Apache License"

    $modelManifestRelative = $modelManifestPath.Substring($modelStore.Length).TrimStart("\", "/").Replace("\", "/")
    $modelFiles = @()
    foreach ($file in Get-ChildItem -LiteralPath (Join-Path $output "models") -File -Recurse) {
        $relative = $file.FullName.Substring((Join-Path $output "models").Length).TrimStart("\", "/").Replace("\", "/")
        $modelFiles += @{
            path = "models/$relative"
            size_bytes = [long]$file.Length
            sha256 = Get-Sha256 $file.FullName
        }
    }
    $modelManifestOutputPath = Join-Path (Join-Path $output "models") ($modelManifestRelative.Replace("/", "\"))
    $modelManifestHash = Get-Sha256 $modelManifestOutputPath
    $modelManifestDigest = "sha256:" + $modelManifestHash
    $manifest = @{
        schema_version = 1
        bundle_type = "internal-offline-local-ai-runtime-and-model"
        distribution_approved = $false
        audit_status = "pending"
        ollama = @{
            version = $release.tag_name
            asset = "ollama-windows-amd64.zip"
            size_bytes = [long]$asset.size
            sha256 = $expectedHash
            source = $asset.browser_download_url
        }
        model = @{
            name = $ModelName
            source = "https://ollama.com/library/qwen3:4b"
            license = "Apache-2.0"
            license_review_status = "pending"
            manifest_path = "models/$modelManifestRelative"
            manifest_sha256 = $modelManifestHash
            manifest_digest = $modelManifestDigest
            layers = $modelLayers
            files = $modelFiles
        }
        notices = @(
            "THIRD_PARTY_NOTICES/Ollama-MIT-LICENSE.txt",
            "THIRD_PARTY_NOTICES/Qwen3-APACHE-2.0-LICENSE.txt"
        )
        model_runtime_mode = "Ollama selects its supported backend at runtime; no GPU acceleration is promised without a device-specific test."
        network_used_during_bundle_creation = $true
        network_required_after_install = $false
    }
    $manifest | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $output "manifest.json") -Encoding UTF8

    Write-Host "本地 AI 离线载荷已生成：$output"
    Write-Host ("Ollama 运行包：{0:N2} GiB" -f ([long]$asset.size / 1GB))
    Write-Host ("模型数据文件：{0} 个，总计 {1:N2} GiB" -f $modelFiles.Count, ((($modelFiles | Measure-Object -Property size_bytes -Sum).Sum) / 1GB))
    Write-Host "模型：$ModelName；许可证审查仍为 pending，载荷仅供内部验证。"
}
finally {
    if ($server -and !$server.HasExited) {
        Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
    }
    if ($null -eq $oldModels) { Remove-Item Env:OLLAMA_MODELS -ErrorAction SilentlyContinue } else { $env:OLLAMA_MODELS = $oldModels }
    if ($null -eq $oldHost) { Remove-Item Env:OLLAMA_HOST -ErrorAction SilentlyContinue } else { $env:OLLAMA_HOST = $oldHost }
    if ($null -eq $oldKeepAlive) { Remove-Item Env:OLLAMA_KEEP_ALIVE -ErrorAction SilentlyContinue } else { $env:OLLAMA_KEEP_ALIVE = $oldKeepAlive }
    if (Test-Path -LiteralPath $temp) {
        Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue
    }
}
