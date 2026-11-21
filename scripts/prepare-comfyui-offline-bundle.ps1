param(
    [Parameter(Mandatory = $true)]
    [string]$SetupExe,
    [Parameter(Mandatory = $true)]
    [string]$OutputZip,
    [string]$ReleaseTag,
    [Parameter(Mandatory = $true)]
    [string]$LocalAIPayloadRoot
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$AssetNames = @(
    "ComfyUI_windows_portable_nvidia.7z",
    "ComfyUI_windows_portable_nvidia_cu126.7z",
    "ComfyUI_windows_portable_amd.7z",
    "ComfyUI_windows_portable_intel.7z"
)
$AllowedRedirectHosts = @("github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com")
$ReleaseApi = "https://api.github.com/repos/Comfy-Org/ComfyUI/releases/latest"
$SevenZipApi = "https://api.github.com/repos/ip7z/7zip/releases/latest"
$MaxAssetBytes = 12GB
$ChunkBytes = 1MB

function Assert-OfficialUri([string]$Url, [string[]]$Hosts) {
    $uri = [Uri]$Url
    if ($uri.Scheme -ne "https" -or $uri.UserInfo -or $uri.Port -ne 443 -or $Hosts -notcontains $uri.Host.ToLowerInvariant()) {
        throw "拒绝非官方 HTTPS 地址：$Url"
    }
    return $uri
}

function Add-FileEntry([System.IO.Compression.ZipArchive]$Archive, [string]$EntryName, [string]$SourcePath) {
    $entry = $Archive.CreateEntry($EntryName, [System.IO.Compression.CompressionLevel]::NoCompression)
    $input = [System.IO.File]::OpenRead($SourcePath)
    $output = $entry.Open()
    try { $input.CopyTo($output, $ChunkBytes) }
    finally { $output.Dispose(); $input.Dispose() }
}

function Add-TextEntry([System.IO.Compression.ZipArchive]$Archive, [string]$EntryName, [string]$Text) {
    $entry = $Archive.CreateEntry($EntryName, [System.IO.Compression.CompressionLevel]::NoCompression)
    $output = $entry.Open()
    try {
        $bytes = [System.Text.UTF8Encoding]::new($false).GetBytes($Text)
        $output.Write($bytes, 0, $bytes.Length)
    } finally { $output.Dispose() }
}

function Get-ZipEntrySha256([System.IO.Compression.ZipArchiveEntry]$Entry) {
    $input = $Entry.Open()
    $hasher = [System.Security.Cryptography.IncrementalHash]::CreateHash([System.Security.Cryptography.HashAlgorithmName]::SHA256)
    $buffer = [byte[]]::new($ChunkBytes)
    try {
        while (($read = $input.Read($buffer, 0, $buffer.Length)) -gt 0) {
            $hasher.AppendData($buffer, 0, $read)
        }
        return [BitConverter]::ToString($hasher.GetHashAndReset()).Replace("-", "").ToLowerInvariant()
    } finally {
        $hasher.Dispose()
        $input.Dispose()
    }
}

function Add-VerifiedRemoteEntry(
    [System.IO.Compression.ZipArchive]$Archive,
    [System.Net.Http.HttpClient]$Client,
    [string]$EntryName,
    [string]$Url,
    [long]$ExpectedSize,
    [string]$ExpectedSha256,
    [string]$MirrorPath
) {
    if ($ExpectedSize -le 0 -or $ExpectedSize -gt $MaxAssetBytes) {
        throw "官方资产大小无效或超过限制：$EntryName"
    }
    if ($ExpectedSha256 -notmatch '^[0-9a-fA-F]{64}$') {
        throw "官方资产缺少有效 SHA-256：$EntryName"
    }

    $currentUri = Assert-OfficialUri $Url @("github.com")
    $response = $null
    for ($redirect = 0; $redirect -le 5; $redirect++) {
        $response = $Client.GetAsync($currentUri, [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()
        if ([int]$response.StatusCode -in @(301, 302, 303, 307, 308)) {
            $location = $response.Headers.Location
            $response.Dispose()
            if (!$location) { throw "官方资源重定向缺少 Location：$EntryName" }
            if (!$location.IsAbsoluteUri) { $location = [Uri]::new($currentUri, $location) }
            $currentUri = Assert-OfficialUri $location.AbsoluteUri $AllowedRedirectHosts
            continue
        }
        break
    }
    if (!$response) { throw "未取得官方资产响应：$EntryName" }

    try {
        if (!$response.IsSuccessStatusCode) {
            throw "官方资产下载失败：$EntryName HTTP $([int]$response.StatusCode)"
        }
        $contentLength = $response.Content.Headers.ContentLength
        if ($null -ne $contentLength -and [long]$contentLength -ne $ExpectedSize) {
            throw "官方响应长度与元数据不一致：$EntryName"
        }

        $entry = $Archive.CreateEntry($EntryName, [System.IO.Compression.CompressionLevel]::NoCompression)
        $output = $entry.Open()
        $input = $response.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
        $mirror = $null
        $hasher = [System.Security.Cryptography.IncrementalHash]::CreateHash([System.Security.Cryptography.HashAlgorithmName]::SHA256)
        $buffer = [byte[]]::new($ChunkBytes)
        [long]$received = 0
        try {
            if ($MirrorPath) {
                $mirrorParent = Split-Path -Parent $MirrorPath
                if ($mirrorParent) { New-Item -ItemType Directory -Path $mirrorParent -Force | Out-Null }
                $mirror = [System.IO.File]::Open($MirrorPath, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
            }
            while (($read = $input.Read($buffer, 0, $buffer.Length)) -gt 0) {
                $received += $read
                if ($received -gt $ExpectedSize) { throw "下载数据超过官方声明大小：$EntryName" }
                $output.Write($buffer, 0, $read)
                if ($mirror) { $mirror.Write($buffer, 0, $read) }
                $hasher.AppendData($buffer, 0, $read)
                if (($received % (256MB)) -lt $read) {
                    Write-Host ("{0}: {1:N1}% ({2:N2} GiB)" -f $EntryName, ($received * 100.0 / $ExpectedSize), ($received / 1GB))
                }
            }
            $actualHash = [BitConverter]::ToString($hasher.GetHashAndReset()).Replace("-", "").ToLowerInvariant()
        } finally {
            $hasher.Dispose()
            $input.Dispose()
            $output.Dispose()
            if ($mirror) { $mirror.Dispose() }
        }
        if ($received -ne $ExpectedSize) { throw "下载文件长度不匹配：$EntryName ($received / $ExpectedSize)" }
        if ($actualHash -ne $ExpectedSha256.ToLowerInvariant()) { throw "SHA-256 校验失败：$EntryName" }
        return @{
            name = [System.IO.Path]::GetFileName($EntryName)
            size_bytes = $received
            sha256 = $actualHash
            source = $Url
            verified = $true
        }
    } finally { $response.Dispose() }
}

function Get-RequiredSha256($Asset, [string]$Label) {
    if (!$Asset -or $Asset.digest -notmatch '^sha256:([0-9a-fA-F]{64})$') {
        throw "官方资产缺少可信 SHA-256：$Label"
    }
    return ([regex]::Match($Asset.digest, '^sha256:([0-9a-fA-F]{64})$')).Groups[1].Value.ToLowerInvariant()
}

if (!(Test-Path -LiteralPath $SetupExe -PathType Leaf)) { throw "找不到桌面安装程序：$SetupExe" }
$auditPath = Join-Path $PSScriptRoot "..\docs\compliance\comfyui-portable-dependency-audit.json"
$auditPath = [System.IO.Path]::GetFullPath($auditPath)
if (!(Test-Path -LiteralPath $auditPath -PathType Leaf)) {
    throw "缺少第三方依赖许可证审查登记表：$auditPath"
}
$auditRegister = Get-Content -LiteralPath $auditPath -Raw | ConvertFrom-Json
if ($auditRegister.distribution_approved -ne $false -or $auditRegister.audit_status -ne "pending") {
    throw "第三方依赖审查状态与当前内部构建策略不一致；请人工复核登记表后再构建。"
}
Write-Warning "第三方依赖许可证审查尚未完成：本次离线套件仅供内部验证，禁止公开或客户分发。"
$setupItem = Get-Item -LiteralPath $SetupExe
if ($setupItem.Length -lt 1MB) { throw "桌面安装程序大小异常。" }

$LocalAIPayloadRoot = (Resolve-Path -LiteralPath $LocalAIPayloadRoot -ErrorAction Stop).Path
$localAIManifestPath = Join-Path $LocalAIPayloadRoot "manifest.json"
$localAIRuntimePath = Join-Path $LocalAIPayloadRoot "ollama-windows-amd64.zip"
if (!(Test-Path -LiteralPath $localAIManifestPath -PathType Leaf) -or
    !(Test-Path -LiteralPath $localAIRuntimePath -PathType Leaf) -or
    !(Test-Path -LiteralPath (Join-Path $LocalAIPayloadRoot "models") -PathType Container)) {
    throw "本地 AI 离线载荷不完整，缺少 manifest、Ollama 运行包或模型目录。"
}
$localAIManifest = Get-Content -LiteralPath $localAIManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($localAIManifest.bundle_type -ne "internal-offline-local-ai-runtime-and-model" -or
    $localAIManifest.distribution_approved -ne $false -or
    $localAIManifest.audit_status -ne "pending" -or
    $localAIManifest.model.name -cne "qwen3:4b") {
    throw "本地 AI 离线载荷的版本、模型或许可证审查门禁状态不匹配。"
}
$localAIPayloadFiles = @(Get-ChildItem -LiteralPath $LocalAIPayloadRoot -File -Recurse)
if ($localAIPayloadFiles.Count -lt 4 -or $localAIPayloadFiles.Count -gt 1000) {
    throw "本地 AI 离线载荷文件数量无效或超过安全上限。"
}
$localAIPayloadBytes = ($localAIPayloadFiles | Measure-Object -Property Length -Sum).Sum

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.Net.Http

$headers = @{
    Accept = "application/vnd.github+json"
    "X-GitHub-Api-Version" = "2022-11-28"
    "User-Agent" = "AI-Comic-Studio-Offline-Bundle"
}

$release = Invoke-RestMethod -Uri $ReleaseApi -Headers $headers -TimeoutSec 30
if (!$release.tag_name -or !$release.assets) { throw "Comfy-Org 官方 Release 元数据无效。" }
if ($ReleaseTag -and $ReleaseTag -ne $release.tag_name) { throw "指定版本与官方最新版本不一致。" }

$manifestAssets = @()
foreach ($name in $AssetNames) {
    $asset = @($release.assets | Where-Object { $_.name -ceq $name }) | Select-Object -First 1
    if (!$asset) { throw "官方版本 $($release.tag_name) 缺少必需资产：$name" }
    $digest = Get-RequiredSha256 $asset $name
    $expectedPath = "/Comfy-Org/ComfyUI/releases/download/$($release.tag_name)/$name"
    $uri = Assert-OfficialUri $asset.browser_download_url @("github.com")
    if ($uri.AbsolutePath -cne $expectedPath -or $uri.Query -or $uri.Fragment) {
        throw "官方资产 URL 与版本或文件名不匹配：$name"
    }
    $manifestAssets += @{
        name = $name
        url = $asset.browser_download_url
        size_bytes = [long]$asset.size
        sha256 = $digest
    }
}

$sevenZipRelease = Invoke-RestMethod -Uri $SevenZipApi -Headers $headers -TimeoutSec 30
if (!$sevenZipRelease.tag_name -or !$sevenZipRelease.assets) { throw "7-Zip 官方 Release 元数据无效。" }
$sevenZipTool = @($sevenZipRelease.assets | Where-Object { $_.name -ceq "7zr.exe" }) | Select-Object -First 1
$sevenZipSource = @($sevenZipRelease.assets | Where-Object { $_.name -match '^7z[0-9]+-src\.7z$' }) | Select-Object -First 1
if (!$sevenZipTool -or !$sevenZipSource) { throw "官方 7-Zip Release 缺少 7zr.exe 或对应源代码包。" }
$sevenZipToolDigest = Get-RequiredSha256 $sevenZipTool "7zr.exe"
$sevenZipSourceDigest = Get-RequiredSha256 $sevenZipSource $sevenZipSource.name
foreach ($toolAsset in @($sevenZipTool, $sevenZipSource)) {
    $toolUri = Assert-OfficialUri $toolAsset.browser_download_url @("github.com")
    $expectedToolPath = "/ip7z/7zip/releases/download/$($sevenZipRelease.tag_name)/$($toolAsset.name)"
    if ($toolUri.AbsolutePath -cne $expectedToolPath -or $toolUri.Query -or $toolUri.Fragment) {
        throw "7-Zip 资产 URL 不匹配：$($toolAsset.name)"
    }
}

$totalBytes = ($manifestAssets | Measure-Object -Property size_bytes -Sum).Sum
$largestAssetBytes = [long](($manifestAssets | Measure-Object -Property size_bytes -Maximum).Maximum)
# The largest archive is mirrored to temporary storage while its dependency inventory is generated.
$estimatedBundleBytes = [long]$totalBytes + [long]$localAIPayloadBytes + [long]$sevenZipTool.size + [long]$sevenZipSource.size + [long]$setupItem.Length + $largestAssetBytes + 1GB
$target = [System.IO.Path]::GetFullPath($OutputZip)
if (Test-Path -LiteralPath $target) {
    throw "输出文件已存在：$target。为保护已有离线安装套件，底层打包脚本不会覆盖或删除它。若确认重新制作，请先通过 create-comfyui-offline-installer.ps1 -Force 明确删除旧包。"
}
$targetDir = Split-Path -Parent $target
New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
$targetDrive = [System.IO.Path]::GetPathRoot($target)
$targetDriveInfo = [System.IO.DriveInfo]::new($targetDrive)
if ($targetDriveInfo.AvailableFreeSpace -lt $estimatedBundleBytes) {
    throw ("输出盘空间不足。制作完整 ZIP 至少需要 {0:N2} GiB 可用空间，当前仅有 {1:N2} GiB。" -f ($estimatedBundleBytes / 1GB), ($targetDriveInfo.AvailableFreeSpace / 1GB))
}
Write-Host ("ComfyUI 官方版本：{0}，四种 Windows x64 运行包总计 {1:N2} GiB。" -f $release.tag_name, ($totalBytes / 1GB))
Write-Host ("7-Zip 解压工具版本：{0}。" -f $sevenZipRelease.tag_name)
Write-Host ("本地 AI 载荷：Ollama {0} + {1}，共 {2:N2} GiB。" -f $localAIManifest.ollama.version, $localAIManifest.model.name, ($localAIPayloadBytes / 1GB))
Write-Host "本地 AI 和 ComfyUI 模型/运行包均随 ZIP 提供；许可证审查未完成前仅供内部验证。"

$installScript = Join-Path $PSScriptRoot "install-comfyui-offline.ps1"
if (!(Test-Path -LiteralPath $installScript -PathType Leaf)) { throw "离线安装脚本不存在：$installScript" }

$auditScript = Join-Path $PSScriptRoot "audit-comfyui-portable-dependencies.ps1"
if (!(Test-Path -LiteralPath $auditScript -PathType Leaf)) { throw "依赖清点脚本不存在：$auditScript" }

$localAIInstallScript = Join-Path $PSScriptRoot "install-local-ai-offline.ps1"
if (!(Test-Path -LiteralPath $localAIInstallScript -PathType Leaf)) { throw "本地 AI 离线安装脚本不存在：$localAIInstallScript" }


$clientHandler = [System.Net.Http.HttpClientHandler]::new()
$clientHandler.AllowAutoRedirect = $false
$client = [System.Net.Http.HttpClient]::new($clientHandler)
$client.Timeout = [System.Threading.Timeout]::InfiniteTimeSpan
$stream = [System.IO.File]::Open($target, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
$archive = [System.IO.Compression.ZipArchive]::new($stream, [System.IO.Compression.ZipArchiveMode]::Create, $false)
$inventoryTempRoot = $null
try {
    $inventoryTempRoot = Join-Path $targetDir (".comfyui-audit-" + [Guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $inventoryTempRoot -Force | Out-Null

    $manifestResults = @()
    $inventoryReports = @()
    $sevenZipTempPath = Join-Path $inventoryTempRoot "7zr.exe"
    $toolResult = Add-VerifiedRemoteEntry $archive $client "ComfyUI-Payload/7zr.exe" $sevenZipTool.browser_download_url ([long]$sevenZipTool.size) $sevenZipToolDigest $sevenZipTempPath

    foreach ($asset in $manifestAssets) {
        $assetTempPath = Join-Path $inventoryTempRoot $asset.name
        $reportTempPath = Join-Path $inventoryTempRoot ($asset.name + ".inventory.json")
        $manifestResults += Add-VerifiedRemoteEntry $archive $client ("ComfyUI-Payload/" + $asset.name) $asset.url $asset.size_bytes $asset.sha256 $assetTempPath
        & $auditScript -SevenZipPath $sevenZipTempPath -ArchivePaths @($assetTempPath) -ReleaseTag $release.tag_name -OutputPath $reportTempPath
        if (!(Test-Path -LiteralPath $reportTempPath -PathType Leaf)) {
            throw "ComfyUI 官方资产依赖清点未生成报告：$($asset.name)"
        }
        $singleReport = Get-Content -LiteralPath $reportTempPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($singleReport.audit_status -ne "pending" -or $singleReport.distribution_approved -ne $false -or
            $singleReport.release_tag -ne $release.tag_name -or @($singleReport.portable_variants).Count -ne 1) {
            throw "ComfyUI 官方资产依赖清点报告无效或意外批准了分发：$($asset.name)"
        }
        $inventoryReports += $singleReport
        Remove-Item -LiteralPath $assetTempPath, $reportTempPath -Force
    }

    if ($inventoryReports.Count -ne 4) { throw "必须为四种 ComfyUI Portable 运行包分别生成依赖清点报告。" }
    $inventoryBase = $inventoryReports[0]
    $inventoryBase.portable_variants = @($inventoryReports | ForEach-Object { @($_.portable_variants)[0] })
    $inventoryBase.generated_utc = [DateTime]::UtcNow.ToString("o")
    $inventoryBase.audit_status = "pending"
    $inventoryBase.distribution_approved = $false
    $inventoryReportPath = "THIRD_PARTY_NOTICES/comfyui-portable-dependency-inventory.json"
    Add-TextEntry $archive $inventoryReportPath ($inventoryBase | ConvertTo-Json -Depth 12)
    Add-FileEntry $archive "ComfyUI-Payload/install-comfyui-offline.ps1" $installScript
    Add-FileEntry $archive "ComfyUI-Payload/audit-comfyui-portable-dependencies.ps1" $auditScript
    Add-FileEntry $archive "THIRD_PARTY_NOTICES/comfyui-portable-dependency-audit.json" $auditPath
    Add-FileEntry $archive "LocalAI-Payload/install-local-ai-offline.ps1" $localAIInstallScript
    foreach ($localAIFile in $localAIPayloadFiles) {
        $relative = $localAIFile.FullName.Substring($LocalAIPayloadRoot.Length).TrimStart("\", "/").Replace("\", "/")
        Add-FileEntry $archive ("LocalAI-Payload/" + $relative) $localAIFile.FullName
    }
    $sevenZipSourceResult = Add-VerifiedRemoteEntry $archive $client "THIRD_PARTY_NOTICES/$($sevenZipSource.name)" $sevenZipSource.browser_download_url ([long]$sevenZipSource.size) $sevenZipSourceDigest

    $licenseUrl = "https://raw.githubusercontent.com/Comfy-Org/ComfyUI/$($release.tag_name)/LICENSE"
    $licenseUri = Assert-OfficialUri $licenseUrl @("raw.githubusercontent.com")
    $licenseResponse = $client.GetAsync($licenseUri).GetAwaiter().GetResult()
    try {
        if (!$licenseResponse.IsSuccessStatusCode) { throw "无法读取该版本 ComfyUI GPL-3.0 许可证文本。" }
        $licenseText = $licenseResponse.Content.ReadAsStringAsync().GetAwaiter().GetResult()
        if ($licenseText -notmatch "GNU GENERAL PUBLIC LICENSE" -or $licenseText -notmatch "Version 3") {
            throw "ComfyUI 许可证内容校验失败。"
        }
        Add-TextEntry $archive "THIRD_PARTY_NOTICES/ComfyUI-GPL-3.0-LICENSE.txt" $licenseText
    } finally { $licenseResponse.Dispose() }

    $sevenZipLicenseUrl = "https://raw.githubusercontent.com/ip7z/7zip/$($sevenZipRelease.tag_name)/DOC/License.txt"
    $sevenZipLicenseUri = Assert-OfficialUri $sevenZipLicenseUrl @("raw.githubusercontent.com")
    $sevenZipLicenseResponse = $client.GetAsync($sevenZipLicenseUri).GetAwaiter().GetResult()
    try {
        if (!$sevenZipLicenseResponse.IsSuccessStatusCode) { throw "无法读取 7-Zip 官方许可证文本。" }
        $sevenZipLicenseText = $sevenZipLicenseResponse.Content.ReadAsStringAsync().GetAwaiter().GetResult()
        if ($sevenZipLicenseText -notmatch "7-Zip source code" -or $sevenZipLicenseText -notmatch "GNU LGPL") {
            throw "7-Zip 许可证内容校验失败。"
        }
        Add-TextEntry $archive "THIRD_PARTY_NOTICES/7-Zip-LICENSE.txt" $sevenZipLicenseText
    } finally { $sevenZipLicenseResponse.Dispose() }

    $manifest = @{
        schema_version = 1
        product = "AI漫剧制作大师"
        bundle_type = "complete-offline-comfyui-engine-internal-validation-only"
        distribution_status = "blocked-pending-third-party-dependency-audit"
        distribution_approved = $false
        dependency_audit_register = "THIRD_PARTY_NOTICES/comfyui-portable-dependency-audit.json"
        dependency_inventory_tool = "ComfyUI-Payload/audit-comfyui-portable-dependencies.ps1"
        dependency_inventory_report = "THIRD_PARTY_NOTICES/comfyui-portable-dependency-inventory.json"
        comfyui_version = $release.tag_name
        source = $ReleaseApi
        created_utc = [DateTime]::UtcNow.ToString("o")
        target_architecture = "windows-x64"
        gpu_selection = @{
            nvidia_current = "NVIDIA models other than GTX 10 series and older: ComfyUI_windows_portable_nvidia.7z"
            nvidia_legacy = "GTX 10 series and older: ComfyUI_windows_portable_nvidia_cu126.7z"
            amd = "RX 6000/7000/9000, Ryzen AI Max, and Radeon AI PRO R9700: ComfyUI_windows_portable_amd.7z"
            intel = "Intel Arc only: ComfyUI_windows_portable_intel.7z"
            unknown = "stop-installation-no-network-fallback"
        }
        model_policy = "ComfyUI image-generation checkpoints remain optional and are not included; the selected local text model qwen3:4b and its Ollama runtime are included in LocalAI-Payload for offline installation. Model redistribution review remains pending."
        local_ai = @{
            runtime_version = $localAIManifest.ollama.version
            runtime_asset = "LocalAI-Payload/ollama-windows-amd64.zip"
            runtime_sha256 = $localAIManifest.ollama.sha256
            payload_manifest_sha256 = (Get-FileHash -LiteralPath $localAIManifestPath -Algorithm SHA256).Hash.ToLowerInvariant()
            model = $localAIManifest.model.name
            model_manifest_path = "LocalAI-Payload/$($localAIManifest.model.manifest_path)"
            model_manifest_sha256 = $localAIManifest.model.manifest_sha256
            cloud_features_disabled = $localAIManifest.cloud_features_disabled
            license = $localAIManifest.model.license
            license_review_status = "pending"
            distribution_approved = $false
        }
        assets = $manifestResults
        tools = @($toolResult)
        license = @{
            spdx_id = "GPL-3.0"
            license_file = "THIRD_PARTY_NOTICES/ComfyUI-GPL-3.0-LICENSE.txt"
            upstream_source = "https://github.com/Comfy-Org/ComfyUI/tree/$($release.tag_name)"
            note = "This notice covers ComfyUI itself only. A full dependency/license audit is required before public distribution."
        }
        seven_zip = @{
            version = $sevenZipRelease.tag_name
            executable = "ComfyUI-Payload/7zr.exe"
            source_archive = "THIRD_PARTY_NOTICES/$($sevenZipSource.name)"
            source_url = "https://github.com/ip7z/7zip/tree/$($sevenZipRelease.tag_name)"
            source_sha256 = $sevenZipSourceResult.sha256
            source_size_bytes = $sevenZipSourceResult.size_bytes
            license_file = "THIRD_PARTY_NOTICES/7-Zip-LICENSE.txt"
        }
    }
    Add-TextEntry $archive "ComfyUI-Payload/manifest.json" ($manifest | ConvertTo-Json -Depth 8)

    Add-FileEntry $archive "Setup.exe" $setupItem.FullName
    $setupHash = (Get-FileHash -LiteralPath $setupItem.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    Add-TextEntry $archive "Setup.exe.sha256" "$setupHash  Setup.exe"
    Add-TextEntry $archive "安装说明.txt" @'
AI漫剧制作大师 - 完整离线安装套件（内部验证版）

1. 解压本 ZIP。
2. 双击根目录中的 Setup.exe。
3. 完整 ZIP 预计约 11 GiB，解压和安装时还需要额外空间；安装器会分别检查 ComfyUI 和本地 AI 的磁盘空间。
4. 安装程序会检测显卡，校验并部署匹配的一个 ComfyUI Windows x64 运行包；另会部署内置 Ollama 运行环境和 Qwen3 4B 文本模型。
5. 安装时不需要联网下载 ComfyUI、Ollama 或 Qwen3 4B；上述运行环境和文本模型均已包含在 ZIP 中。
6. 图片生成检查点不包含在本套件中；后续下载前必须显示来源、大小、磁盘空间并再次征求确认。
7. 本套件面向 Windows x64。ComfyUI 只对官方文档明确覆盖的 GPU 型号启用加速；未确认的设备使用 CPU 模式或停止安装。
8. ComfyUI、7-Zip、Ollama 和 Qwen3 及其依赖许可证均须逐项审核。审查完成前本套件仅供内部验证，不得公开或客户分发。
9. ComfyUI 依赖清点工具位于 ComfyUI-Payload\\audit-comfyui-portable-dependencies.ps1；本地 AI 安装脚本位于 LocalAI-Payload\\install-local-ai-offline.ps1。自动清点不等于许可证批准。

本 ZIP 的构建过程会校验每个官方资产的文件长度和 SHA-256。任何一项校验失败都会终止构建并删除不完整 ZIP。
'@
} catch {
    $archive.Dispose()
    $stream.Dispose()
    $client.Dispose()
    if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Force }
    throw
} finally {
    $archive.Dispose()
    $stream.Dispose()
    $client.Dispose()
    if ($inventoryTempRoot -and (Test-Path -LiteralPath $inventoryTempRoot)) {
        Remove-Item -LiteralPath $inventoryTempRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# Re-open the finished ZIP and verify its central directory, required entries,
# lengths and SHA-256 values after packaging. This catches truncated/corrupt ZIP output.
$verifyStream = [System.IO.File]::OpenRead($target)
$verifyArchive = $null
try {
    $verifyArchive = [System.IO.Compression.ZipArchive]::new($verifyStream, [System.IO.Compression.ZipArchiveMode]::Read, $false)
    $entryNames = @($verifyArchive.Entries | ForEach-Object { $_.FullName })
    if ($entryNames.Count -ne @($entryNames | Select-Object -Unique).Count) {
        throw "完整离线 ZIP 中存在重复文件条目。"
    }

    $expectedEntries = @()
    foreach ($assetResult in $manifestResults) {
        $expectedEntries += @{
            path = "ComfyUI-Payload/$($assetResult.name)"
            size = [long]$assetResult.size_bytes
            sha256 = [string]$assetResult.sha256
        }
    }
    $expectedEntries += @{ path = "ComfyUI-Payload/7zr.exe"; size = [long]$toolResult.size_bytes; sha256 = [string]$toolResult.sha256 }
    $expectedEntries += @{ path = "THIRD_PARTY_NOTICES/$($sevenZipSource.name)"; size = [long]$sevenZipSourceResult.size_bytes; sha256 = [string]$sevenZipSourceResult.sha256 }
    $expectedEntries += @{ path = "Setup.exe"; size = [long]$setupItem.Length; sha256 = $setupHash }
    foreach ($localAIFile in $localAIPayloadFiles) {
        $relative = $localAIFile.FullName.Substring($LocalAIPayloadRoot.Length).TrimStart("\", "/").Replace("\", "/")
        $expectedEntries += @{
            path = "LocalAI-Payload/$relative"
            size = [long]$localAIFile.Length
            sha256 = (Get-FileHash -LiteralPath $localAIFile.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    }
    $expectedEntries += @{
        path = "LocalAI-Payload/install-local-ai-offline.ps1"
        size = (Get-Item -LiteralPath $localAIInstallScript).Length
        sha256 = (Get-FileHash -LiteralPath $localAIInstallScript -Algorithm SHA256).Hash.ToLowerInvariant()
    }

    foreach ($expected in $expectedEntries) {
        $matches = @($verifyArchive.Entries | Where-Object { $_.FullName -ceq $expected.path })
        if ($matches.Count -ne 1 -or [long]$matches[0].Length -ne $expected.size) {
            throw "离线 ZIP 条目缺失或大小不匹配：$($expected.path)"
        }
        $actualHash = Get-ZipEntrySha256 $matches[0]
        if ($actualHash -ne $expected.sha256.ToLowerInvariant()) {
            throw "离线 ZIP 条目 SHA-256 校验失败：$($expected.path)"
        }
    }

    foreach ($requiredName in @(
        "ComfyUI-Payload/manifest.json",
        "LocalAI-Payload/manifest.json",
        "LocalAI-Payload/ollama-windows-amd64.zip",
        "LocalAI-Payload/install-local-ai-offline.ps1",
        "LocalAI-Payload/THIRD_PARTY_NOTICES/Ollama-MIT-LICENSE.txt",
        "LocalAI-Payload/THIRD_PARTY_NOTICES/Qwen3-APACHE-2.0-LICENSE.txt",
        "LocalAI-Payload/THIRD_PARTY_NOTICES/Qwen3-MODEL-NOTICE.txt",
        "ComfyUI-Payload/install-comfyui-offline.ps1",
        "ComfyUI-Payload/audit-comfyui-portable-dependencies.ps1",
        "THIRD_PARTY_NOTICES/ComfyUI-GPL-3.0-LICENSE.txt",
        "THIRD_PARTY_NOTICES/7-Zip-LICENSE.txt",
        "THIRD_PARTY_NOTICES/comfyui-portable-dependency-audit.json",
        "THIRD_PARTY_NOTICES/comfyui-portable-dependency-inventory.json",
        "安装说明.txt",
        "Setup.exe.sha256"
    )) {
        if (@($entryNames | Where-Object { $_ -ceq $requiredName }).Count -ne 1) {
            throw "离线 ZIP 缺少必要文件：$requiredName"
        }
    }
} finally {
    if ($verifyArchive) { $verifyArchive.Dispose() }
    $verifyStream.Dispose()
}

$zipItem = Get-Item -LiteralPath $target
$zipHash = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash
"$zipHash  $($zipItem.Name)" | Set-Content -LiteralPath "$target.sha256" -Encoding ascii
Write-Host ("离线套件已生成：{0}" -f $target)
Write-Host ("ZIP 大小：{0:N2} GiB" -f ($zipItem.Length / 1GB))
Write-Host "ZIP SHA-256：$zipHash"
