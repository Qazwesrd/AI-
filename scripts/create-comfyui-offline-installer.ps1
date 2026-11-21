[CmdletBinding()]
param(
    [string]$SetupExe,
    [string]$OutputZip,
    [switch]$SkipBuild,
    [switch]$Force
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
$releaseDir = Join-Path $root "apps\desktop\release"

if ([string]::IsNullOrWhiteSpace($OutputZip)) {
    $OutputZip = Join-Path $releaseDir "AI漫剧制作大师-完整离线安装套件.zip"
}
$OutputZip = [System.IO.Path]::GetFullPath($OutputZip)
$outputDirectory = Split-Path -Parent $OutputZip
New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null

if (Test-Path -LiteralPath $OutputZip) {
    if (-not $Force) {
        throw "输出文件已存在：$OutputZip。若确认覆盖，请添加 -Force 参数。"
    }
    Remove-Item -LiteralPath $OutputZip -Force
}
if (Test-Path -LiteralPath "$OutputZip.sha256") {
    Remove-Item -LiteralPath "$OutputZip.sha256" -Force
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
    if ($LASTEXITCODE -ne 0) {
        throw "Windows 安装程序构建失败，已停止离线套件制作。"
    }

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

Write-Host "第 2/2 步：从 ComfyUI 和 7-Zip 官方发布源下载并校验全部离线引擎资源。"
Write-Host "预计下载约 7.3 GiB；请使用稳定网络，并确保输出盘有至少 10 GiB 可用空间。"
Write-Host "下载内容只用于制作离线 ZIP，不会安装到当前电脑。"

& $bundleScript -SetupExe $SetupExe -OutputZip $OutputZip
if ($LASTEXITCODE -ne 0) {
    throw "离线 ZIP 构建失败。"
}

if (!(Test-Path -LiteralPath $OutputZip -PathType Leaf)) {
    throw "离线 ZIP 没有生成。"
}
$zip = Get-Item -LiteralPath $OutputZip
if ($zip.Length -lt 6GB) {
    throw "生成的 ZIP 小于预期（$([math]::Round($zip.Length / 1GB, 2)) GiB），拒绝交付。"
}
$checksumPath = "$OutputZip.sha256"
if (!(Test-Path -LiteralPath $checksumPath -PathType Leaf)) {
    throw "ZIP SHA-256 校验文件缺失。"
}

Write-Host ""
Write-Host "完整离线安装套件制作完成："
Write-Host $OutputZip
Write-Host ("文件大小：{0:N2} GiB" -f ($zip.Length / 1GB))
Write-Host "SHA-256："
Get-Content -LiteralPath $checksumPath
Write-Host "下一步：将 ZIP 完整解压，再双击其中的 Setup.exe。不要单独移动 Setup.exe。"
