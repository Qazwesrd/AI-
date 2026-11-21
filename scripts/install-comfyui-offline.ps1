param(
    [Parameter(Mandatory = $true)]
    [string]$PayloadRoot,
    [Parameter(Mandatory = $true)]
    [string]$InstallRoot
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

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
                selection_reason = "GTX 10 系列及更旧型号，使用官方 cu126 运行包"
            }
        }
        return @{
            name = "ComfyUI_windows_portable_nvidia.7z"
            detected_gpu = $model
            vendor = "NVIDIA"
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
                selection_reason = "检测到官方 Windows AMD 运行包支持的 GPU 系列"
            }
        }
        throw "检测到 AMD 显卡，但型号不在已核实的 Windows 运行包支持范围内。安装已停止，不会猜测或联网回退。"
    }

    $intel = @($recognized | Where-Object Vendor -eq "Intel")
    if ($intel.Count -gt 0) {
        $model = $intel[0].Name
        if ($model -match "(?i)\bArc\b") {
            return @{
                name = "ComfyUI_windows_portable_intel.7z"
                detected_gpu = $model
                vendor = "Intel"
                selection_reason = "检测到 Intel Arc GPU，使用官方 Intel 运行包"
            }
        }
        throw "检测到 Intel 显卡，但当前官方 Windows XPU 运行包面向 Intel Arc；未识别到 Arc 型号，安装已停止。"
    }

    throw "没有识别到受支持的 NVIDIA、AMD 或 Intel GPU。安装已停止，不会猜测运行包，也不会联网下载。"
}

function Get-ArchiveEntries([string]$SevenZip, [string]$ArchivePath) {
    $listing = @(& $SevenZip l -slt -- $ArchivePath 2>&1)
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
    if ($entries.Count -lt 1 -or $entries.Count -gt 100000) {
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
if ($manifest.target_architecture -ne "windows-x64" -or $manifest.bundle_type -ne "complete-offline-comfyui-engine") {
    throw "离线引擎数据包版本或系统架构不匹配。"
}
$expectedAssetNames = @(
    "ComfyUI_windows_portable_nvidia.7z",
    "ComfyUI_windows_portable_nvidia_cu126.7z",
    "ComfyUI_windows_portable_amd.7z",
    "ComfyUI_windows_portable_intel.7z"
)
$manifestAssetItems = @($manifest.assets)
$manifestAssetNames = @($manifestAssetItems | ForEach-Object { [string]$_.name } | Sort-Object -Unique)
if ($manifestAssetItems.Count -ne 4 -or $manifestAssetNames.Count -ne 4 -or
    (Compare-Object $expectedAssetNames $manifestAssetNames).Count -gt 0) {
    throw "离线数据包缺少官方显卡运行包、包含重复项或包含未知文件。"
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

$adapters = @(Get-CimInstance -ClassName Win32_VideoController -ErrorAction Stop)
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
$installFullPath = [System.IO.Path]::GetFullPath($InstallRoot)
if (Test-Path -LiteralPath $installFullPath) {
    throw "检测到已有 ComfyUI 目录。为防止覆盖你现有的模型、插件或配置，安装已停止；请先完整备份并将旧目录移到其他位置后再运行安装程序。"
}
$installDrive = [System.IO.Path]::GetPathRoot($installFullPath)
$driveInfo = [System.IO.DriveInfo]::new($installDrive)
$requiredFree = [Math]::Max(8GB, [long]$archiveInfo.ExpandedBytes + 1GB)
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
    Move-Item -LiteralPath $portableRoot -Destination $installFullPath
    Write-Host "ComfyUI 引擎已部署到：$installFullPath"
    Write-Host "图片生成模型未包含在安装包内，也没有下载任何模型。"
} finally {
    if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue }
}
