$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$installer = Join-Path $repoRoot "scripts\install-local-ai-offline.ps1"
if (!(Test-Path -LiteralPath $installer -PathType Leaf)) {
    throw "找不到本地 AI 离线安装脚本：$installer"
}

$temp = Join-Path ([System.IO.Path]::GetTempPath()) ("aicomics-local-ai-install-test-" + [Guid]::NewGuid().ToString("N"))
$payload = Join-Path $temp "LocalAI-Payload"
$bundle = Join-Path $temp "bundle"
$installRoot = Join-Path $temp "installed\Ollama"
$runtimeSource = Join-Path $temp "fake-runtime"
$log = Join-Path $temp "install.log"

function Write-Utf8([string]$Path, [string]$Value) {
    $parent = Split-Path -Parent $Path
    if ($parent) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    [System.IO.File]::WriteAllText($Path, $Value, [System.Text.UTF8Encoding]::new($false))
}
function Hash([string]$Path) {
    (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

try {
    $modelManifestRelative = "models/manifests/registry.ollama.ai/library/qwen3/4b"
    $modelManifestPath = Join-Path $payload ($modelManifestRelative.Replace("/", "\"))
    $runtimeZip = Join-Path $payload "ollama-windows-amd64.zip"

    New-Item -ItemType Directory -Path $payload, $bundle, $runtimeSource -Force | Out-Null
    Write-Utf8 (Join-Path $runtimeSource "ollama.exe") "mock ollama executable"
    Compress-Archive -Path (Join-Path $runtimeSource "*") -DestinationPath $runtimeZip -CompressionLevel NoCompression
    $runtimeHash = Hash $runtimeZip

    $blobContent = "mock-model-blob"
    $temporaryBlob = Join-Path $temp "model-blob"
    Write-Utf8 $temporaryBlob $blobContent
    $blobHash = Hash $temporaryBlob
    $blobName = "sha256-" + $blobHash
    $blobPath = Join-Path (Join-Path $payload "models\blobs") $blobName
    Copy-Item -LiteralPath $temporaryBlob -Destination $blobPath
    Write-Utf8 $modelManifestPath '{"schemaVersion":2,"config":{"digest":"sha256:' + $blobHash + '","size":' + ([System.Text.Encoding]::UTF8.GetByteCount($blobContent)) + '},"layers":[]}'

    $modelManifestHash = Hash $modelManifestPath
    $modelFiles = @(
        @{ path = $modelManifestRelative; size_bytes = (Get-Item $modelManifestPath).Length; sha256 = $modelManifestHash },
        @{ path = "models/blobs/$blobName"; size_bytes = (Get-Item $blobPath).Length; sha256 = $blobHash }
    )
    $notice1 = "THIRD_PARTY_NOTICES/Ollama-MIT-LICENSE.txt"
    $notice2 = "THIRD_PARTY_NOTICES/Qwen3-APACHE-2.0-LICENSE.txt"
    Write-Utf8 (Join-Path $payload $notice1) "MIT License"
    Write-Utf8 (Join-Path $payload $notice2) "Apache License Version 2.0"

    $manifest = @{
        schema_version = 1
        bundle_type = "internal-offline-local-ai-runtime-and-model"
        distribution_approved = $false
        audit_status = "pending"
        ollama = @{
            version = "v0.1.0"
            asset = "ollama-windows-amd64.zip"
            size_bytes = (Get-Item $runtimeZip).Length
            sha256 = $runtimeHash
            source = "https://github.com/ollama/ollama/releases/download/v0.1.0/ollama-windows-amd64.zip"
        }
        model = @{
            name = "qwen3:4b"
            license = "Apache-2.0"
            license_review_status = "pending"
            manifest_path = $modelManifestRelative
            manifest_sha256 = $modelManifestHash
            files = $modelFiles
        }
        notices = @($notice1, $notice2)
    }
    $payloadManifestPath = Join-Path $payload "manifest.json"
    Write-Utf8 $payloadManifestPath ($manifest | ConvertTo-Json -Depth 8)
    $payloadManifestHash = Hash $payloadManifestPath

    $bundleComfyui = Join-Path $temp "ComfyUI-Payload"
    New-Item -ItemType Directory -Path $bundleComfyui -Force | Out-Null
    $bundleManifest = @{
        distribution_approved = $false
        local_ai = @{
            model = "qwen3:4b"
            payload_manifest_sha256 = $payloadManifestHash
            runtime_sha256 = $runtimeHash
            model_manifest_sha256 = $modelManifestHash
        }
    }
    Write-Utf8 (Join-Path $bundleComfyui "manifest.json") ($bundleManifest | ConvertTo-Json -Depth 6)

    & $installer -PayloadRoot $payload -InstallRoot $installRoot -LogPath $log
    if (!(Test-Path -LiteralPath (Join-Path $installRoot "runtime\ollama.exe") -PathType Leaf)) {
        throw "离线安装测试失败：Ollama 运行文件未安装。"
    }
    if (!(Test-Path -LiteralPath (Join-Path $installRoot ($modelManifestRelative.Replace("models/", "models/").Replace("/", "\"))) -PathType Leaf)) {
        throw "离线安装测试失败：Qwen3 模型 manifest 未安装。"
    }
    if (!(Test-Path -LiteralPath (Join-Path $installRoot "AI漫剧大师-启动本地AI.bat") -PathType Leaf)) {
        throw "离线安装测试失败：本地 AI 启动脚本未生成。"
    }

    $sentinel = Join-Path $installRoot "user-data-preservation-test.txt"
    Write-Utf8 $sentinel "preserve me"
    & $installer -PayloadRoot $payload -InstallRoot $installRoot -LogPath $log
    if ((Get-Content -LiteralPath $sentinel -Raw) -ne "preserve me") {
        throw "离线安装测试失败：重复安装覆盖了用户数据。"
    }

    $runtimeSourceV2 = Join-Path $temp "fake-runtime-v2"
    New-Item -ItemType Directory -Path $runtimeSourceV2 -Force | Out-Null
    Write-Utf8 (Join-Path $runtimeSourceV2 "ollama.exe") "mock ollama executable v2"
    Compress-Archive -Path (Join-Path $runtimeSourceV2 "*") -DestinationPath $runtimeZip -CompressionLevel NoCompression -Force
    $runtimeHashV2 = Hash $runtimeZip
    $manifest.ollama.version = "v0.2.0"
    $manifest.ollama.source = "https://github.com/ollama/ollama/releases/download/v0.2.0/ollama-windows-amd64.zip"
    $manifest.ollama.size_bytes = (Get-Item $runtimeZip).Length
    $manifest.ollama.sha256 = $runtimeHashV2
    Write-Utf8 $payloadManifestPath ($manifest | ConvertTo-Json -Depth 8)
    $payloadManifestHash = Hash $payloadManifestPath
    $bundleManifest.local_ai.payload_manifest_sha256 = $payloadManifestHash
    $bundleManifest.local_ai.runtime_sha256 = $runtimeHashV2
    Write-Utf8 (Join-Path $bundleComfyui "manifest.json") ($bundleManifest | ConvertTo-Json -Depth 6)

    & $installer -PayloadRoot $payload -InstallRoot $installRoot -LogPath $log
    $installedState = Get-Content -LiteralPath (Join-Path $installRoot "install-manifest.json") -Raw | ConvertFrom-Json
    if ($installedState.ollama_version -ne "v0.2.0" -or
        (Get-Content -LiteralPath (Join-Path $installRoot "runtime\ollama.exe") -Raw) -ne "mock ollama executable v2") {
        throw "离线升级测试失败：Ollama 运行环境未升级到新版本。"
    }
    if ((Get-Content -LiteralPath $sentinel -Raw) -ne "preserve me" -or
        (Hash (Join-Path $installRoot "models\blobs\$blobName")) -ne $blobHash) {
        throw "离线升级测试失败：升级覆盖了用户数据或原有模型 blob。"
    }

    $tamperedPayload = Join-Path $temp "tampered-payload"
    Copy-Item -LiteralPath $payload -Destination $tamperedPayload -Recurse
    Write-Utf8 (Join-Path $tamperedPayload "models\blobs\$blobName") "tampered model blob"
    $tamperedRoot = Join-Path $temp "tampered-install"
    $rejected = $false
    try {
        & $installer -PayloadRoot $tamperedPayload -InstallRoot $tamperedRoot -LogPath (Join-Path $temp "tampered.log")
    } catch {
        $rejected = $true
    }
    if (!$rejected -or (Test-Path -LiteralPath $tamperedRoot)) {
        throw "离线安装测试失败：未拒绝 SHA-256 不匹配的模型文件。"
    }

    Write-Host "本地 AI 离线安装集成测试通过：首次安装、重复安装、运行环境升级、模型/用户数据保护、篡改文件拒绝。"
}
finally {
    if (Test-Path -LiteralPath $temp) {
        Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue
    }
}
