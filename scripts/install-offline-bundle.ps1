param(
    [string]$InstallerPath = (Join-Path $PSScriptRoot "AI漫剧制作大师-Setup.exe"),
    [string]$ResourceBundlePath = (Join-Path $PSScriptRoot "AI漫剧大师-AI离线资源包-2026.11.zip"),
    [string]$InstallDirectory = "",
    [string]$DataDirectory = ""
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.IO.Compression.FileSystem

function Read-Choice([string]$Prompt, [string]$Default) {
    $value = Read-Host "$Prompt（直接回车使用：$Default）"
    if ([string]::IsNullOrWhiteSpace($value)) { return $Default }
    return [System.IO.Path]::GetFullPath($value)
}

if (!(Test-Path -LiteralPath $InstallerPath -PathType Leaf)) { throw "找不到桌面安装程序：$InstallerPath" }
if (!(Test-Path -LiteralPath $ResourceBundlePath -PathType Leaf)) { throw "找不到完整 AI 离线资源包：$ResourceBundlePath" }

$hashFile = "$ResourceBundlePath.sha256"
if (!(Test-Path -LiteralPath $hashFile -PathType Leaf)) { throw "缺少资源包 SHA-256 校验文件：$hashFile" }
$expectedHash = ((Get-Content -LiteralPath $hashFile -Raw).Trim() -split '\s+')[0].ToLowerInvariant()
if ($expectedHash -notmatch '^[a-f0-9]{64}$') { throw "资源包 SHA-256 文件格式无效。" }
$actualHash = (Get-FileHash -LiteralPath $ResourceBundlePath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualHash -ne $expectedHash) { throw "资源包 SHA-256 校验失败，已停止安装。" }

$defaultInstall = Join-Path $env:ProgramFiles "AI漫剧制作大师"
$defaultData = Join-Path $env:LOCALAPPDATA "AI漫剧制作大师-Data"
if (!$InstallDirectory) { $InstallDirectory = Read-Choice "程序安装目录" $defaultInstall }
if (!$DataDirectory) { $DataDirectory = Read-Choice "AI 模型和项目数据目录（建议选择空间充足的磁盘）" $defaultData }
$InstallDirectory = [System.IO.Path]::GetFullPath($InstallDirectory)
$DataDirectory = [System.IO.Path]::GetFullPath($DataDirectory)

if ($InstallDirectory.StartsWith($DataDirectory, [System.StringComparison]::OrdinalIgnoreCase) -or
    $DataDirectory.StartsWith($InstallDirectory, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "程序安装目录与 AI 数据目录不能互相包含，请选择两个独立目录。"
}

$archiveSize = (Get-Item -LiteralPath $ResourceBundlePath).Length
$driveRoot = [System.IO.Path]::GetPathRoot($DataDirectory)
$drive = [System.IO.DriveInfo]::new($driveRoot)
$requiredFree = $archiveSize + 2GB
if ($drive.AvailableFreeSpace -lt $requiredFree) {
    throw ("AI 数据盘空间不足。解压前至少需要 {0:N2} GiB 可用空间；当前 {1:N2} GiB。" -f ($requiredFree / 1GB), ($drive.AvailableFreeSpace / 1GB))
}

Write-Host "[1/5] 启动桌面程序安装器。请在安装界面确认程序安装目录：$InstallDirectory"
$installer = Start-Process -FilePath $InstallerPath -ArgumentList @("/D=$InstallDirectory") -Wait -PassThru
if ($installer.ExitCode -ne 0) { throw "桌面程序安装失败，退出代码：$($installer.ExitCode)" }

Write-Host "[2/5] 将离线数据目录设置为：$DataDirectory"
New-Item -ItemType Directory -Path $DataDirectory -Force | Out-Null
[Environment]::SetEnvironmentVariable("AICOMICS_DATA_DIR", $DataDirectory, "User")
$env:AICOMICS_DATA_DIR = $DataDirectory

$staging = Join-Path $DataDirectory (".ai-resource-staging-" + [guid]::NewGuid().ToString("N"))
$engines = Join-Path $DataDirectory "engines"
New-Item -ItemType Directory -Path $staging -Force | Out-Null
try {
    Write-Host "[3/5] 解压 AI 离线资源包（约 15 GiB），请勿关闭窗口。"
    [System.IO.Compression.ZipFile]::ExtractToDirectory($ResourceBundlePath, $staging)

    $manifestPath = Join-Path $staging "resource-manifest.json"
    $inventoryPath = Join-Path $staging "file-inventory.json"
    foreach ($required in @($manifestPath, $inventoryPath, (Join-Path $staging "Ollama\runtime\ollama.exe"),
        (Join-Path $staging "Ollama\models\blobs"), (Join-Path $staging "ComfyUI\python_embeded\python.exe"),
        (Join-Path $staging "ComfyUI\ComfyUI\main.py"), (Join-Path $staging "FFmpeg\ffmpeg.exe"),
        (Join-Path $staging "FFmpeg\ffprobe.exe"), (Join-Path $staging "StableDiffusionCpp"))) {
        if (!(Test-Path -LiteralPath $required)) { throw "资源包解压后缺少必要文件或目录：$required" }
    }

    Write-Host "[4/5] 校验资源清单中的全部文件 SHA-256。"
    $inventory = @(Get-Content -LiteralPath $inventoryPath -Raw | ConvertFrom-Json)
    if ($inventory.Count -eq 0) { throw "资源文件清单为空。" }
    $seenInventoryPaths = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($entry in $inventory) {
        $relative = [string]$entry.path
        if (!$seenInventoryPaths.Add($relative.Replace('/', '\'))) { throw "资源清单存在重复路径：$relative" }
        if ([System.IO.Path]::IsPathRooted($relative) -or $relative -match '(^|[\\/])\.\.([\\/]|$)') {
            throw "资源清单包含不安全路径：$relative"
        }
        $file = Join-Path $staging $relative
        if (!(Test-Path -LiteralPath $file -PathType Leaf)) { throw "资源文件缺失：$relative" }
        $actual = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash
        if ($actual -ne [string]$entry.sha256) { throw "资源文件 SHA-256 不匹配：$relative" }
    }

    Write-Host "[5/5] 导入引擎目录；已有同名文件不会被覆盖。"
    New-Item -ItemType Directory -Path $engines -Force | Out-Null
    foreach ($name in @("Ollama", "ComfyUI", "FFmpeg", "StableDiffusionCpp", "VACE")) {
        $source = Join-Path $staging $name
        if (!(Test-Path -LiteralPath $source -PathType Container)) { throw "资源包缺少引擎目录：$name" }
        $destination = Join-Path $engines $name
        New-Item -ItemType Directory -Path $destination -Force | Out-Null
        & robocopy $source $destination /E /XC /XN /XO /R:1 /W:1 /NFL /NDL /NJH /NJS
        if ($LASTEXITCODE -ge 8) { throw "导入 $name 失败，robocopy 退出代码：$LASTEXITCODE" }
    }
    Copy-Item -LiteralPath $manifestPath -Destination (Join-Path $DataDirectory "resource-manifest.json") -Force

    $ollama = Join-Path $engines "Ollama\runtime\ollama.exe"
    $comfyPython = Join-Path $engines "ComfyUI\python_embeded\python.exe"
    $comfyMain = Join-Path $engines "ComfyUI\ComfyUI\main.py"
    $ffmpeg = Join-Path $engines "FFmpeg\ffmpeg.exe"
    foreach ($file in @($ollama, $comfyPython, $comfyMain, $ffmpeg)) {
        if (!(Test-Path -LiteralPath $file -PathType Leaf)) { throw "安装后引擎检查失败：$file" }
    }
} finally {
    if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
}

Write-Host ""
Write-Host "离线安装与资源导入完成。"
Write-Host "程序目录：$InstallDirectory"
Write-Host "AI 数据目录：$DataDirectory"
Write-Host "请重新登录 Windows 或重新启动桌面程序，使数据目录环境变量生效。"
Write-Host "本脚本不会自动联网下载模型，也不会覆盖已有同名模型文件。"
