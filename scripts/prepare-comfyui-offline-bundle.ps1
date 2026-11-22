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
$AllowedRedirectHosts = @("codeload.github.com", "github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com", "huggingface.co", "cdn-lfs.huggingface.co", "cas-bridge.xethub.hf.co", "cas-server.xethub.hf.co", "us.aws.cdn.hf.co")
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

function Add-PinnedSourceEntry(
    [System.IO.Compression.ZipArchive]$Archive,
    [System.Net.Http.HttpClient]$Client,
    [string]$EntryName,
    [string]$Url,
    [string]$PinnedCommit,
    [string]$TempPath
) {
    if ($PinnedCommit.Length -ne 40 -or $PinnedCommit -notmatch '^[0-9a-f]+$') { throw "源代码提交 SHA 无效：$EntryName" }
    $uri = Assert-OfficialUri $Url @("codeload.github.com")
    if ($uri.AbsolutePath -notmatch ("/zip/" + [regex]::Escape($PinnedCommit) + "$") -or $uri.Query -or $uri.Fragment) {
        throw "源代码压缩包 URL 未固定到指定提交：$EntryName"
    }
    $response = $null
    for ($redirect = 0; $redirect -le 3; $redirect++) {
        $response = $Client.GetAsync($uri, [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()
        if ([int]$response.StatusCode -in @(301, 302, 303, 307, 308)) {
            $location = $response.Headers.Location
            $response.Dispose()
            if (!$location) { throw "源代码下载重定向缺少 Location：$EntryName" }
            if (!$location.IsAbsoluteUri) { $location = [Uri]::new($uri, $location) }
            $uri = Assert-OfficialUri $location.AbsoluteUri @("codeload.github.com")
            continue
        }
        break
    }
    if (!$response) { throw "未取得固定提交源代码响应：$EntryName" }
    try {
        if (!$response.IsSuccessStatusCode) { throw "固定提交源代码下载失败：$EntryName HTTP $([int]$response.StatusCode)" }
        $input = $response.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
        $output = [System.IO.File]::Open($TempPath, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
        $hasher = [System.Security.Cryptography.IncrementalHash]::CreateHash([System.Security.Cryptography.HashAlgorithmName]::SHA256)
        $buffer = [byte[]]::new($ChunkBytes)
        [long]$received = 0
        try {
            while (($read = $input.Read($buffer, 0, $buffer.Length)) -gt 0) {
                $received += $read
                if ($received -gt 2GB) { throw "固定提交源代码包超过 2 GiB：$EntryName" }
                $output.Write($buffer, 0, $read)
                $hasher.AppendData($buffer, 0, $read)
            }
            if ($received -lt 1024) { throw "固定提交源代码包大小异常：$EntryName" }
            $digest = [BitConverter]::ToString($hasher.GetHashAndReset()).Replace("-", "").ToLowerInvariant()
        } finally {
            $hasher.Dispose()
            $output.Dispose()
            $input.Dispose()
        }
        Add-FileEntry $Archive $EntryName $TempPath
        return @{ path = $EntryName; size_bytes = $received; sha256 = $digest; source_commit = $PinnedCommit }
    } finally { $response.Dispose() }
}

function Get-TextSha256([string]$Text) {
    $bytes = [System.Text.UTF8Encoding]::new($false).GetBytes($Text)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($sha.ComputeHash($bytes)).Replace("-", "").ToLowerInvariant() }
    finally { $sha.Dispose() }
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

    $currentUri = Assert-OfficialUri $Url @("github.com", "huggingface.co")
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
$estimatedBundleBytes = [long]$totalBytes + [long]$localAIPayloadBytes + [long]$sevenZipTool.size + [long]$sevenZipSource.size + [long]$setupItem.Length + $largestAssetBytes + [long]2132696762 + 1536MB
$target = [System.IO.Path]::GetFullPath($OutputZip)
$checksumTarget = "$target.sha256"
$stagingTarget = "$target.$([Guid]::NewGuid().ToString('N')).partial"
$stagingChecksum = "$stagingTarget.sha256"
if ((Test-Path -LiteralPath $target) -or (Test-Path -LiteralPath $checksumTarget)) {
    throw "输出文件或 SHA-256 校验文件已存在：$target。为保护已有离线安装套件，底层打包脚本不会覆盖或删除它。若确认重新制作，请先通过 create-comfyui-offline-installer.ps1 -Force 明确删除旧包及其校验文件。"
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
$stream = [System.IO.File]::Open($stagingTarget, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
$archive = [System.IO.Compression.ZipArchive]::new($stream, [System.IO.Compression.ZipArchiveMode]::Create, $false)
$inventoryTempRoot = $null
try {
    $inventoryTempRoot = Join-Path $targetDir (".comfyui-audit-" + [Guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $inventoryTempRoot -Force | Out-Null

    $manifestResults = @()
    $inventoryReports = @()
    $sevenZipTempPath = Join-Path $inventoryTempRoot "7zr.exe"
    $toolResult = Add-VerifiedRemoteEntry $archive $client "ComfyUI-Payload/7zr.exe" $sevenZipTool.browser_download_url ([long]$sevenZipTool.size) $sevenZipToolDigest $sevenZipTempPath

    # Pinned Comfy-Org Stable Diffusion 1.5 FP16 checkpoint for first-run offline image generation.
    # The source revision, exact byte length and SHA-256 are pinned to prevent mutable-branch drift.
    $imageModelName = "v1-5-pruned-emaonly-fp16.safetensors"
    $imageModelRevision = "ce8e3e3657a9b767b33fc0171ff91fb04aed1b2f"
    $imageModelUrl = "https://huggingface.co/Comfy-Org/stable-diffusion-1.5/resolve/$imageModelRevision/$imageModelName"
    $imageModelSize = [long]2132696762
    $imageModelSha256 = "e9476a13728cd75d8279f6ec8bad753a66a1957ca375a1464dc63b37db6e3916"
    $imageModelEntry = "ComfyUI-Payload/models/checkpoints/$imageModelName"
    $imageModelResult = Add-VerifiedRemoteEntry $archive $client $imageModelEntry $imageModelUrl $imageModelSize $imageModelSha256 $null

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
        $singleVariant = @($singleReport.portable_variants)[0]
        $singleVariant.archive_path = "ComfyUI-Payload/$($asset.name)"
        $singleReport.portable_variants = @($singleVariant)
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
Add-FileEntry $archive "ComfyUI-Payload/install-local-engines-transaction.ps1" (Join-Path $PSScriptRoot "install-local-engines-transaction.ps1")
    Add-FileEntry $archive "ComfyUI-Payload/audit-comfyui-portable-dependencies.ps1" $auditScript

    $bundleAuditRegister = Get-Content -LiteralPath $auditPath -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($registerVariant in @($bundleAuditRegister.portable_variants)) {
        $inventoryVariant = @($inventoryBase.portable_variants | Where-Object { $_.asset -ceq [string]$registerVariant.asset })
        if ($inventoryVariant.Count -ne 1) {
            throw "审查登记表与本次四种 Portable 依赖清点结果无法对应：$($registerVariant.asset)"
        }
        $registerVariant.inventory_status = [string]$inventoryVariant[0].inventory_status
        $registerVariant.sha256 = [string]$inventoryVariant[0].sha256
        $registerVariant.release_tag = [string]$inventoryVariant[0].release_tag
        $registerVariant.components = $inventoryVariant[0].components
        # The checked-in audit register intentionally has a stable schema and
        # may not yet contain generated evidence fields. Add them safely rather
        # than assigning a property that does not exist on a PSCustomObject.
        $registerVariant | Add-Member -NotePropertyName inventory_report_path -NotePropertyValue $inventoryReportPath -Force
        $registerVariant | Add-Member -NotePropertyName inventory_generated_utc -NotePropertyValue ([string]$inventoryBase.generated_utc) -Force
    }
    $bundleAuditRegister.release_blockers[0] = "Manually review the generated file inventory for each official Windows portable archive and identify bundled redistributable components not surfaced by automated metadata scanning."
    $bundleAuditRegister.release_blockers[1] = "Review embedded Python package metadata and license evidence for completeness; confirm package licenses and notices manually."
    $bundleAuditRegister.release_blockers[2] = "Review PyTorch, torchvision, torchaudio, CUDA/ROCm/XPU runtime libraries and their separate license and notice obligations for each GPU variant."
    $bundleAuditRegister.release_blockers[8] = "Manually review the recorded Qwen3 4B manifest digest and blob SHA-256 inventory against the exact upstream model revision and license."
    $bundleAuditRegister.release_blockers += "Run a clean Windows installation and verify the included runtime, model store, third-party notices, and post-install offline operation."
    $bundleAuditRegister.release_blockers += "Review Stable Diffusion 1.5 checkpoint provenance, CreativeML OpenRAIL-M downstream restriction notice, and model distribution obligations before public release."
    $bundleAuditRegister.known_components[0].status = "license_text_included_inventory_generated_manual_license_review_pending"
    $bundleAuditRegister.local_ai_payload.runtime_version = [string]$localAIManifest.ollama.version
    $bundleAuditRegister.local_ai_payload.runtime_sha256 = [string]$localAIManifest.ollama.sha256
    $bundleAuditRegister.local_ai_payload.runtime_audit_status = "runtime_digest_verified_dependency_audit_pending"
    $bundleAuditRegister.local_ai_payload.model_manifest_sha256 = [string]$localAIManifest.model.manifest_sha256
    $bundleAuditRegister.local_ai_payload.model_blob_inventory_status = "manifest_and_blob_sha256_verified_license_review_pending"
    Add-TextEntry $archive "THIRD_PARTY_NOTICES/comfyui-portable-dependency-audit.json" ($bundleAuditRegister | ConvertTo-Json -Depth 12)
    Add-FileEntry $archive "LocalAI-Payload/install-local-ai-offline.ps1" $localAIInstallScript
    foreach ($localAIFile in $localAIPayloadFiles) {
        $relative = $localAIFile.FullName.Substring($LocalAIPayloadRoot.Length).TrimStart("\", "/").Replace("\", "/")
        Add-FileEntry $archive ("LocalAI-Payload/" + $relative) $localAIFile.FullName
    }
    $sevenZipSourceResult = Add-VerifiedRemoteEntry $archive $client "THIRD_PARTY_NOTICES/$($sevenZipSource.name)" $sevenZipSource.browser_download_url ([long]$sevenZipSource.size) $sevenZipSourceDigest

    $imageLicenseUrl = "https://raw.githubusercontent.com/CompVis/stable-diffusion/main/LICENSE"
    $imageLicenseUri = Assert-OfficialUri $imageLicenseUrl @("raw.githubusercontent.com")
    $imageLicenseResponse = $client.GetAsync($imageLicenseUri).GetAwaiter().GetResult()
    try {
        if (!$imageLicenseResponse.IsSuccessStatusCode) { throw "无法读取 Stable Diffusion CreativeML OpenRAIL-M 许可证。" }
        $imageLicenseText = $imageLicenseResponse.Content.ReadAsStringAsync().GetAwaiter().GetResult()
        if ($imageLicenseText -notmatch "CreativeML Open RAIL-M" -or $imageLicenseText -notmatch "Attachment A") {
            throw "Stable Diffusion 模型许可证文本校验失败。"
        }
        Add-TextEntry $archive "THIRD_PARTY_NOTICES/Stable-Diffusion-CreativeML-OpenRAIL-M-LICENSE.txt" $imageLicenseText
    } finally { $imageLicenseResponse.Dispose() }

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

    # Include complete source archives pinned to the exact upstream release commits.
    # Hashes are computed from the bytes downloaded over HTTPS and recorded in the manifest.
    $comfyAimdoSourceCommit = "68088de9dce9f5e32231fccf230938769c5562da"
    $frontendSourceCommit = "c62d747f378e0fa277cbb5137bffaf282598c64c"
    $comfyAimdoSourceResult = Add-PinnedSourceEntry $archive $client "THIRD_PARTY_NOTICES/source-code/comfy-aimdo-v0.6.0-68088de9.zip" "https://codeload.github.com/Comfy-Org/comfy-aimdo/zip/$comfyAimdoSourceCommit" $comfyAimdoSourceCommit (Join-Path $inventoryTempRoot "comfy-aimdo-source.zip")
    $frontendSourceResult = Add-PinnedSourceEntry $archive $client "THIRD_PARTY_NOTICES/source-code/ComfyUI_frontend-v1.59.6-c62d747f.zip" "https://codeload.github.com/Comfy-Org/ComfyUI_frontend/zip/$frontendSourceCommit" $frontendSourceCommit (Join-Path $inventoryTempRoot "comfyui-frontend-source.zip")

    # Resolve the pinned frontend workspace's production dependency tree and retain
    # every detected package LICENSE/COPYING/NOTICE file with a hash inventory.
    $frontendExtractRoot = Join-Path $inventoryTempRoot "comfyui-frontend-source-extracted"
    New-Item -ItemType Directory -Path $frontendExtractRoot -Force | Out-Null
    Expand-Archive -LiteralPath (Join-Path $inventoryTempRoot "comfyui-frontend-source.zip") -DestinationPath $frontendExtractRoot -Force
    $frontendRootCandidates = @(Get-ChildItem -LiteralPath $frontendExtractRoot -Directory | Where-Object {
        Test-Path -LiteralPath (Join-Path $_.FullName "package.json") -PathType Leaf
    })
    if ($frontendRootCandidates.Count -ne 1) { throw "固定版本 ComfyUI Frontend 源代码包根目录数量异常。" }
    $frontendSourceRoot = $frontendRootCandidates[0].FullName
    $frontendAuditScript = Join-Path $PSScriptRoot "audit-comfyui-frontend-npm-licenses.ps1"
    if (!(Test-Path -LiteralPath $frontendAuditScript -PathType Leaf)) { throw "ComfyUI Frontend npm 许可证清点脚本不存在。" }
    $frontendAuditOutput = Join-Path $inventoryTempRoot "comfyui-frontend-npm-license-audit"
    & $frontendAuditScript -SourceRoot $frontendSourceRoot -OutputRoot $frontendAuditOutput -SourceCommit $frontendSourceCommit
    $frontendAuditReportPath = Join-Path $frontendAuditOutput "frontend-npm-dependency-licenses.json"
    $frontendAuditSummaryPath = Join-Path $frontendAuditOutput "frontend-npm-dependency-summary.json"
    if (!(Test-Path -LiteralPath $frontendAuditReportPath -PathType Leaf) -or !(Test-Path -LiteralPath $frontendAuditSummaryPath -PathType Leaf)) {
        throw "ComfyUI Frontend npm 许可证清点未生成完整报告。"
    }
    $frontendAuditSummary = Get-Content -LiteralPath $frontendAuditSummaryPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($frontendAuditSummary.source_commit -cne $frontendSourceCommit -or
        $frontendAuditSummary.distribution_approved -ne $false -or
        [int]$frontendAuditSummary.package_count -lt 1) {
        throw "ComfyUI Frontend npm 许可证清点报告的来源或审核状态无效。"
    }
    $frontendAuditLicenseFiles = @(Get-ChildItem -LiteralPath (Join-Path $frontendAuditOutput "licenses") -File -Recurse)
    $frontendAuditBundleFiles = @()
    foreach ($auditFile in @(Get-ChildItem -LiteralPath $frontendAuditOutput -File -Recurse)) {
        $relative = $auditFile.FullName.Substring($frontendAuditOutput.Length).TrimStart("\", "/").Replace("\", "/")
        $entryName = "THIRD_PARTY_NOTICES/ComfyUI-Frontend-NPM/$relative"
        Add-FileEntry $archive $entryName $auditFile.FullName
        $fileHash = (Get-FileHash -LiteralPath $auditFile.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        $frontendAuditBundleFiles += @{
            path = $entryName
            installed_path = "licenses/ComfyUI-Frontend-NPM/$relative"
            size_bytes = [long]$auditFile.Length
            sha256 = $fileHash
        }
    }
    if ($frontendAuditLicenseFiles.Count -lt 1) { throw "ComfyUI Frontend npm 依赖未发现任何许可证/NOTICE 文件，停止打包以便调查。" }
    $frontendNpmAuditManifest = @{
        report_file = "THIRD_PARTY_NOTICES/ComfyUI-Frontend-NPM/frontend-npm-dependency-licenses.json"
        summary_file = "THIRD_PARTY_NOTICES/ComfyUI-Frontend-NPM/frontend-npm-dependency-summary.json"
        package_count = [int]$frontendAuditSummary.package_count
        license_file_count = [int]$frontendAuditSummary.license_file_count
        packages_missing_license_expression = [int]$frontendAuditSummary.packages_missing_license_expression
        packages_without_detected_license_or_notice_file = [int]$frontendAuditSummary.packages_without_detected_license_or_notice_file
        source_lockfile_sha256 = [string]$frontendAuditSummary.source_lockfile_sha256
        status = "inventory_generated_manual_license_review_pending"
        distribution_approved = $false
        report_sha256 = (Get-FileHash -LiteralPath $frontendAuditReportPath -Algorithm SHA256).Hash.ToLowerInvariant()
        summary_sha256 = (Get-FileHash -LiteralPath $frontendAuditSummaryPath -Algorithm SHA256).Hash.ToLowerInvariant()
        files = $frontendAuditBundleFiles
    }

    # GPL source licenses for the exact ComfyUI frontend and comfy-aimdo versions
    # found in the v0.46.0 portable archives. Immutable source commits prevent tag drift.
    $comfyAimdoCommit = "68088de9dce9f5e32231fccf230938769c5562da"
    $comfyAimdoLicenseUrl = "https://raw.githubusercontent.com/Comfy-Org/comfy-aimdo/$comfyAimdoCommit/LICENSE"
    $comfyAimdoLicenseUri = Assert-OfficialUri $comfyAimdoLicenseUrl @("raw.githubusercontent.com")
    $comfyAimdoLicenseResponse = $client.GetAsync($comfyAimdoLicenseUri).GetAwaiter().GetResult()
    try {
        if (!$comfyAimdoLicenseResponse.IsSuccessStatusCode) { throw "无法读取 comfy-aimdo v0.6.0 GPL-3.0 许可证。" }
        $comfyAimdoLicenseText = $comfyAimdoLicenseResponse.Content.ReadAsStringAsync().GetAwaiter().GetResult()
        if ($comfyAimdoLicenseText -notmatch "GNU GENERAL PUBLIC LICENSE" -or $comfyAimdoLicenseText -notmatch "Version 3") {
            throw "comfy-aimdo GPL-3.0 许可证文本校验失败。"
        }
        Add-TextEntry $archive "THIRD_PARTY_NOTICES/comfy-aimdo-GPL-3.0-LICENSE.txt" $comfyAimdoLicenseText
    } finally { $comfyAimdoLicenseResponse.Dispose() }

    $frontendCommit = "c62d747f378e0fa277cbb5137bffaf282598c64c"
    $frontendLicenseUrl = "https://raw.githubusercontent.com/Comfy-Org/ComfyUI_frontend/$frontendCommit/LICENSE"
    $frontendLicenseUri = Assert-OfficialUri $frontendLicenseUrl @("raw.githubusercontent.com")
    $frontendLicenseResponse = $client.GetAsync($frontendLicenseUri).GetAwaiter().GetResult()
    try {
        if (!$frontendLicenseResponse.IsSuccessStatusCode) { throw "无法读取 ComfyUI Frontend v1.59.6 GPL-3.0 许可证。" }
        $frontendLicenseText = $frontendLicenseResponse.Content.ReadAsStringAsync().GetAwaiter().GetResult()
        if ($frontendLicenseText -notmatch "GNU GENERAL PUBLIC LICENSE" -or $frontendLicenseText -notmatch "Version 3") {
            throw "ComfyUI Frontend GPL-3.0 许可证文本校验失败。"
        }
        Add-TextEntry $archive "THIRD_PARTY_NOTICES/ComfyUI-Frontend-GPL-3.0-LICENSE.txt" $frontendLicenseText
    } finally { $frontendLicenseResponse.Dispose() }

    $frontendNoticeUrl = "https://raw.githubusercontent.com/Comfy-Org/ComfyUI_frontend/$frontendCommit/THIRD_PARTY_NOTICES.md"
    $frontendNoticeUri = Assert-OfficialUri $frontendNoticeUrl @("raw.githubusercontent.com")
    $frontendNoticeResponse = $client.GetAsync($frontendNoticeUri).GetAwaiter().GetResult()
    try {
        if (!$frontendNoticeResponse.IsSuccessStatusCode) { throw "无法读取 ComfyUI Frontend 第三方商标通知。" }
        $frontendNoticeText = $frontendNoticeResponse.Content.ReadAsStringAsync().GetAwaiter().GetResult()
        if ($frontendNoticeText -notmatch "AMD") { throw "ComfyUI Frontend 第三方通知文本校验失败。" }
        Add-TextEntry $archive "THIRD_PARTY_NOTICES/ComfyUI-Frontend-THIRD-PARTY-NOTICES.md" $frontendNoticeText
    } finally { $frontendNoticeResponse.Dispose() }

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
        model_policy = "The pinned Stable Diffusion 1.5 FP16 checkpoint is included for offline image generation. CreativeML OpenRAIL-M use restrictions and distribution review remain pending; this bundle is internal validation only."
        image_generation_model = @{
            name = "Stable Diffusion 1.5 FP16"
            file = $imageModelEntry
            source = $imageModelUrl
            revision = $imageModelRevision
            size_bytes = $imageModelSize
            sha256 = $imageModelSha256
            license = "CreativeML OpenRAIL-M"
            license_file = "THIRD_PARTY_NOTICES/Stable-Diffusion-CreativeML-OpenRAIL-M-LICENSE.txt"
            license_review_status = "pending"
            distribution_approved = $false
        }
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
        comfy_aimdo = @{
            version = "0.6.0"
            license = "GPL-3.0"
            license_file = "THIRD_PARTY_NOTICES/comfy-aimdo-GPL-3.0-LICENSE.txt"
            license_sha256 = (Get-TextSha256 $comfyAimdoLicenseText)
            source_repository = "https://github.com/Comfy-Org/comfy-aimdo"
            source_commit = $comfyAimdoCommit
            source_archive = $comfyAimdoSourceResult.path
            source_archive_sha256 = $comfyAimdoSourceResult.sha256
            source_archive_size_bytes = $comfyAimdoSourceResult.size_bytes
            source_code_status = "upstream_commit_pinned_source_archive_included"
            exact_wheel_sha256 = "45f821b5828c4b38bcac6553f2250b56e6db87bd65334ed37eb938985d809a3c"
            exact_wheel_hash_match_status = "pending"
        }
        comfyui_frontend = @{
            version = "1.59.6"
            license = "GPL-3.0-only"
            license_file = "THIRD_PARTY_NOTICES/ComfyUI-Frontend-GPL-3.0-LICENSE.txt"
            license_sha256 = (Get-TextSha256 $frontendLicenseText)
            third_party_notice_file = "THIRD_PARTY_NOTICES/ComfyUI-Frontend-THIRD-PARTY-NOTICES.md"
            third_party_notice_sha256 = (Get-TextSha256 $frontendNoticeText)
            source_repository = "https://github.com/Comfy-Org/ComfyUI_frontend"
            source_commit = $frontendCommit
            source_archive = $frontendSourceResult.path
            source_archive_sha256 = $frontendSourceResult.sha256
            source_archive_size_bytes = $frontendSourceResult.size_bytes
            source_code_status = "upstream_commit_pinned_source_archive_included"
            exact_wheel_sha256 = "7564e716da2d1e122612f067bcffda6c3b9bc766f3b9f8c85fc44cf1d9c54f85"
            exact_wheel_hash_match_status = "pending"
            frontend_dependency_notice_review_status = "pending"
            npm_dependency_audit = $frontendNpmAuditManifest
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
6. 本套件已包含 Stable Diffusion 1.5 FP16 图片生成检查点（约 2.13 GB）及 CreativeML Open RAIL-M 完整许可证。桌面端已加入本地完整许可证展示、使用限制确认记录和生图 API 门禁；该机制仍需法律审查，整套依赖审查完成前仅供内部验证，不得公开或客户分发。
7. 本套件面向 Windows x64。ComfyUI 只对官方文档明确覆盖的 GPU 型号启用加速；未确认的设备使用 CPU 模式或停止安装。
8. ComfyUI、ComfyUI Frontend、comfy-aimdo、7-Zip、Ollama 和 Qwen3 及其依赖许可证均须逐项审核；Frontend 与 comfy-aimdo 的 GPL-3.0 许可证文本及固定提交源代码压缩包已附带，但二进制与源代码对应关系、前端传递依赖通知仍需核实。审查完成前本套件仅供内部验证，不得公开或客户分发。
9. ComfyUI 依赖清点工具位于 ComfyUI-Payload\\audit-comfyui-portable-dependencies.ps1；本地 AI 安装脚本位于 LocalAI-Payload\\install-local-ai-offline.ps1。自动清点不等于许可证批准。

本 ZIP 的构建过程会校验每个官方资产的文件长度和 SHA-256。任何一项校验失败都会终止构建并删除不完整 ZIP。
'@
} catch {
    $archive.Dispose()
    $stream.Dispose()
    $client.Dispose()
    if (Test-Path -LiteralPath $stagingTarget) { Remove-Item -LiteralPath $stagingTarget -Force }
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
$verifyStream = [System.IO.File]::OpenRead($stagingTarget)
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
    $expectedEntries += @{ path = $imageModelEntry; size = [long]$imageModelResult.size_bytes; sha256 = [string]$imageModelResult.sha256 }
    $transactionInstaller = Join-Path $PSScriptRoot "install-local-engines-transaction.ps1"
    if (!(Test-Path -LiteralPath $transactionInstaller -PathType Leaf)) { throw "整套本地引擎事务安装脚本不存在：$transactionInstaller" }
    $expectedEntries += @{ path = "ComfyUI-Payload/install-local-engines-transaction.ps1"; size = (Get-Item -LiteralPath $transactionInstaller).Length; sha256 = (Get-FileHash -LiteralPath $transactionInstaller -Algorithm SHA256).Hash.ToLowerInvariant() }
    $expectedEntries += @{ path = "THIRD_PARTY_NOTICES/$($sevenZipSource.name)"; size = [long]$sevenZipSourceResult.size_bytes; sha256 = [string]$sevenZipSourceResult.sha256 }
    $expectedEntries += @{ path = [string]$comfyAimdoSourceResult.path; size = [long]$comfyAimdoSourceResult.size_bytes; sha256 = [string]$comfyAimdoSourceResult.sha256 }
    $expectedEntries += @{ path = [string]$frontendSourceResult.path; size = [long]$frontendSourceResult.size_bytes; sha256 = [string]$frontendSourceResult.sha256 }
    $expectedEntries += @{ path = "THIRD_PARTY_NOTICES/comfy-aimdo-GPL-3.0-LICENSE.txt"; size = [System.Text.UTF8Encoding]::new($false).GetByteCount($comfyAimdoLicenseText); sha256 = (Get-TextSha256 $comfyAimdoLicenseText) }
    $expectedEntries += @{ path = "THIRD_PARTY_NOTICES/ComfyUI-Frontend-GPL-3.0-LICENSE.txt"; size = [System.Text.UTF8Encoding]::new($false).GetByteCount($frontendLicenseText); sha256 = (Get-TextSha256 $frontendLicenseText) }
    $expectedEntries += @{ path = "THIRD_PARTY_NOTICES/ComfyUI-Frontend-THIRD-PARTY-NOTICES.md"; size = [System.Text.UTF8Encoding]::new($false).GetByteCount($frontendNoticeText); sha256 = (Get-TextSha256 $frontendNoticeText) }
    foreach ($frontendAuditFile in $frontendAuditBundleFiles) {
        $expectedEntries += @{
            path = [string]$frontendAuditFile.path
            size = [long]$frontendAuditFile.size_bytes
            sha256 = [string]$frontendAuditFile.sha256
        }
    }
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
        "ComfyUI-Payload/models/checkpoints/v1-5-pruned-emaonly-fp16.safetensors",
        "THIRD_PARTY_NOTICES/Stable-Diffusion-CreativeML-OpenRAIL-M-LICENSE.txt",
        "LocalAI-Payload/manifest.json",
        "LocalAI-Payload/ollama-windows-amd64.zip",
        "LocalAI-Payload/install-local-ai-offline.ps1",
        "LocalAI-Payload/THIRD_PARTY_NOTICES/Ollama-MIT-LICENSE.txt",
        "LocalAI-Payload/THIRD_PARTY_NOTICES/Qwen3-APACHE-2.0-LICENSE.txt",
        "LocalAI-Payload/THIRD_PARTY_NOTICES/Qwen3-MODEL-NOTICE.txt",
        "ComfyUI-Payload/install-comfyui-offline.ps1",
        "ComfyUI-Payload/install-local-engines-transaction.ps1",
        "ComfyUI-Payload/audit-comfyui-portable-dependencies.ps1",
        "THIRD_PARTY_NOTICES/ComfyUI-GPL-3.0-LICENSE.txt",
        "THIRD_PARTY_NOTICES/source-code/comfy-aimdo-v0.6.0-68088de9.zip",
        "THIRD_PARTY_NOTICES/source-code/ComfyUI_frontend-v1.59.6-c62d747f.zip",
        "THIRD_PARTY_NOTICES/ComfyUI-Frontend-NPM/frontend-npm-dependency-licenses.json",
        "THIRD_PARTY_NOTICES/ComfyUI-Frontend-NPM/frontend-npm-dependency-summary.json",
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

    $inventoryEntry = $verifyArchive.GetEntry("THIRD_PARTY_NOTICES/comfyui-portable-dependency-inventory.json")
    $inventoryReader = [System.IO.StreamReader]::new($inventoryEntry.Open(), [System.Text.Encoding]::UTF8)
    try { $inventoryText = $inventoryReader.ReadToEnd() } finally { $inventoryReader.Dispose() }
    $inventoryObject = $inventoryText | ConvertFrom-Json
    $inventoryVariants = @($inventoryObject.portable_variants)
    $inventoryNames = @($inventoryVariants | ForEach-Object { [string]$_.asset } | Sort-Object -Unique)
    $inventoryDifferences = @(Compare-Object -ReferenceObject $AssetNames -DifferenceObject $inventoryNames)
    if ($inventoryObject.audit_status -ne "pending" -or
        $inventoryObject.distribution_approved -ne $false -or
        $inventoryObject.release_tag -cne $release.tag_name -or
        $inventoryVariants.Count -ne 4 -or $inventoryDifferences.Count -gt 0) {
        throw "完整离线 ZIP 中的四种 Portable 依赖清点报告未通过完整性检查。"
    }

    $bundleManifestEntry = $verifyArchive.GetEntry("ComfyUI-Payload/manifest.json")
    $bundleManifestReader = [System.IO.StreamReader]::new($bundleManifestEntry.Open(), [System.Text.Encoding]::UTF8)
    try { $bundleManifestText = $bundleManifestReader.ReadToEnd() } finally { $bundleManifestReader.Dispose() }
    $bundleManifestCheck = $bundleManifestText | ConvertFrom-Json
    if ($bundleManifestCheck.dependency_inventory_report -cne "THIRD_PARTY_NOTICES/comfyui-portable-dependency-inventory.json" -or
        $bundleManifestCheck.comfyui_version -cne $inventoryObject.release_tag) {
        throw "ComfyUI 主清单与依赖清点报告版本或路径不匹配。"
    }
    $bundleAssetItems = @($bundleManifestCheck.assets)
    foreach ($variant in $inventoryVariants) {
        $matchingAsset = @($bundleAssetItems | Where-Object { $_.name -ceq [string]$variant.asset })
        if ($matchingAsset.Count -ne 1 -or
            [string]$matchingAsset[0].sha256 -ne [string]$variant.sha256 -or
            [long]$matchingAsset[0].size_bytes -ne [long]$variant.archive_size_bytes) {
            throw "最终 ZIP 的依赖清点报告与主清单资产校验值不匹配：$($variant.asset)"
        }
    }
} catch {
    if (Test-Path -LiteralPath $stagingTarget) { Remove-Item -LiteralPath $stagingTarget -Force -ErrorAction SilentlyContinue }
    throw
} finally {
    if ($verifyArchive) { $verifyArchive.Dispose() }
    $verifyStream.Dispose()
}

# Publish only after the ZIP has passed its full read-back and SHA-256 checks.
# The final paths are never exposed while the archive is still being written.
$zipHash = (Get-FileHash -LiteralPath $stagingTarget -Algorithm SHA256).Hash
$zipName = [System.IO.Path]::GetFileName($target)
$checksumText = "$zipHash  $zipName" + [Environment]::NewLine
[System.IO.File]::WriteAllText($stagingChecksum, $checksumText, [System.Text.Encoding]::ASCII)
if ((Test-Path -LiteralPath $target) -or (Test-Path -LiteralPath $checksumTarget)) {
    Remove-Item -LiteralPath $stagingTarget, $stagingChecksum -Force -ErrorAction SilentlyContinue
    throw "发布前检测到目标 ZIP 或校验文件已出现；为保护已有文件，取消发布。"
}
[System.IO.File]::Move($stagingTarget, $target)
try {
    [System.IO.File]::Move($stagingChecksum, $checksumTarget)
} catch {
    Remove-Item -LiteralPath $target -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $stagingChecksum -Force -ErrorAction SilentlyContinue
    throw
}
$zipItem = Get-Item -LiteralPath $target
Write-Host ("离线套件已生成：{0}" -f $target)
Write-Host ("ZIP 大小：{0:N2} GiB" -f ($zipItem.Length / 1GB))
Write-Host "ZIP SHA-256：$zipHash"
