param(
    [Parameter(Mandatory = $true)]
    [string]$SetupExe,
    [Parameter(Mandatory = $true)]
    [string]$OutputZip,
    [string]$ReleaseTag
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

function Add-VerifiedRemoteEntry(
    [System.IO.Compression.ZipArchive]$Archive,
    [System.Net.Http.HttpClient]$Client,
    [string]$EntryName,
    [string]$Url,
    [long]$ExpectedSize,
    [string]$ExpectedSha256
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
        $input = $response.Content.ReadAsStream()
        $hasher = [System.Security.Cryptography.IncrementalHash]::CreateHash([System.Security.Cryptography.HashAlgorithmName]::SHA256)
        $buffer = [byte[]]::new($ChunkBytes)
        [long]$received = 0
        try {
            while (($read = $input.Read($buffer, 0, $buffer.Length)) -gt 0) {
                $received += $read
                if ($received -gt $ExpectedSize) { throw "下载数据超过官方声明大小：$EntryName" }
                $output.Write($buffer, 0, $read)
                $hasher.AppendData($buffer, 0, $read)
                if (($received % (256MB)) -lt $read) {
                    Write-Host ("{0}: {1:N1}% ({2:N2} GiB)" -f $EntryName, ($received * 100.0 / $ExpectedSize), ($received / 1GB))
                }
            }
            $actualHash = [Convert]::ToHexString($hasher.GetHashAndReset()).ToLowerInvariant()
        } finally {
            $hasher.Dispose()
            $input.Dispose()
            $output.Dispose()
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
$setupItem = Get-Item -LiteralPath $SetupExe
if ($setupItem.Length -lt 1MB) { throw "桌面安装程序大小异常。" }

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
Write-Host ("ComfyUI 官方版本：{0}，四种 Windows x64 运行包总计 {1:N2} GiB。" -f $release.tag_name, ($totalBytes / 1GB))
Write-Host ("7-Zip 解压工具版本：{0}。" -f $sevenZipRelease.tag_name)
Write-Host "图片生成模型不包含在本套件中。"

$installScript = Join-Path $PSScriptRoot "install-comfyui-offline.ps1"
if (!(Test-Path -LiteralPath $installScript -PathType Leaf)) { throw "离线安装脚本不存在：$installScript" }

$target = [System.IO.Path]::GetFullPath($OutputZip)
$targetDir = Split-Path -Parent $target
New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Force }

$clientHandler = [System.Net.Http.HttpClientHandler]::new()
$clientHandler.AllowAutoRedirect = $false
$client = [System.Net.Http.HttpClient]::new($clientHandler)
$client.Timeout = [System.Threading.Timeout]::InfiniteTimeSpan
$stream = [System.IO.File]::Open($target, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
$archive = [System.IO.Compression.ZipArchive]::new($stream, [System.IO.Compression.ZipArchiveMode]::Create, $false)
try {
    $manifestResults = @()
    foreach ($asset in $manifestAssets) {
        $manifestResults += Add-VerifiedRemoteEntry $archive $client ("ComfyUI-Payload/" + $asset.name) $asset.url $asset.size_bytes $asset.sha256
    }

    $toolResult = Add-VerifiedRemoteEntry $archive $client "ComfyUI-Payload/7zr.exe" $sevenZipTool.browser_download_url ([long]$sevenZipTool.size) $sevenZipToolDigest
    Add-FileEntry $archive "ComfyUI-Payload/install-comfyui-offline.ps1" $installScript
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
        bundle_type = "complete-offline-comfyui-engine"
        comfyui_version = $release.tag_name
        source = $ReleaseApi
        created_utc = [DateTime]::UtcNow.ToString("o")
        target_architecture = "windows-x64"
        gpu_selection = @{
            nvidia = "ComfyUI_windows_portable_nvidia.7z"
            nvidia_legacy_gtx10_and_older = "ComfyUI_windows_portable_nvidia_cu126.7z"
            amd = "ComfyUI_windows_portable_amd.7z"
            intel = "ComfyUI_windows_portable_intel.7z"
            unknown = "stop-installation-no-network-fallback"
        }
        model_policy = "Image-generation models are separate optional downloads and are not included or downloaded automatically."
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
AI漫剧制作大师 - ComfyUI 完整离线安装套件

1. 解压本 ZIP。
2. 双击根目录中的 Setup.exe。
3. 安装程序会检测显卡，校验并仅部署匹配的一个 ComfyUI Windows x64 运行包。
4. 安装过程不需要联网下载 ComfyUI；所有引擎运行包均已包含在本 ZIP 中。
5. 图片生成模型不包含在本套件中；后续下载模型前必须显示来源、大小、磁盘空间并再次征求确认。
6. 本套件面向 Windows x64。未知显卡或无法识别的硬件会停止安装，不会猜测或联网回退。
7. ComfyUI 采用 GPL-3.0；7-Zip 采用其上游许可证。详见 THIRD_PARTY_NOTICES。完整依赖许可证审查仍是正式公开分发前的必要关卡。

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
}

$zipItem = Get-Item -LiteralPath $target
$zipHash = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash
"$zipHash  $($zipItem.Name)" | Set-Content -LiteralPath "$target.sha256" -Encoding ascii
Write-Host ("离线套件已生成：{0}" -f $target)
Write-Host ("ZIP 大小：{0:N2} GiB" -f ($zipItem.Length / 1GB))
Write-Host "ZIP SHA-256：$zipHash"
