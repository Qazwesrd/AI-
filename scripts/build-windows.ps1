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

if (!(Test-Path $Python)) {
    throw "Python virtual environment not found."
}

if (!(Test-Path "$Desktop\node_modules\electron-builder")) {
    throw "electron-builder dependency not found."
}

if (!(Test-Path "$Web\node_modules")) {
    throw "Web node_modules not found."
}

# Validate source code before producing an installer.
Write-Host "[0/4] Running backend regression tests and source validation..."
$TestDataRoot = Join-Path $env:TEMP ("ai-comic-build-test-" + [guid]::NewGuid().ToString("N"))
$env:APP_DATA_DIR = $TestDataRoot
$env:PYTHONIOENCODING = "utf-8"
Push-Location $Api
try {
    & $Python -m unittest discover -s tests -v
    Check-ExitCode "Backend regression tests failed"
    & $Python -m compileall -q "$Api\app"
    Check-ExitCode "Python source compilation failed"
} finally {
    Pop-Location
    Remove-Item Env:APP_DATA_DIR -ErrorAction SilentlyContinue
    Remove-Item Env:PYTHONIOENCODING -ErrorAction SilentlyContinue
}

$FfmpegSource = Join-Path $Desktop "build-resources\ffmpeg\ffmpeg.exe"
$FfprobeSource = Join-Path $Desktop "build-resources\ffmpeg\ffprobe.exe"
$SystemFfmpeg = (Get-Command ffmpeg.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1).Source
$SystemFfprobe = (Get-Command ffprobe.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1).Source

if (!(Test-Path $FfmpegSource) -or (Get-Item $FfmpegSource).Length -lt 1MB) {
    if ($SystemFfmpeg) {
        New-Item -ItemType Directory -Path (Split-Path $FfmpegSource) -Force | Out-Null
        Copy-Item $SystemFfmpeg $FfmpegSource -Force
    } else {
        throw "FFmpeg executable is missing or is a Git LFS pointer. Install FFmpeg and add it to PATH."
    }
}
if (!(Test-Path $FfprobeSource) -or (Get-Item $FfprobeSource).Length -lt 1MB) {
    if ($SystemFfprobe) {
        New-Item -ItemType Directory -Path (Split-Path $FfprobeSource) -Force | Out-Null
        Copy-Item $SystemFfprobe $FfprobeSource -Force
    } else {
        throw "FFmpeg executable is missing or is a Git LFS pointer. Install FFmpeg and add it to PATH."
    }
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

if (!(Test-Path "$Web\node_modules\.bin\tsc.cmd")) {
    Write-Host "Local TypeScript compiler is missing; restoring frontend development dependencies..."
    npm ci --prefix $Web --include=dev
    Check-ExitCode "Frontend dependency installation failed"
}

& "$Web\node_modules\.bin\tsc.cmd" --noEmit
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

# Generate a branded futuristic application icon before packaging.
Write-Host "[3.5/4] Creating application icon..."
Add-Type -AssemblyName System.Drawing
$IconDirectory = Join-Path $Desktop "build-resources"
New-Item -ItemType Directory -Path $IconDirectory -Force | Out-Null
$IconPath = Join-Path $IconDirectory "icon.ico"
$PngPath = Join-Path $env:TEMP ("ai-comic-icon-" + [guid]::NewGuid().ToString("N") + ".png")
$Bitmap = New-Object System.Drawing.Bitmap(256, 256)
$Graphics = [System.Drawing.Graphics]::FromImage($Bitmap)
$Graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
$Graphics.Clear([System.Drawing.Color]::FromArgb(8, 8, 24))
$Bounds = New-Object System.Drawing.Rectangle(0, 0, 256, 256)
$Gradient = New-Object System.Drawing.Drawing2D.LinearGradientBrush(
    $Bounds,
    [System.Drawing.Color]::FromArgb(22, 18, 58),
    [System.Drawing.Color]::FromArgb(8, 8, 24),
    [System.Drawing.Drawing2D.LinearGradientMode]::ForwardDiagonal
)
$Graphics.FillRectangle($Gradient, $Bounds)
$OuterPen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(150, 95, 255), 5)
$InnerPen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(65, 220, 255), 3)
$Graphics.DrawEllipse($OuterPen, 25, 25, 206, 206)
$Graphics.DrawEllipse($InnerPen, 48, 48, 160, 160)
$Graphics.DrawArc($InnerPen, 12, 75, 232, 110, 205, 130)
$Glow = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(45, 70, 180, 255))
$Graphics.FillEllipse($Glow, 72, 72, 112, 112)
$TextBrush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(245, 245, 255))
$Font = New-Object System.Drawing.Font("Arial", 76, [System.Drawing.FontStyle]::Bold, [System.Drawing.GraphicsUnit]::Pixel)
$Format = New-Object System.Drawing.StringFormat
$Format.Alignment = [System.Drawing.StringAlignment]::Center
$Format.LineAlignment = [System.Drawing.StringAlignment]::Center
$Graphics.DrawString("AI", $Font, $TextBrush, (New-Object System.Drawing.RectangleF(24, 55, 208, 145)), $Format)
$Bitmap.Save($PngPath, [System.Drawing.Imaging.ImageFormat]::Png)
$Graphics.Dispose(); $Bitmap.Dispose(); $Gradient.Dispose(); $OuterPen.Dispose(); $InnerPen.Dispose(); $Glow.Dispose(); $TextBrush.Dispose(); $Font.Dispose(); $Format.Dispose()
$PngBytes = [System.IO.File]::ReadAllBytes($PngPath)
$Memory = New-Object System.IO.MemoryStream
$Writer = New-Object System.IO.BinaryWriter($Memory)
$Writer.Write([UInt16]0); $Writer.Write([UInt16]1); $Writer.Write([UInt16]1)
$Writer.Write([Byte]0); $Writer.Write([Byte]0); $Writer.Write([Byte]0); $Writer.Write([Byte]0)
$Writer.Write([UInt16]1); $Writer.Write([UInt16]32)
$Writer.Write([UInt32]$PngBytes.Length); $Writer.Write([UInt32]22); $Writer.Write($PngBytes)
[System.IO.File]::WriteAllBytes($IconPath, $Memory.ToArray())
$Writer.Dispose(); $Memory.Dispose(); Remove-Item $PngPath -Force
if (!(Test-Path $IconPath) -or (Get-Item $IconPath).Length -lt 100) { throw "Application icon generation failed." }
Write-Host "Application icon created: $IconPath"

Write-Host "[4/4] Building Electron installer..."

Set-Location $Desktop

$Release = Join-Path $Desktop "release"
New-Item -ItemType Directory -Path $Release -Force | Out-Null
Get-ChildItem $Release -File -Filter "*Setup*.exe" -ErrorAction SilentlyContinue | Remove-Item -Force

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