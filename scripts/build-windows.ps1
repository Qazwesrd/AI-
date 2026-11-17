$ErrorActionPreference = "Stop"

# Automatically locate the project root.
$Root = Split-Path -Parent $PSScriptRoot
$Api = Join-Path $Root "apps\api"
$Web = Join-Path $Root "apps\web"
$Desktop = Join-Path $Root "apps\desktop"
$Python = Join-Path $Api ".venv\Scripts\python.exe"

function Check-ExitCode {
    param([string]$Message)

    if ($LASTEXITCODE -ne 0) {
        throw "$Message. Exit code: $LASTEXITCODE"
    }
}

Write-Host "========== WINDOWS BUILD START =========="
Write-Host "Project root: $Root"

# Validate source code before producing an installer.
Write-Host "[0/4] Running source validation..."
& $Python -m compileall -q "$Api\app"
Check-ExitCode "Python source compilation failed"

$FfmpegSource = Join-Path $Desktop "build-resources\ffmpeg\ffmpeg.exe"
$FfprobeSource = Join-Path $Desktop "build-resources\ffmpeg\ffprobe.exe"
$SystemFfmpeg = (Get-Command ffmpeg.exe -ErrorAction SilentlyContinue | Select-Object -First 1).Source
$SystemFfprobe = (Get-Command ffprobe.exe -ErrorAction SilentlyContinue | Select-Object -First 1).Source

if (!(Test-Path $FfmpegSource) -or (Get-Item $FfmpegSource).Length -lt 1MB) {
    if ($SystemFfmpeg) {
        New-Item -ItemType Directory -Path (Split-Path $FfmpegSource) -Force | Out-Null
        Copy-Item $SystemFfmpeg $FfmpegSource -Force
    } else {
        throw "FFmpeg 文件缺失或只是 Git LFS 指针。请先安装 FFmpeg 并加入 PATH。"
    }
}
if (!(Test-Path $FfprobeSource) -or (Get-Item $FfprobeSource).Length -lt 1MB) {
    if ($SystemFfprobe) {
        New-Item -ItemType Directory -Path (Split-Path $FfprobeSource) -Force | Out-Null
        Copy-Item $SystemFfprobe $FfprobeSource -Force
    } else {
        throw "FFprobe 文件缺失或只是 Git LFS 指针。请先安装 FFmpeg 并加入 PATH。"
    }
}

if (!(Test-Path $Python)) {
    throw "Python virtual environment not found."
}

if (!(Test-Path "$Desktop\node_modules\electron-builder")) {
    throw "electron-builder dependency not found."
}

if (!(Test-Path "$Web\node_modules")) {
    throw "Web node_modules not found."
}

Write-Host "[1/4] Checking PyInstaller..."

& $Python -m pip show pyinstaller *> $null

if ($LASTEXITCODE -ne 0) {
    & $Python -m pip install pyinstaller
    Check-ExitCode "PyInstaller installation failed"
}

Write-Host "[2/4] Building API executable..."

Set-Location $Api

New-Item -ItemType Directory -Path "$Api\dist" -Force | Out-Null
New-Item -ItemType Directory -Path "$Api\build\pyinstaller" -Force | Out-Null

$PyInstallerArgs = @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--onefile",
    "--name", "ai-comic-api",
    "--distpath", "$Api\dist",
    "--workpath", "$Api\build\pyinstaller",
    "--specpath", "$Api\build",
    "--paths", $Api,
    "--collect-submodules", "app",
    "--collect-all", "uvicorn",
    "--collect-all", "fastapi",
    "--collect-all", "starlette",
    "--collect-all", "sqlalchemy",
    "--collect-all", "pydantic",
    "--collect-all", "cryptography",
    "--collect-all", "multipart",
    "$Api\build_entry.py"
)

& $Python @PyInstallerArgs
Check-ExitCode "API packaging failed"

$ApiExe = "$Api\dist\ai-comic-api.exe"

if (!(Test-Path $ApiExe)) {
    throw "API executable was not generated."
}

Write-Host "API executable created."

Write-Host "[3/4] Building Next.js frontend..."

Set-Location $Web

npx tsc --noEmit
Check-ExitCode "TypeScript validation failed"

npm run build
Check-ExitCode "Next.js build failed"

if (!(Test-Path "$Web\.next\standalone\server.js")) {
    throw "Next.js standalone server.js not found."
}

if (!(Test-Path "$Web\.next\static")) {
    throw "Next.js static directory not found."
}

Write-Host "Frontend standalone build created."

Write-Host "[4/4] Building Electron installer..."

Set-Location $Desktop

npm run build:desktop
Check-ExitCode "Electron installer build failed"

$Release = Join-Path $Desktop "release"

if (!(Test-Path $Release)) {
    throw "Release directory not found."
}

$Installers = @(Get-ChildItem $Release -File -Filter "*.exe")

if ($Installers.Count -eq 0) {
    throw "No installer EXE was generated."
}

Write-Host "========== BUILD SUCCESS =========="

foreach ($Installer in $Installers) {
    Write-Host $Installer.FullName
    Write-Host ("Size: {0} MB" -f [math]::Round($Installer.Length / 1MB, 2))
}

Set-Location $Root