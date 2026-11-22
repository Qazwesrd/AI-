[CmdletBinding()]
param(
    [string]$SetupExe,
    [string]$OutputZip,
    [switch]$SkipBuild,
    [switch]$Force,
    [long]$PartSizeBytes = 1800000000
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ($env:OS -ne "Windows_NT") {
    throw "此脚本只能在 Windows 10/11 x64 上运行。"
}
if (-not [Environment]::Is64BitOperatingSystem) {
    throw "此脚本需要 Windows x64。"
}

$root = Split-Path -Parent $PSScriptRoot
$buildScript = Join-Path $PSScriptRoot "build-windows.ps1"
$bundleScript = Join-Path $PSScriptRoot "prepare-comfyui-offline-bundle.ps1"
$localAIBuilder = Join-Path $PSScriptRoot "prepare-local-ai-offline-payload.ps1"
$releaseDir = Join-Path $root "apps\desktop\release"

if ([string]::IsNullOrWhiteSpace($OutputZip)) {
    $OutputZip = Join-Path $releaseDir "AI漫剧制作大师-完整离线安装套件.zip"
}
$OutputZip = [System.IO.Path]::GetFullPath($OutputZip)
$outputDirectory = Split-Path -Parent $OutputZip
New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null

$archiveBase = [IO.Path]::GetFileName($OutputZip)
$partsManifestPath = "$OutputZip.parts.json"
$joinerTool = Join-Path $outputDirectory "AI漫剧制作大师-离线套件合并工具.ps1"
$existingPartFiles = @(Get-ChildItem -LiteralPath $outputDirectory -File -Filter "$archiveBase.part*" -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match ('^' + [regex]::Escape($archiveBase) + '\.part\d{3}$') })
$existingArtifacts = @(
    $OutputZip,
    "$OutputZip.sha256",
    $partsManifestPath,
    $joinerTool
) | Where-Object { Test-Path -LiteralPath $_ }
$existingArtifacts = @($existingArtifacts) + @($existingPartFiles | ForEach-Object { $_.FullName })
if ($existingArtifacts.Count -gt 0) {
    if (-not $Force) {
        throw "检测到已有完整 ZIP、分卷、清单或合并工具。为保护已有离线套件，不会覆盖；若确认重新制作，请添加 -Force 参数。"
    }
    Remove-Item -LiteralPath $existingArtifacts -Force -ErrorAction SilentlyContinue
}

if ([string]::IsNullOrWhiteSpace($SetupExe)) {
    if ($SkipBuild) {
        throw "使用 -SkipBuild 时必须同时通过 -SetupExe 指定已经构建好的 Setup.exe。"
    }
    if (!(Test-Path -LiteralPath $buildScript -PathType Leaf)) {
        throw "缺少 Windows 桌面构建脚本：$buildScript"
    }

    Write-Host "第 1/2 步：构建当前源码对应的 Windows 正式安装程序。"
    Write-Host "这一步需要已安装 Node.js、Python 环境、项目依赖以及 FFmpeg。"
    $buildStarted = Get-Date
    & $buildScript

    $candidates = @(Get-ChildItem -LiteralPath $releaseDir -File -Filter "*Setup*.exe" |
        Where-Object { $_.LastWriteTime -ge $buildStarted.AddMinutes(-2) } |
        Sort-Object LastWriteTime -Descending)
    if ($candidates.Count -lt 1) {
        throw "构建结束，但没有找到本次生成的 Setup.exe。"
    }
    $SetupExe = $candidates[0].FullName
} else {
    $SetupExe = (Resolve-Path -LiteralPath $SetupExe -ErrorAction Stop).Path
}

if (!(Test-Path -LiteralPath $SetupExe -PathType Leaf)) {
    throw "找不到 Setup.exe：$SetupExe"
}
if (!(Test-Path -LiteralPath $bundleScript -PathType Leaf)) {
    throw "缺少离线套件构建脚本：$bundleScript"
}
if (!(Test-Path -LiteralPath $localAIBuilder -PathType Leaf)) {
    throw "缺少本地 AI 离线载荷构建脚本：$localAIBuilder"
}

$localAIPayloadRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("aicomics-local-ai-payload-" + [Guid]::NewGuid().ToString("N"))
try {
    Write-Host "第 2/3 步：下载并验证 Ollama 官方运行环境，准备 Qwen3 4B 本地模型离线载荷。"
    Write-Host "此步骤会准备约 4 GiB 本地 AI 资源，并额外下载约 2.13 GB 的 Stable Diffusion 1.5 图片模型。若临时盘与输出盘是同一磁盘，建议开始前至少预留 24 GiB 可用空间；不会安装到当前电脑。"
    & $localAIBuilder -OutputDirectory $localAIPayloadRoot
    if (!(Test-Path -LiteralPath (Join-Path $localAIPayloadRoot "manifest.json") -PathType Leaf)) {
        throw "本地 AI 离线载荷构建未生成有效 manifest。"
    }

    Write-Host "第 3/3 步：下载并校验 ComfyUI 四种官方运行包、7-Zip 及完整离线安装 ZIP。"
    Write-Host "完整套件预计约 13 GiB（随 ComfyUI 版本和驱动变动）；输出盘还需预留打包和安装临时空间。"
    Write-Host "本步骤仅制作 ZIP，不会在当前电脑安装 ComfyUI 或 Ollama。"
    & $bundleScript -SetupExe $SetupExe -OutputZip $OutputZip -LocalAIPayloadRoot $localAIPayloadRoot
} finally {
    if (Test-Path -LiteralPath $localAIPayloadRoot) {
        Remove-Item -LiteralPath $localAIPayloadRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}

if (!(Test-Path -LiteralPath $OutputZip -PathType Leaf)) {
    throw "离线 ZIP 没有生成。"
}
$zip = Get-Item -LiteralPath $OutputZip
if ($zip.Length -lt 9GB) {
    throw "生成的 ZIP 小于预期（$([math]::Round($zip.Length / 1GB, 2)) GiB），拒绝交付。"
}
$checksumPath = "$OutputZip.sha256"
if (!(Test-Path -LiteralPath $checksumPath -PathType Leaf)) {
    throw "ZIP SHA-256 校验文件缺失。"
}

$splitScript = Join-Path $PSScriptRoot "split-offline-bundle.ps1"
if (!(Test-Path -LiteralPath $splitScript -PathType Leaf)) {
    throw "缺少离线套件分卷脚本：$splitScript"
}
Write-Host "正在把完整离线 ZIP 拆分为低于 2 GB 的下载分卷，并生成逐卷 SHA-256 清单。"
& $splitScript -InputZip $OutputZip -PartSizeBytes $PartSizeBytes
$partsManifest = "$OutputZip.parts.json"
if (!(Test-Path -LiteralPath $partsManifest -PathType Leaf) -or !(Test-Path -LiteralPath $joinerTool -PathType Leaf)) {
    throw "分卷清单或自动合并工具缺失，拒绝报告套件准备完成。"
}

# Validate the exact manifest-to-file mapping before the workflow uploads any
# artifact group. This catches missing, extra, reordered, oversized or damaged
# volumes while the original ZIP is still available for recovery.
$manifest = Get-Content -LiteralPath $partsManifest -Raw -Encoding UTF8 | ConvertFrom-Json
if ($manifest.schema_version -ne 1 -or $manifest.archive_name -cne [IO.Path]::GetFileName($OutputZip)) {
    throw "分卷清单版本或原始 ZIP 文件名不匹配。"
}
if ([long]$manifest.archive_size_bytes -ne [long]$zip.Length) {
    throw "分卷清单声明的 ZIP 大小与实际文件不一致。"
}
$archiveHash = (Get-FileHash -LiteralPath $OutputZip -Algorithm SHA256).Hash.ToLowerInvariant()
if ($manifest.archive_sha256 -cne $archiveHash) {
    throw "分卷清单中的原始 ZIP SHA-256 与实际文件不一致。"
}
$checksumLine = (Get-Content -LiteralPath $checksumPath -Raw -Encoding ASCII).Trim()
$checksumMatch = [regex]::Match($checksumLine, '^([0-9a-fA-F]{64})\s+(.+?)\s*
$manifestParts = @($manifest.parts)
if ($manifestParts.Count -lt 5 -or $manifestParts.Count -gt 12) {
    throw "完整离线套件分卷数量必须为 5 至 12 卷，实际为 $($manifestParts.Count) 卷；拒绝生成不完整的上传分组。"
}
$seenNames = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
[long]$partTotal = 0
$partFiles = @()
for ($i = 0; $i -lt $manifestParts.Count; $i++) {
    $entry = $manifestParts[$i]
    $expectedName = "$([IO.Path]::GetFileName($OutputZip)).part$('{0:D3}' -f ($i + 1))"
    if ($entry.name -cne $expectedName -or !$seenNames.Add([string]$entry.name)) {
        throw "分卷清单中的文件名重复、缺失或顺序异常：$($entry.name)"
    }
    $partPath = Join-Path $outputDirectory $entry.name
    if (!(Test-Path -LiteralPath $partPath -PathType Leaf)) {
        throw "缺少清单声明的分卷：$partPath"
    }
    $part = Get-Item -LiteralPath $partPath
    if ([long]$part.Length -ne [long]$entry.size_bytes -or $part.Length -gt 1900000000) {
        throw "分卷大小与清单不符或超过上传限制：$($entry.name)"
    }
    $partHash = (Get-FileHash -LiteralPath $partPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($partHash -cne ([string]$entry.sha256).ToLowerInvariant()) {
        throw "分卷 SHA-256 校验失败：$($entry.name)"
    }
    $partTotal += [long]$part.Length
    $partFiles += $part
}
if ($partTotal -ne [long]$zip.Length) {
    throw "分卷总大小与原始 ZIP 不一致。"
}
$actualParts = @(Get-ChildItem -LiteralPath $outputDirectory -File -Filter "$([IO.Path]::GetFileName($OutputZip)).part*" | Where-Object { $_.Name -match ('^' + [regex]::Escape([IO.Path]::GetFileName($OutputZip)) + '\.part\d{3}$') })
if ($actualParts.Count -ne $manifestParts.Count) {
    throw "输出目录存在清单之外的额外分卷或缺失分卷。"
}

Write-Host ""
Write-Host "完整离线安装套件制作完成并通过分卷清单、逐卷大小和 SHA-256 校验："
Write-Host $OutputZip
Write-Host ("文件大小：{0:N2} GiB" -f ($zip.Length / 1GB))
Write-Host "SHA-256："
Get-Content -LiteralPath $checksumPath
Write-Host ("分卷数量：{0}" -f $partFiles.Count)
Write-Host ("分卷清单：{0}" -f $partsManifest)
Write-Host ("合并工具：{0}" -f $joinerTool)
Write-Host "分发时上传全部 .part 分卷、.parts.json 清单和合并工具；完整 ZIP 本身不要作为单个 Release 附件上传。"
Write-Host "用户下载全部文件后，运行合并工具并选择 .parts.json 清单；校验通过后再解压 ZIP 并运行 Setup.exe。"
)
$expectedArchiveName = [IO.Path]::GetFileName($OutputZip)
if (!$checksumMatch.Success -or
    ![StringComparer]::OrdinalIgnoreCase.Equals($checksumMatch.Groups[1].Value, $archiveHash) -or
    ![StringComparer]::Ordinal.Equals($checksumMatch.Groups[2].Value, $expectedArchiveName)) {
    $recordedHash = if ($checksumMatch.Success) { $checksumMatch.Groups[1].Value } else { '<unparseable>' }
    $recordedName = if ($checksumMatch.Success) { $checksumMatch.Groups[2].Value } else { '<unparseable>' }
    throw "原始 ZIP SHA-256 校验文件不匹配。实际哈希：$archiveHash；记录哈希：$recordedHash；预期文件名：$expectedArchiveName；记录文件名：$recordedName。"
}
$manifestParts = @($manifest.parts)
if ($manifestParts.Count -lt 5 -or $manifestParts.Count -gt 12) {
    throw "完整离线套件分卷数量必须为 5 至 12 卷，实际为 $($manifestParts.Count) 卷；拒绝生成不完整的上传分组。"
}
$seenNames = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
[long]$partTotal = 0
$partFiles = @()
for ($i = 0; $i -lt $manifestParts.Count; $i++) {
    $entry = $manifestParts[$i]
    $expectedName = "$([IO.Path]::GetFileName($OutputZip)).part$('{0:D3}' -f ($i + 1))"
    if ($entry.name -cne $expectedName -or !$seenNames.Add([string]$entry.name)) {
        throw "分卷清单中的文件名重复、缺失或顺序异常：$($entry.name)"
    }
    $partPath = Join-Path $outputDirectory $entry.name
    if (!(Test-Path -LiteralPath $partPath -PathType Leaf)) {
        throw "缺少清单声明的分卷：$partPath"
    }
    $part = Get-Item -LiteralPath $partPath
    if ([long]$part.Length -ne [long]$entry.size_bytes -or $part.Length -gt 1900000000) {
        throw "分卷大小与清单不符或超过上传限制：$($entry.name)"
    }
    $partHash = (Get-FileHash -LiteralPath $partPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($partHash -cne ([string]$entry.sha256).ToLowerInvariant()) {
        throw "分卷 SHA-256 校验失败：$($entry.name)"
    }
    $partTotal += [long]$part.Length
    $partFiles += $part
}
if ($partTotal -ne [long]$zip.Length) {
    throw "分卷总大小与原始 ZIP 不一致。"
}
$actualParts = @(Get-ChildItem -LiteralPath $outputDirectory -File -Filter "$([IO.Path]::GetFileName($OutputZip)).part*" | Where-Object { $_.Name -match ('^' + [regex]::Escape([IO.Path]::GetFileName($OutputZip)) + '\.part\d{3}$') })
if ($actualParts.Count -ne $manifestParts.Count) {
    throw "输出目录存在清单之外的额外分卷或缺失分卷。"
}

Write-Host ""
Write-Host "完整离线安装套件制作完成并通过分卷清单、逐卷大小和 SHA-256 校验："
Write-Host $OutputZip
Write-Host ("文件大小：{0:N2} GiB" -f ($zip.Length / 1GB))
Write-Host "SHA-256："
Get-Content -LiteralPath $checksumPath
Write-Host ("分卷数量：{0}" -f $partFiles.Count)
Write-Host ("分卷清单：{0}" -f $partsManifest)
Write-Host ("合并工具：{0}" -f $joinerTool)
Write-Host "分发时上传全部 .part 分卷、.parts.json 清单和合并工具；完整 ZIP 本身不要作为单个 Release 附件上传。"
Write-Host "用户下载全部文件后，运行合并工具并选择 .parts.json 清单；校验通过后再解压 ZIP 并运行 Setup.exe。"
