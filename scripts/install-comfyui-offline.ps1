param(
    [Parameter(Mandatory = $true)]
    [string]$PayloadRoot,
    [Parameter(Mandatory = $true)]
    [string]$InstallRoot,
    [string]$LogPath = (Join-Path $env:TEMP "AIComicStudio-ComfyUI-install.log"),
    [object[]]$DetectedAdapters,
    [switch]$ValidateOnly
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$transcriptStarted = $false
try {
    $logDirectory = Split-Path -Parent ([System.IO.Path]::GetFullPath($LogPath))
    if ($logDirectory) { New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null }
    Start-Transcript -Path $LogPath -Append -ErrorAction Stop | Out-Null
    $transcriptStarted = $true
} catch {
    Write-Warning "无法创建 ComfyUI 安装日志：$($_.Exception.Message)"
}

function Get-Sha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-GpuVendor($Adapter) {
    $pnp = [string]$Adapter.PNPDeviceID
    $name = [string]$Adapter.Name
    if ($pnp -match "VEN_10DE" -or $name -match "(?i)NVIDIA|GeForce|Quadro") { return "NVIDIA" }
    if ($pnp -match "VEN_1002" -or $name -match "(?i)AMD|Radeon") { return "AMD" }
    if ($pnp -match "VEN_8086" -or $name -match "(?i)Intel|Arc Graphics") { return "Intel" }
    return "unknown"
}

function Select-ComfyAsset($Adapters) {
    $recognized = @()
    foreach ($adapter in $Adapters) {
        $vendor = Get-GpuVendor $adapter
        if ($vendor -ne "unknown") {
            $recognized += [pscustomobject]@{
                Name = [string]$adapter.Name
                Vendor = $vendor
            }
        }
    }

    # Prefer NVIDIA discrete GPUs on hybrid laptops, then AMD, then Intel.
    $nvidia = @($recognized | Where-Object Vendor -eq "NVIDIA")
    if ($nvidia.Count -gt 0) {
        $model = $nvidia[0].Name
        $gtx = [regex]::Match($model, "(?i)GTX\s*([0-9]{3,4})")
        if ($gtx.Success -and [int]$gtx.Groups[1].Value -le 1099) {
            return @{
                name = "ComfyUI_windows_portable_nvidia_cu126.7z"
                detected_gpu = $model
                vendor = "NVIDIA"
                runtime_mode = "gpu"
                selection_reason = "GTX 10 系列及更旧型号，使用官方 cu126 运行包"
            }
        }
        return @{
            name = "ComfyUI_windows_portable_nvidia.7z"
            detected_gpu = $model
            vendor = "NVIDIA"
            runtime_mode = "gpu"
            selection_reason = "除 GTX 10 系列及更旧型号外，使用官方标准 NVIDIA 运行包"
        }
    }

    $amd = @($recognized | Where-Object Vendor -eq "AMD")
    if ($amd.Count -gt 0) {
        $model = $amd[0].Name
        if ($model -match "(?i)RX\s*(?:6|7|9)[0-9]{3}" -or
            $model -match "(?i)Ryzen AI Max" -or
            $model -match "(?i)Radeon AI PRO R9700") {
            return @{
                name = "ComfyUI_windows_portable_amd.7z"
                detected_gpu = $model
                vendor = "AMD"
                runtime_mode = "gpu"
                selection_reason = "检测到已列入官方 Windows AMD 加速支持范围的 GPU 系列"
            }
        }

        # Older Radeon Vega APUs (including Ryzen 7 4800U Radeon Graphics)
        # are not assumed to support the current Windows ROCm GPU path.
        # The official AMD portable also supports CPU-only execution.
        return @{
            name = "ComfyUI_windows_portable_amd.7z"
            detected_gpu = $model
            vendor = "AMD"
            runtime_mode = "cpu"
            selection_reason = "该 AMD 集成显卡未列入已确认的 GPU 加速支持范围，安全使用官方便携包 CPU 模式"
        }
    }

    $intel = @($recognized | Where-Object Vendor -eq "Intel")
    if ($intel.Count -gt 0) {
        $model = $intel[0].Name
        if ($model -match "(?i)\bArc\b") {
            return @{
                name = "ComfyUI_windows_portable_intel.7z"
                detected_gpu = $model
                vendor = "Intel"
                runtime_mode = "gpu"
                selection_reason = "检测到 Intel Arc GPU，使用官方 Intel 运行包"
            }
        }
        return @{
            name = "ComfyUI_windows_portable_intel.7z"
            detected_gpu = $intel[0].Name
            vendor = "Intel"
            runtime_mode = "cpu"
            selection_reason = "未识别到 Intel Arc 加速型号，使用官方便携包 CPU 模式"
        }
    }

    throw "没有识别到受支持的 NVIDIA、AMD 或 Intel GPU。安装已停止，不会猜测运行包，也不会联网下载。"
}

function Get-ArchiveEntries([string]$SevenZip, [string]$ArchivePath) {
    # Bound native listing output before parsing to avoid unbounded memory use
    # when an archive contains an unexpectedly large directory table.
    $maximumEntries = 50000
    $maximumListingLines = ([long]$maximumEntries * 12) + 1000
    $listing = New-Object System.Collections.Generic.List[string]
    & $SevenZip l -slt -- $ArchivePath 2>&1 | ForEach-Object {
        if ($listing.Count -ge $maximumListingLines) {
            throw "ComfyUI 运行包目录清单超过安全上限 $maximumListingLines 行。"
        }
        $listing.Add([string]$_)
    }
    if ($LASTEXITCODE -ne 0) { throw "ComfyUI 运行包目录清单读取失败。" }
    $blocks = @()
    $current = @{}
    foreach ($line in $listing) {
        $text = [string]$line
        if ($text -match "^Path = (.*)$") {
            if ($current.Count -gt 0) { $blocks += ,$current }
            $current = @{ Path = $Matches[1] }
            continue
        }
        if ($text -match "^([^=]+?) = (.*)$") {
            $current[$Matches[1].Trim()] = $Matches[2]
        }
    }
    if ($current.Count -gt 0) { $blocks += ,$current }

    $entries = @($blocks | Where-Object { $_.Path -and $_.ContainsKey("Size") })
    if ($entries.Count -lt 1 -or $entries.Count -gt $maximumEntries) {
        throw "ComfyUI 压缩包文件数量无效或超过安全限制。"
    }

    [long]$totalSize = 0
    foreach ($entry in $entries) {
        $relative = [string]$entry.Path
        $normalized = $relative.Replace("/", "\")
        $parts = @($normalized.Split("\"))
        $invalidWindowsName = $false
        foreach ($part in $parts) {
            if ([string]::IsNullOrWhiteSpace($part) -or
                $part.EndsWith(".") -or $part.EndsWith(" ") -or
                $part -match "^(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)" -or
                $part.IndexOfAny([System.IO.Path]::GetInvalidFileNameChars()) -ge 0) {
                $invalidWindowsName = $true
                break
            }
        }
        if ($normalized.Length -gt 240 -or $parts.Count -gt 64 -or
            [System.IO.Path]::IsPathRooted($normalized) -or
            $normalized -match "^[A-Za-z]:" -or
            $normalized.StartsWith("\") -or
            $parts -contains ".." -or
            $normalized.Contains(":") -or
            $invalidWindowsName) {
            throw "压缩包包含不安全路径，已停止安装：$relative"
        }
        if ($entry.ContainsKey("Symbolic Link") -or $entry.ContainsKey("Hard Link")) {
            throw "压缩包包含链接文件，已停止安装：$relative"
        }
        [long]$entrySize = 0
        if ($entry.ContainsKey("Size")) {
            if (![long]::TryParse([string]$entry.Size, [ref]$entrySize) -or $entrySize -lt 0) {
                throw "压缩包包含无效文件大小：$relative"
            }
        }
        if ($entrySize -gt 20GB) { throw "压缩包单个文件超过安全上限：$relative" }
        $totalSize += $entrySize
        if ($totalSize -gt 80GB) { throw "压缩包总解压体积超过 80 GiB 安全上限。" }
    }
    return @{ Entries = $entries; ExpandedBytes = $totalSize }
}

$payload = (Resolve-Path -LiteralPath $PayloadRoot).Path
$manifestPath = Join-Path $payload "manifest.json"
$sevenZip = Join-Path $payload "7zr.exe"
if (!(Test-Path -LiteralPath $manifestPath -PathType Leaf) -or !(Test-Path -LiteralPath $sevenZip -PathType Leaf)) {
    throw "离线引擎数据包不完整，缺少 manifest.json 或 7zr.exe。请确认 Setup.exe 与 ComfyUI-Payload 文件夹在同一目录。"
}
$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($manifest.target_architecture -ne "windows-x64" -or
    $manifest.bundle_type -ne "complete-offline-comfyui-engine-internal-validation-only" -or
    $manifest.distribution_approved -ne $false -or
    $manifest.distribution_status -ne "blocked-pending-third-party-dependency-audit") {
    throw "离线引擎数据包版本、系统架构或内部验证/分发门禁状态不匹配。"
}
$imageModel = $manifest.image_generation_model
$imageModelRelative = "models/checkpoints/v1-5-pruned-emaonly-fp16.safetensors"
$imageModelPath = Join-Path $payload $imageModelRelative
$imageLicenseRelative = "THIRD_PARTY_NOTICES/Stable-Diffusion-CreativeML-OpenRAIL-M-LICENSE.txt"
$imageLicensePath = Join-Path $bundleRoot $imageLicenseRelative
if (!(Test-Path -LiteralPath $imageLicensePath -PathType Leaf)) {
    throw "离线安装套件缺少 Stable Diffusion CreativeML OpenRAIL-M 完整许可证文本。"
}
$imageLicenseText = Get-Content -LiteralPath $imageLicensePath -Raw -Encoding UTF8
if ($imageLicenseText -notmatch "CreativeML Open RAIL-M" -or $imageLicenseText -notmatch "Attachment A") {
    throw "Stable Diffusion CreativeML OpenRAIL-M 许可证文本不完整。"
}
$comfyAimdoLicensePath = Join-Path $bundleRoot ([string]$manifest.comfy_aimdo.license_file)
$frontendLicensePath = Join-Path $bundleRoot ([string]$manifest.comfyui_frontend.license_file)
$frontendNoticePath = Join-Path $bundleRoot ([string]$manifest.comfyui_frontend.third_party_notice_file)
if (!(Test-Path -LiteralPath $comfyAimdoLicensePath -PathType Leaf) -or
    (Get-Content -LiteralPath $comfyAimdoLicensePath -Raw -Encoding UTF8) -notmatch "GNU GENERAL PUBLIC LICENSE" -or
    (Get-Sha256 $comfyAimdoLicensePath) -ne ([string]$manifest.comfy_aimdo.license_sha256).ToLowerInvariant()) {
    throw "缺少 comfy-aimdo GPL-3.0 许可证文本。"
}
if (!(Test-Path -LiteralPath $frontendLicensePath -PathType Leaf) -or
    (Get-Content -LiteralPath $frontendLicensePath -Raw -Encoding UTF8) -notmatch "GNU GENERAL PUBLIC LICENSE" -or
    (Get-Sha256 $frontendLicensePath) -ne ([string]$manifest.comfyui_frontend.license_sha256).ToLowerInvariant()) {
    throw "缺少 ComfyUI Frontend GPL-3.0 许可证文本。"
}
if (!(Test-Path -LiteralPath $frontendNoticePath -PathType Leaf) -or
    (Get-Sha256 $frontendNoticePath) -ne ([string]$manifest.comfyui_frontend.third_party_notice_sha256).ToLowerInvariant()) {
    throw "缺少 ComfyUI Frontend 第三方通知文件。"
}
if (!$imageModel -or
    $imageModel.name -cne "Stable Diffusion 1.5 FP16" -or
    $imageModel.file -cne "ComfyUI-Payload/$imageModelRelative" -or
    $imageModel.license -cne "CreativeML OpenRAIL-M" -or
    $imageModel.license_review_status -cne "pending" -or
    $imageModel.distribution_approved -ne $false -or
    !(Test-Path -LiteralPath $imageModelPath -PathType Leaf)) {
    throw "离线数据包缺少已登记的 Stable Diffusion 图片模型或许可证状态不正确。"
}
$imageModelHash = (Get-FileHash -LiteralPath $imageModelPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($imageModelHash -cne ([string]$imageModel.sha256).ToLowerInvariant() -or
    (Get-Item -LiteralPath $imageModelPath).Length -ne [long]$imageModel.size_bytes) {
    throw "Stable Diffusion 图片模型的文件大小或 SHA-256 校验失败。"
}
$expectedAssetNames = @(
    "ComfyUI_windows_portable_nvidia.7z",
    "ComfyUI_windows_portable_nvidia_cu126.7z",
    "ComfyUI_windows_portable_amd.7z",
    "ComfyUI_windows_portable_intel.7z"
)
$manifestAssetItems = @($manifest.assets)
$manifestAssetNames = @($manifestAssetItems | ForEach-Object { [string]$_.name } | Sort-Object -Unique)
$assetDifferences = @(Compare-Object -ReferenceObject $expectedAssetNames -DifferenceObject $manifestAssetNames)
if ($manifestAssetItems.Count -ne 4 -or $manifestAssetNames.Count -ne 4 -or
    $assetDifferences.Count -gt 0) {
    throw "离线数据包缺少官方显卡运行包、包含重复项或包含未知文件。"
}

$bundleRoot = Split-Path -Parent $payload
$inventoryReportRelative = [string]$manifest.dependency_inventory_report
if ($inventoryReportRelative -cne "THIRD_PARTY_NOTICES/comfyui-portable-dependency-inventory.json") {
    throw "离线清单缺少四种 Portable 运行包的依赖清点报告路径。"
}
$inventoryReportPath = Join-Path $bundleRoot ($inventoryReportRelative.Replace("/", "\"))
if (!(Test-Path -LiteralPath $inventoryReportPath -PathType Leaf)) {
    throw "离线安装套件缺少四种 Portable 运行包的依赖清点报告。"
}
$inventoryReport = Get-Content -LiteralPath $inventoryReportPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($inventoryReport.audit_status -ne "pending" -or
    $inventoryReport.distribution_approved -ne $false -or
    $inventoryReport.release_tag -cne [string]$manifest.comfyui_version) {
    throw "依赖清点报告的版本或人工审核门禁状态无效。"
}
$inventoryVariants = @($inventoryReport.portable_variants)
$inventoryNames = @($inventoryVariants | ForEach-Object { [string]$_.asset } | Sort-Object -Unique)
$inventoryDifferences = @(Compare-Object -ReferenceObject $expectedAssetNames -DifferenceObject $inventoryNames)
if ($inventoryVariants.Count -ne 4 -or $inventoryNames.Count -ne 4 -or $inventoryDifferences.Count -gt 0) {
    throw "依赖清点报告必须逐一覆盖四种官方 Portable 运行包。"
}
foreach ($variant in $inventoryVariants) {
    $assetRecord = @($manifestAssetItems | Where-Object { $_.name -ceq [string]$variant.asset })
    if ($assetRecord.Count -ne 1 -or
        $variant.inventory_status -ne "inventory_generated_review_pending" -or
        $variant.release_tag -cne [string]$manifest.comfyui_version -or
        [string]$variant.sha256 -ne [string]$assetRecord[0].sha256 -or
        [long]$variant.archive_size_bytes -ne [long]$assetRecord[0].size_bytes) {
        throw "Portable 依赖清点报告与资产 SHA-256、大小或版本不匹配：$($variant.asset)"
    }
    $variantComponents = @($variant.components)
    if (@($variantComponents | Where-Object { $_.license_review_status -ne "pending" }).Count -gt 0) {
        throw "自动清点报告不得将任何组件许可证标记为已批准：$($variant.asset)"
    }
}

$auditRegisterPath = Join-Path $bundleRoot "THIRD_PARTY_NOTICES\comfyui-portable-dependency-audit.json"
if (!(Test-Path -LiteralPath $auditRegisterPath -PathType Leaf)) {
    throw "离线安装套件缺少第三方依赖审查登记表。"
}
$auditRegister = Get-Content -LiteralPath $auditRegisterPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($auditRegister.audit_status -ne "pending" -or $auditRegister.distribution_approved -ne $false) {
    throw "第三方依赖审查登记表状态无效；自动清点不能代替人工许可证审核。"
}
$registerVariants = @($auditRegister.portable_variants)
$registerNames = @($registerVariants | ForEach-Object { [string]$_.asset } | Sort-Object -Unique)
$registerDifferences = @(Compare-Object -ReferenceObject $expectedAssetNames -DifferenceObject $registerNames)
if ($registerVariants.Count -ne 4 -or $registerNames.Count -ne 4 -or $registerDifferences.Count -gt 0) {
    throw "第三方依赖审查登记表必须逐一覆盖四种官方 Portable 运行包。"
}
foreach ($variant in $inventoryVariants) {
    $registerEntry = @($registerVariants | Where-Object { $_.asset -ceq [string]$variant.asset })
    if ($registerEntry.Count -ne 1 -or
        $registerEntry[0].inventory_status -ne "inventory_generated_review_pending" -or
        [string]$registerEntry[0].sha256 -ne [string]$variant.sha256 -or
        [string]$registerEntry[0].release_tag -ne [string]$variant.release_tag) {
        throw "第三方审查登记表与依赖清点报告不匹配：$($variant.asset)"
    }
}
foreach ($manifestAsset in $manifestAssetItems) {
    $expectedUrl = "https://github.com/Comfy-Org/ComfyUI/releases/download/$($manifest.comfyui_version)/$($manifestAsset.name)"
    if ($manifestAsset.verified -ne $true -or $manifestAsset.source -ne $expectedUrl -or
        [string]$manifestAsset.sha256 -notmatch "^[0-9a-fA-F]{64}$" -or [long]$manifestAsset.size_bytes -le 0) {
        throw "离线数据包包含未经验证或来源不正确的 ComfyUI 资产。"
    }
}
$bundleRoot = Split-Path -Parent $payload
$comfyLicensePath = Join-Path $bundleRoot ([string]$manifest.license.license_file)
$sevenZipLicensePath = Join-Path $bundleRoot ([string]$manifest.seven_zip.license_file)
$sevenZipSourcePath = Join-Path $bundleRoot (([string]$manifest.seven_zip.source_archive).Replace("/", "\"))
$comfyAimdoSourceRelative = [string]$manifest.comfy_aimdo.source_archive
$frontendSourceRelative = [string]$manifest.comfyui_frontend.source_archive
if ($comfyAimdoSourceRelative -cne "THIRD_PARTY_NOTICES/source-code/comfy-aimdo-v0.6.0-68088de9.zip" -or
    $frontendSourceRelative -cne "THIRD_PARTY_NOTICES/source-code/ComfyUI_frontend-v1.59.6-c62d747f.zip") {
    throw "GPL 对应源代码包路径或版本不匹配。"
}
$comfyAimdoSourcePath = Join-Path $bundleRoot $comfyAimdoSourceRelative
$frontendSourcePath = Join-Path $bundleRoot $frontendSourceRelative
if (!(Test-Path -LiteralPath $comfyLicensePath -PathType Leaf) -or
    (Get-Content -LiteralPath $comfyLicensePath -Raw -Encoding UTF8) -notmatch "GNU GENERAL PUBLIC LICENSE") {
    throw "缺少 ComfyUI GPL-3.0 许可证文件。"
}
if (!(Test-Path -LiteralPath $sevenZipLicensePath -PathType Leaf) -or
    (Get-Content -LiteralPath $sevenZipLicensePath -Raw -Encoding UTF8) -notmatch "GNU LGPL") {
    throw "缺少 7-Zip 许可证文件。"
}
if (!(Test-Path -LiteralPath $sevenZipSourcePath -PathType Leaf) -or
    (Get-Sha256 $sevenZipSourcePath) -ne ([string]$manifest.seven_zip.source_sha256).ToLowerInvariant()) {
    throw "7-Zip 对应源代码包缺失或 SHA-256 校验失败。"
}
if (!(Test-Path -LiteralPath $comfyAimdoSourcePath -PathType Leaf) -or
    (Get-Sha256 $comfyAimdoSourcePath) -ne ([string]$manifest.comfy_aimdo.source_archive_sha256).ToLowerInvariant()) {
    throw "comfy-aimdo GPL 对应源代码包缺失或 SHA-256 校验失败。"
}
if (!(Test-Path -LiteralPath $frontendSourcePath -PathType Leaf) -or
    (Get-Sha256 $frontendSourcePath) -ne ([string]$manifest.comfyui_frontend.source_archive_sha256).ToLowerInvariant()) {
    throw "ComfyUI Frontend GPL 对应源代码包缺失或 SHA-256 校验失败。"
}
$frontendNpmAudit = $manifest.comfyui_frontend.npm_dependency_audit
if ($null -eq $frontendNpmAudit -or
    $frontendNpmAudit.status -cne "inventory_generated_manual_license_review_pending" -or
    $frontendNpmAudit.distribution_approved -ne $false -or
    [int]$frontendNpmAudit.package_count -lt 1) {
    throw "ComfyUI Frontend npm 依赖许可证清点状态或清点数量无效。"
}
$frontendNpmReportPath = Join-Path $bundleRoot ([string]$frontendNpmAudit.report_file)
$frontendNpmSummaryPath = Join-Path $bundleRoot ([string]$frontendNpmAudit.summary_file)
if (!(Test-Path -LiteralPath $frontendNpmReportPath -PathType Leaf) -or
    (Get-Sha256 $frontendNpmReportPath) -ne ([string]$frontendNpmAudit.report_sha256).ToLowerInvariant()) {
    throw "ComfyUI Frontend npm 许可证完整清点报告缺失或 SHA-256 校验失败。"
}
if (!(Test-Path -LiteralPath $frontendNpmSummaryPath -PathType Leaf) -or
    (Get-Sha256 $frontendNpmSummaryPath) -ne ([string]$frontendNpmAudit.summary_sha256).ToLowerInvariant()) {
    throw "ComfyUI Frontend npm 许可证清点摘要缺失或 SHA-256 校验失败。"
}
$frontendNpmSummary = Get-Content -LiteralPath $frontendNpmSummaryPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($frontendNpmSummary.source_commit -cne [string]$manifest.comfyui_frontend.source_commit -or
    $frontendNpmSummary.distribution_approved -ne $false -or
    [int]$frontendNpmSummary.package_count -ne [int]$frontendNpmAudit.package_count -or
    [int]$frontendNpmSummary.license_file_count -ne [int]$frontendNpmAudit.license_file_count) {
    throw "ComfyUI Frontend npm 清点摘要与安装包清单不匹配。"
}
$frontendNpmFiles = @($frontendNpmAudit.files)
if ($frontendNpmFiles.Count -lt 2) { throw "ComfyUI Frontend npm 许可证清点文件列表为空或不完整。" }
$frontendNpmExpectedReport = [string]$frontendNpmAudit.report_file
$frontendNpmExpectedSummary = [string]$frontendNpmAudit.summary_file
if (@($frontendNpmFiles | Where-Object { $_.path -ceq $frontendNpmExpectedReport -and $_.sha256 -ceq $frontendNpmAudit.report_sha256 }).Count -ne 1 -or
    @($frontendNpmFiles | Where-Object { $_.path -ceq $frontendNpmExpectedSummary -and $_.sha256 -ceq $frontendNpmAudit.summary_sha256 }).Count -ne 1) {
    throw "ComfyUI Frontend npm 清点报告未列入逐文件完整性清单。"
}
foreach ($frontendNpmFile in $frontendNpmFiles) {
    $relativePath = [string]$frontendNpmFile.path
    $installedRelativePath = [string]$frontendNpmFile.installed_path
    if (!$relativePath.StartsWith("THIRD_PARTY_NOTICES/ComfyUI-Frontend-NPM/", [System.StringComparison]::Ordinal) -or
        $relativePath -match '(^|/)\.\.(/|$)' -or
        !$installedRelativePath.StartsWith("licenses/ComfyUI-Frontend-NPM/", [System.StringComparison]::Ordinal) -or
        $installedRelativePath -match '(^|/)\.\.(/|$)') {
        throw "ComfyUI Frontend npm 许可证文件路径不安全。"
    }
    $sourceFile = Join-Path $bundleRoot $relativePath.Replace("/", "\")
    if (!(Test-Path -LiteralPath $sourceFile -PathType Leaf) -or
        (Get-Item -LiteralPath $sourceFile).Length -ne [long]$frontendNpmFile.size_bytes -or
        (Get-Sha256 $sourceFile) -ne ([string]$frontendNpmFile.sha256).ToLowerInvariant()) {
        throw "ComfyUI Frontend npm 许可证/NOTICE 文件缺失或 SHA-256 校验失败：$relativePath"
    }
}
$cpuArchitecture = @(Get-CimInstance -ClassName Win32_Processor -ErrorAction Stop | Select-Object -ExpandProperty Architecture | Select-Object -Unique)
if ([Environment]::Is64BitOperatingSystem -ne $true -or $cpuArchitecture.Count -ne 1 -or $cpuArchitecture[0] -ne 9) {
    throw "当前系统不是受支持的 Windows x64 环境。"
}

$tool = $manifest.tools | Where-Object { $_.name -eq "7zr.exe" } | Select-Object -First 1
$expectedToolUrl = "https://github.com/ip7z/7zip/releases/download/$($manifest.seven_zip.version)/7zr.exe"
if (!$tool -or $tool.source -ne $expectedToolUrl -or
    (Get-Sha256 $sevenZip) -ne ([string]$tool.sha256).ToLowerInvariant()) {
    throw "7-Zip 解压工具来源或 SHA-256 校验失败。"
}

if ($PSBoundParameters.ContainsKey("DetectedAdapters")) {
    $adapters = @($DetectedAdapters)
} else {
    $adapters = @(Get-CimInstance -ClassName Win32_VideoController -ErrorAction Stop)
}
$selection = Select-ComfyAsset $adapters
$asset = $manifest.assets | Where-Object { $_.name -eq $selection.name } | Select-Object -First 1
if (!$asset -or $asset.verified -ne $true) { throw "离线数据包缺少所需显卡运行包：$($selection.name)" }
$expectedAssetUrl = "https://github.com/Comfy-Org/ComfyUI/releases/download/$($manifest.comfyui_version)/$($selection.name)"
if ($asset.source -ne $expectedAssetUrl) { throw "ComfyUI 运行包来源与官方版本清单不匹配。" }

$archivePath = Join-Path $payload $selection.name
if (!(Test-Path -LiteralPath $archivePath -PathType Leaf)) { throw "缺少已选择的显卡运行包：$($selection.name)" }
if ((Get-Item -LiteralPath $archivePath).Length -ne [long]$asset.size_bytes) {
    throw "ComfyUI 运行包大小校验失败。"
}
if ((Get-Sha256 $archivePath) -ne ([string]$asset.sha256).ToLowerInvariant()) {
    throw "ComfyUI 运行包 SHA-256 校验失败。"
}

$archiveInfo = Get-ArchiveEntries $sevenZip $archivePath
if ($ValidateOnly) {
    $testOutput = @(& $sevenZip t -y -- $archivePath 2>&1)
    if ($LASTEXITCODE -ne 0) { throw "ComfyUI 运行包完整性测试失败，预检未通过。" }
    Write-Host "ComfyUI 离线载荷预检通过：清单、许可证门禁、SHA-256、归档路径和压缩数据均已验证。"
    return
}
$installFullPath = [System.IO.Path]::GetFullPath($InstallRoot)
if (Test-Path -LiteralPath $installFullPath) {
    $existingMain = Join-Path $installFullPath "ComfyUI\main.py"
    $existingPython = Join-Path $installFullPath "python_embeded\python.exe"
    if ((Test-Path -LiteralPath $existingMain -PathType Leaf) -and
        (Test-Path -LiteralPath $existingPython -PathType Leaf)) {
        $existingVersion = "未知版本"
        $existingRuntimeManifest = Join-Path $installFullPath "runtime-manifest.json"
        if (Test-Path -LiteralPath $existingRuntimeManifest -PathType Leaf) {
            try {
                $existingState = Get-Content -LiteralPath $existingRuntimeManifest -Raw -Encoding UTF8 | ConvertFrom-Json
                if ($existingState.comfyui_version) { $existingVersion = [string]$existingState.comfyui_version }
            } catch {}
        }
        Write-Host "检测到已有 ComfyUI $existingVersion。为保护现有模型、插件和配置，本次保留现有引擎，不覆盖或删除任何文件。"
        if ($transcriptStarted) {
            Stop-Transcript | Out-Null
            $transcriptStarted = $false
        }
        return
    }
    throw "检测到已有 ComfyUI 目录但结构不完整。为保护用户数据，安装已停止；请先备份该目录再修复。"
}
$installDrive = [System.IO.Path]::GetPathRoot($installFullPath)
$driveInfo = [System.IO.DriveInfo]::new($installDrive)
$requiredFree = [Math]::Max(8GB, [long]$archiveInfo.ExpandedBytes + [long]$imageModel.size_bytes + 1GB)
if ($driveInfo.AvailableFreeSpace -lt $requiredFree) {
    throw ("安装盘空间不足。至少需要 {0:N2} GiB，可用 {1:N2} GiB。" -f ($requiredFree / 1GB), ($driveInfo.AvailableFreeSpace / 1GB))
}

$testOutput = @(& $sevenZip t -y -- $archivePath 2>&1)
if ($LASTEXITCODE -ne 0) { throw "ComfyUI 运行包完整性测试失败，已停止安装。" }

$parent = Split-Path -Parent $installFullPath
New-Item -ItemType Directory -Path $parent -Force | Out-Null
$stage = Join-Path $parent (".ComfyUI-stage-" + [guid]::NewGuid().ToString("N"))
$backup = Join-Path $parent (".ComfyUI-backup-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $stage -Force | Out-Null
try {
    Write-Host ("已识别显卡：{0}（{1}）" -f $selection.detected_gpu, $selection.vendor)
    Write-Host ("选择运行包：{0}" -f $selection.name)
    Write-Host ("运行模式：{0}" -f $selection.runtime_mode)
    Write-Host ("适配说明：{0}" -f $selection.selection_reason)
    Write-Host ("解压约 {0:N2} GiB，请保持电脑供电并等待完成。" -f ($archiveInfo.ExpandedBytes / 1GB))

    & $sevenZip x -y ("-o" + $stage) -- $archivePath
    if ($LASTEXITCODE -ne 0) { throw "ComfyUI 运行环境解压失败。" }
    $reparsePoints = @(Get-ChildItem -LiteralPath $stage -Force -Recurse |
        Where-Object { ($_.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 })
    if ($reparsePoints.Count -gt 0) { throw "解压结果包含链接或重解析点，已停止部署。" }

    $python = Get-ChildItem -LiteralPath $stage -Filter "python.exe" -File -Recurse |
        Where-Object { $_.FullName -match "(?i)python[_-]?embed" } | Select-Object -First 1
    $main = Get-ChildItem -LiteralPath $stage -Filter "main.py" -File -Recurse |
        Where-Object { $_.Directory.Name -eq "ComfyUI" } | Select-Object -First 1
    if (!$python -or !$main) { throw "解压后的官方运行包缺少 Python 或 ComfyUI 主程序，已停止部署。" }

    $portableRoot = Split-Path -Parent (Split-Path -Parent $python.FullName)
    $comfyRoot = Split-Path -Parent $main.FullName
    if ((Split-Path -Parent $comfyRoot) -ne $portableRoot) {
        throw "ComfyUI 运行包目录结构不符合预期，已停止部署。"
    }

    # The target was checked before extraction. Never replace or delete an existing
    # ComfyUI folder because it may contain user models, custom nodes, or settings.
    # From this point the destination belongs to this installation attempt. If any
    # post-move step fails, remove only this newly-created destination so a later
    # retry is not blocked by a half-installed directory.
    $deploymentOwned = $false
    try {
        # Claim ownership only after the directory move has succeeded. If another
        # process creates the destination between the earlier existence check and
        # this move, Move-Item must fail without granting this attempt permission
        # to delete that concurrently-created directory.
        Move-Item -LiteralPath $portableRoot -Destination $installFullPath
        $deploymentOwned = $true

        # Install the pinned image checkpoint into ComfyUI's standard checkpoint folder.
        # The source is part of the extracted offline suite; no network request is made.
        $installedCheckpointDir = Join-Path $installFullPath "ComfyUI\models\checkpoints"
        New-Item -ItemType Directory -Path $installedCheckpointDir -Force | Out-Null
        $installedCheckpoint = Join-Path $installedCheckpointDir (Split-Path -Leaf $imageModelPath)
        Copy-Item -LiteralPath $imageModelPath -Destination $installedCheckpoint
        $installedLicenseDir = Join-Path $installFullPath "licenses"
        New-Item -ItemType Directory -Path $installedLicenseDir -Force | Out-Null
        $installedImageLicense = Join-Path $installedLicenseDir "Stable-Diffusion-CreativeML-OpenRAIL-M-LICENSE.txt"
        Copy-Item -LiteralPath $imageLicensePath -Destination $installedImageLicense
        Copy-Item -LiteralPath $comfyAimdoLicensePath -Destination (Join-Path $installedLicenseDir "comfy-aimdo-GPL-3.0-LICENSE.txt")
        Copy-Item -LiteralPath $frontendLicensePath -Destination (Join-Path $installedLicenseDir "ComfyUI-Frontend-GPL-3.0-LICENSE.txt")
        Copy-Item -LiteralPath $frontendNoticePath -Destination (Join-Path $installedLicenseDir "ComfyUI-Frontend-THIRD-PARTY-NOTICES.md")
        $installedSourceCodeDir = Join-Path $installedLicenseDir "source-code"
        New-Item -ItemType Directory -Path $installedSourceCodeDir -Force | Out-Null
        Copy-Item -LiteralPath $comfyAimdoSourcePath -Destination (Join-Path $installedSourceCodeDir "comfy-aimdo-v0.6.0-source.zip")
        Copy-Item -LiteralPath $frontendSourcePath -Destination (Join-Path $installedSourceCodeDir "ComfyUI-Frontend-v1.59.6-source.zip")
        foreach ($frontendNpmFile in $frontendNpmFiles) {
            $installedRelative = ([string]$frontendNpmFile.installed_path).Substring("licenses/".Length).Replace("/", "\")
            $installedTarget = Join-Path $installedLicenseDir $installedRelative
            $installedTargetDirectory = Split-Path -Parent $installedTarget
            New-Item -ItemType Directory -Path $installedTargetDirectory -Force | Out-Null
            $bundleSource = Join-Path $bundleRoot ([string]$frontendNpmFile.path).Replace("/", "\")
            Copy-Item -LiteralPath $bundleSource -Destination $installedTarget
        }
        if ((Get-Content -LiteralPath $installedImageLicense -Raw -Encoding UTF8) -notmatch "Attachment A") {
            throw "Stable Diffusion 模型许可证未能完整部署。"
        }
        if ((Get-FileHash -LiteralPath $installedCheckpoint -Algorithm SHA256).Hash.ToLowerInvariant() -cne $imageModelHash) {
            throw "Stable Diffusion 图片模型部署后的 SHA-256 校验失败。"
        }

        # Provide an explicit CPU launcher for integrated/unsupported GPU devices.
        # This follows the official ComfyUI portable CPU invocation and does not
        # install extra runtimes or download anything.
        $cpuLauncher = Join-Path $installFullPath "AI漫剧大师-CPU模式启动.bat"
        $cpuLauncherContent = @(
            "@echo off",
            "setlocal",
            'cd /d "%~dp0"',
            'if not exist "python_embeded\python.exe" (echo ComfyUI embedded Python is missing.& pause & exit /b 1)',
            'if not exist "ComfyUI\main.py" (echo ComfyUI main.py is missing.& pause & exit /b 1)',
            '"python_embeded\python.exe" -s "ComfyUI\main.py" --cpu --windows-standalone-build',
            "if errorlevel 1 (echo ComfyUI CPU mode exited with an error.& pause & exit /b 1)",
            "endlocal"
        )
        Set-Content -LiteralPath $cpuLauncher -Value $cpuLauncherContent -Encoding ASCII

        $runtimeManifest = @{
            schema_version = 1
            comfyui_version = $manifest.comfyui_version
            selected_asset = $selection.name
            archive_sha256 = ([string]$asset.sha256).ToLowerInvariant()
            gpu_vendor = $selection.vendor
            detected_gpu = $selection.detected_gpu
            runtime_mode = $selection.runtime_mode
            image_generation_model = [string]$imageModel.name
            image_generation_model_sha256 = $imageModelHash
            image_generation_model_license = [string]$imageModel.license
            image_generation_model_license_file = "licenses/Stable-Diffusion-CreativeML-OpenRAIL-M-LICENSE.txt"
            comfy_aimdo_license = "GPL-3.0"
            comfy_aimdo_license_file = "licenses/comfy-aimdo-GPL-3.0-LICENSE.txt"
            comfy_aimdo_source_commit = [string]$manifest.comfy_aimdo.source_commit
            comfy_aimdo_source_code_status = [string]$manifest.comfy_aimdo.source_code_status
            comfy_aimdo_source_archive = "licenses/source-code/comfy-aimdo-v0.6.0-source.zip"
            comfy_aimdo_source_archive_sha256 = [string]$manifest.comfy_aimdo.source_archive_sha256
            comfyui_frontend_license = "GPL-3.0-only"
            comfyui_frontend_license_file = "licenses/ComfyUI-Frontend-GPL-3.0-LICENSE.txt"
            comfyui_frontend_third_party_notice_file = "licenses/ComfyUI-Frontend-THIRD-PARTY-NOTICES.md"
            comfyui_frontend_source_commit = [string]$manifest.comfyui_frontend.source_commit
            comfyui_frontend_source_code_status = [string]$manifest.comfyui_frontend.source_code_status
            comfyui_frontend_source_archive = "licenses/source-code/ComfyUI-Frontend-v1.59.6-source.zip"
            comfyui_frontend_source_archive_sha256 = [string]$manifest.comfyui_frontend.source_archive_sha256
            comfyui_frontend_npm_license_audit_status = [string]$frontendNpmAudit.status
            comfyui_frontend_npm_package_count = [int]$frontendNpmAudit.package_count
            comfyui_frontend_npm_license_file_count = [int]$frontendNpmAudit.license_file_count
            comfyui_frontend_npm_license_report = "licenses/ComfyUI-Frontend-NPM/frontend-npm-dependency-licenses.json"
            comfyui_frontend_npm_license_summary = "licenses/ComfyUI-Frontend-NPM/frontend-npm-dependency-summary.json"
            selection_reason = $selection.selection_reason
            installed_utc = [DateTime]::UtcNow.ToString("o")
            distribution_approved = $false
        }
        $runtimeManifest | ConvertTo-Json -Depth 5 |
            Set-Content -LiteralPath (Join-Path $installFullPath "runtime-manifest.json") -Encoding UTF8

        Write-Host "ComfyUI 引擎已部署到：$installFullPath"
        if ($selection.runtime_mode -eq "cpu") {
            Write-Host "当前设备将使用 CPU 模式；首次图片生成可能较慢。"
        }
        Write-Host ("CPU 模式启动器：{0}" -f $cpuLauncher)
        Write-Host "已从完整离线安装套件部署 Stable Diffusion 1.5 FP16 图片模型；未进行联网下载。"
    } catch {
        if ($deploymentOwned -and (Test-Path -LiteralPath $installFullPath)) {
            Remove-Item -LiteralPath $installFullPath -Recurse -Force -ErrorAction SilentlyContinue
        }
        throw
    }
} finally {
    if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue }
    if ($transcriptStarted) {
        try { Stop-Transcript | Out-Null } catch {}
        $transcriptStarted = $false
    }
}
