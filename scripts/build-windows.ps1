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

# Require FFmpeg and FFprobe to be present and valid before packaging.
$FfmpegSource = Join-Path $Desktop "build-resources\ffmpeg\ffmpeg.exe"
$FfprobeSource = Join-Path $Desktop "build-resources\ffmpeg\ffprobe.exe"
foreach ($tool in @($FfmpegSource, $FfprobeSource)) {
    if (!(Test-Path $tool) -or (Get-Item $tool).Length -lt 1MB) {
        throw "Bundled media runtime is missing or invalid: $tool"
    }
}
Write-Host "Bundled FFmpeg: $FfmpegSource"
Write-Host "Bundled FFprobe: $FfprobeSource"


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

# Start the freshly built local services and capture real UI screenshots for the Chinese manual.
Write-Host "[3.4/4] Capturing application screenshots and generating the PDF manual..."
$ManualDir = Join-Path $Root "docs\manual"
$ManualImageDir = Join-Path $ManualDir "images"
$ManualPdf = Join-Path $ManualDir "AI漫剧制作大师-使用说明书.pdf"
$ManualDataDir = Join-Path $env:TEMP ("ai-comic-manual-data-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $ManualImageDir -Force | Out-Null
New-Item -ItemType Directory -Path $ManualDataDir -Force | Out-Null
$ApiLog = Join-Path $ManualDataDir "api.log"
$ApiErrorLog = Join-Path $ManualDataDir "api-error.log"
$WebLog = Join-Path $ManualDataDir "web.log"
$WebErrorLog = Join-Path $ManualDataDir "web-error.log"
$ApiProcess = $null
$WebProcess = $null
$ManualCaptureSuccess = $false
$OldAppDataDir = $env:APP_DATA_DIR
$OldFfmpegPath = $env:FFMPEG_PATH
$OldFfprobePath = $env:FFPROBE_PATH
$OldPort = $env:PORT
$OldHostname = $env:HOSTNAME
$OldApiBaseUrl = $env:NEXT_PUBLIC_API_BASE_URL
$OldPythonIoEncoding = $env:PYTHONIOENCODING
$OldPythonUtf8 = $env:PYTHONUTF8
try {
    $env:APP_DATA_DIR = $ManualDataDir
    $env:PYTHONIOENCODING = "utf-8"
    $env:PYTHONUTF8 = "1"
    $env:FFMPEG_PATH = $FfmpegSource
    $env:FFPROBE_PATH = $FfprobeSource
    $ApiProcess = Start-Process -FilePath $ApiExe -WorkingDirectory $Api -PassThru -WindowStyle Hidden -RedirectStandardOutput $ApiLog -RedirectStandardError $ApiErrorLog

    $NodeCommand = Get-Command node.exe -CommandType Application -ErrorAction Stop | Select-Object -First 1
    $StandaloneRoot = Join-Path $Web ".next\standalone"
    $StandaloneStatic = Join-Path $StandaloneRoot ".next\static"
    New-Item -ItemType Directory -Path $StandaloneStatic -Force | Out-Null
    Copy-Item (Join-Path $Web ".next\static\*") $StandaloneStatic -Recurse -Force
    if (Test-Path (Join-Path $Web "public")) {
        $StandalonePublic = Join-Path $StandaloneRoot "public"
        New-Item -ItemType Directory -Path $StandalonePublic -Force | Out-Null
        Copy-Item (Join-Path $Web "public\*") $StandalonePublic -Recurse -Force
    }
    $WebServer = Join-Path $StandaloneRoot "server.js"
    $env:PORT = "3000"
    $env:HOSTNAME = "127.0.0.1"
    $env:NEXT_PUBLIC_API_BASE_URL = "http://127.0.0.1:8000"
    $WebProcess = Start-Process -FilePath $NodeCommand.Source -ArgumentList @("server.js") -WorkingDirectory (Split-Path $WebServer) -PassThru -WindowStyle Hidden -RedirectStandardOutput $WebLog -RedirectStandardError $WebErrorLog

    $Ready = $false
    for ($attempt = 1; $attempt -le 120; $attempt++) {
        if ($ApiProcess.HasExited) {
            $ApiDetails = (Get-Content $ApiErrorLog -Raw -ErrorAction SilentlyContinue) + (Get-Content $ApiLog -Raw -ErrorAction SilentlyContinue)
            throw "Local API exited before manual screenshots could be captured. API diagnostics: $ApiDetails"
        }
        if ($WebProcess.HasExited) {
            $WebDetails = (Get-Content $WebErrorLog -Raw -ErrorAction SilentlyContinue) + (Get-Content $WebLog -Raw -ErrorAction SilentlyContinue)
            throw "Web server exited before manual screenshots could be captured. Web diagnostics: $WebDetails"
        }
        try {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" -TimeoutSec 2
            $webResponse = Invoke-WebRequest -Uri "http://127.0.0.1:3000" -TimeoutSec 2
            if ($health.status -eq "ok" -and $webResponse.StatusCode -eq 200) { $Ready = $true; break }
        } catch { Start-Sleep -Seconds 1 }
    }
    if (!$Ready) { throw "Local API and web frontend did not become ready for manual screenshots." }

    $ElectronCommand = Join-Path $Desktop "node_modules\.bin\electron.cmd"
    $CaptureScript = Join-Path $Desktop "capture-manual-screenshots.cjs"
    if (!(Test-Path $ElectronCommand) -or !(Test-Path $CaptureScript)) { throw "Electron screenshot capture tool is missing." }
    & $ElectronCommand $CaptureScript $ManualDir
    Check-ExitCode "Capturing screenshots and rendering the PDF manual failed"

    $ExpectedScreenshots = 17
    $CapturedScreenshots = @(Get-ChildItem $ManualImageDir -File -Filter "*.png")
    if ($CapturedScreenshots.Count -ne $ExpectedScreenshots) {
        throw "Expected $ExpectedScreenshots real application screenshots; captured $($CapturedScreenshots.Count)."
    }
    if (!(Test-Path $ManualPdf) -or (Get-Item $ManualPdf).Length -lt 100KB) {
        throw "The illustrated PDF user manual was not generated correctly."
    }
    Write-Host "Captured $($CapturedScreenshots.Count) application screenshots and generated $ManualPdf"
    $ManualCaptureSuccess = $true
} finally {
    if ($WebProcess -and !$WebProcess.HasExited) { Stop-Process -Id $WebProcess.Id -Force -ErrorAction SilentlyContinue }
    if ($ApiProcess -and !$ApiProcess.HasExited) { Stop-Process -Id $ApiProcess.Id -Force -ErrorAction SilentlyContinue }
    if ($null -eq $OldAppDataDir) { Remove-Item Env:APP_DATA_DIR -ErrorAction SilentlyContinue } else { $env:APP_DATA_DIR = $OldAppDataDir }
    if ($null -eq $OldFfmpegPath) { Remove-Item Env:FFMPEG_PATH -ErrorAction SilentlyContinue } else { $env:FFMPEG_PATH = $OldFfmpegPath }
    if ($null -eq $OldFfprobePath) { Remove-Item Env:FFPROBE_PATH -ErrorAction SilentlyContinue } else { $env:FFPROBE_PATH = $OldFfprobePath }
    if ($null -eq $OldPort) { Remove-Item Env:PORT -ErrorAction SilentlyContinue } else { $env:PORT = $OldPort }
    if ($null -eq $OldHostname) { Remove-Item Env:HOSTNAME -ErrorAction SilentlyContinue } else { $env:HOSTNAME = $OldHostname }
    if ($null -eq $OldApiBaseUrl) { Remove-Item Env:NEXT_PUBLIC_API_BASE_URL -ErrorAction SilentlyContinue } else { $env:NEXT_PUBLIC_API_BASE_URL = $OldApiBaseUrl }
    if ($null -eq $OldPythonIoEncoding) { Remove-Item Env:PYTHONIOENCODING -ErrorAction SilentlyContinue } else { $env:PYTHONIOENCODING = $OldPythonIoEncoding }
    if ($null -eq $OldPythonUtf8) { Remove-Item Env:PYTHONUTF8 -ErrorAction SilentlyContinue } else { $env:PYTHONUTF8 = $OldPythonUtf8 }
    if ($ManualCaptureSuccess) {
        Remove-Item $ManualDataDir -Recurse -Force -ErrorAction SilentlyContinue
    } else {
        Write-Warning "Manual capture failed; diagnostic logs were preserved at $ManualDataDir"
        if (Test-Path $ApiErrorLog) { Get-Content $ApiErrorLog | Write-Host }
        if (Test-Path $WebErrorLog) { Get-Content $WebErrorLog | Write-Host }
    }
}

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

# Final desktop delivery is the signed-off Setup installer plus SHA-256.
# Do not create or publish a separate local-testing ZIP for the desktop project.
$FinalInstaller = Get-ChildItem $Release -File -Filter "*Setup*.exe" | Select-Object -First 1
if (!$FinalInstaller) { throw "The final Windows Setup installer is missing." }
$FinalHash = (Get-FileHash -LiteralPath $FinalInstaller.FullName -Algorithm SHA256).Hash
"$FinalHash  $($FinalInstaller.Name)" | Set-Content -LiteralPath ($FinalInstaller.FullName + ".sha256") -Encoding ascii
Write-Host "Final desktop installer: $($FinalInstaller.FullName)"
Write-Host "Installer SHA256: $FinalHash"

Write-Host "========== BUILD SUCCESS =========="

foreach ($Installer in $Installers) {
    Write-Host $Installer.FullName
    Write-Host ("Size: {0} MB" -f [math]::Round($Installer.Length / 1MB, 2))
}

Set-Location $Root