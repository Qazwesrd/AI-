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
$OldAppDataDir = $env:APP_DATA_DIR
$OldFfmpegPath = $env:FFMPEG_PATH
$OldFfprobePath = $env:FFPROBE_PATH
$OldPort = $env:PORT
$OldHostname = $env:HOSTNAME
$OldApiBaseUrl = $env:NEXT_PUBLIC_API_BASE_URL
try {
    $env:APP_DATA_DIR = $ManualDataDir
    $env:FFMPEG_PATH = $FfmpegSource
    $env:FFPROBE_PATH = $FfprobeSource
    $ApiProcess = Start-Process -FilePath $ApiExe -WorkingDirectory $Api -PassThru -WindowStyle Hidden -RedirectStandardOutput $ApiLog -RedirectStandardError $ApiErrorLog

    $NodeCommand = Get-Command node.exe -CommandType Application -ErrorAction Stop | Select-Object -First 1
    $WebServer = Join-Path $Web ".next\standalone\server.js"
    $env:PORT = "3000"
    $env:HOSTNAME = "127.0.0.1"
    $env:NEXT_PUBLIC_API_BASE_URL = "http://127.0.0.1:8000"
    $WebProcess = Start-Process -FilePath $NodeCommand.Source -ArgumentList @("server.js") -WorkingDirectory (Split-Path $WebServer) -PassThru -WindowStyle Hidden -RedirectStandardOutput $WebLog -RedirectStandardError $WebErrorLog

    $Ready = $false
    for ($attempt = 1; $attempt -le 120; $attempt++) {
        if ($ApiProcess.HasExited) { throw "Local API exited before manual screenshots could be captured. See $ApiErrorLog" }
        if ($WebProcess.HasExited) { throw "Web server exited before manual screenshots could be captured. See $WebErrorLog" }
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
} finally {
    if ($WebProcess -and !$WebProcess.HasExited) { Stop-Process -Id $WebProcess.Id -Force -ErrorAction SilentlyContinue }
    if ($ApiProcess -and !$ApiProcess.HasExited) { Stop-Process -Id $ApiProcess.Id -Force -ErrorAction SilentlyContinue }
    if ($null -eq $OldAppDataDir) { Remove-Item Env:APP_DATA_DIR -ErrorAction SilentlyContinue } else { $env:APP_DATA_DIR = $OldAppDataDir }
    if ($null -eq $OldFfmpegPath) { Remove-Item Env:FFMPEG_PATH -ErrorAction SilentlyContinue } else { $env:FFMPEG_PATH = $OldFfmpegPath }
    if ($null -eq $OldFfprobePath) { Remove-Item Env:FFPROBE_PATH -ErrorAction SilentlyContinue } else { $env:FFPROBE_PATH = $OldFfprobePath }
    if ($null -eq $OldPort) { Remove-Item Env:PORT -ErrorAction SilentlyContinue } else { $env:PORT = $OldPort }
    if ($null -eq $OldHostname) { Remove-Item Env:HOSTNAME -ErrorAction SilentlyContinue } else { $env:HOSTNAME = $OldHostname }
    if ($null -eq $OldApiBaseUrl) { Remove-Item Env:NEXT_PUBLIC_API_BASE_URL -ErrorAction SilentlyContinue } else { $env:NEXT_PUBLIC_API_BASE_URL = $OldApiBaseUrl }
    Remove-Item $ManualDataDir -Recurse -Force -ErrorAction SilentlyContinue
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

# Assemble the single-click local testing ZIP with installer, checksums and illustrated manual.
$DesktopPackage = Get-Content (Join-Path $Desktop "package.json") -Raw | ConvertFrom-Json
$BundleName = "AI漫剧制作大师-本地测试包-$($DesktopPackage.version)"
$BundleZip = Join-Path $Release "$BundleName.zip"
$BundleStage = Join-Path $Release ("bundle-stage-" + [guid]::NewGuid().ToString("N"))
$InstallFolder = Join-Path $BundleStage "安装程序"
$GuideFolder = Join-Path $BundleStage "使用说明书"
New-Item -ItemType Directory -Path $InstallFolder -Force | Out-Null
New-Item -ItemType Directory -Path $GuideFolder -Force | Out-Null
$InstallerForBundle = $Installers | Where-Object { $_.Name -match "Setup" } | Select-Object -First 1
if (!$InstallerForBundle) { throw "The Windows Setup installer is missing from the release folder." }
Copy-Item $InstallerForBundle.FullName $InstallFolder -Force
$InstallerHashFile = Join-Path $Release ($InstallerForBundle.Name + ".sha256")
$InstallerHash = (Get-FileHash -LiteralPath $InstallerForBundle.FullName -Algorithm SHA256).Hash
"$InstallerHash  $($InstallerForBundle.Name)" | Set-Content -LiteralPath $InstallerHashFile -Encoding ascii
Copy-Item $InstallerHashFile $InstallFolder -Force
Copy-Item $ManualPdf $GuideFolder -Force
Copy-Item (Join-Path $ManualDir "manual.html") $GuideFolder -Force
Copy-Item $ManualImageDir (Join-Path $GuideFolder "images") -Recurse -Force
$BundleReadme = @"
AI漫剧制作大师 Windows 本地测试包 $($DesktopPackage.version)

1. 先打开“使用说明书\AI漫剧制作大师-使用说明书.pdf”阅读安装与首次启动说明。
2. 运行“安装程序\$($InstallerForBundle.Name)”并按向导安装。
3. 安装完成后从桌面快捷方式打开软件。
4. 首次使用请配置 AI 服务商；第三方 AI 费用、账号权限和额度由用户自行确认。
5. 建议先用少量测试小说和素材验证流程，再导入重要项目。
6. “使用说明书\images”包含本次构建时从实际软件页面采集的界面截图。
7. 安装程序 SHA-256：$InstallerHash

重要提醒：
- 此 ZIP 是 Windows 安装程序和使用说明书的组合包，不是源码包。
- 正式使用前请在“项目数据备份与恢复中心”创建完整工作空间备份。
- 网页版第三方 AI 需要用户手动提交任务并导回结果；不会自动控制第三方网页。
- 若付费任务状态不确定，先在服务商后台核实，避免重复扣费。
"@
Set-Content -LiteralPath (Join-Path $BundleStage "测试前必读.txt") -Value $BundleReadme -Encoding utf8
if (Test-Path $BundleZip) { Remove-Item $BundleZip -Force }
Compress-Archive -Path (Join-Path $BundleStage "*") -DestinationPath $BundleZip -CompressionLevel Optimal -Force
Remove-Item $BundleStage -Recurse -Force
if (!(Test-Path $BundleZip) -or (Get-Item $BundleZip).Length -lt 1MB) { throw "The combined installer and manual ZIP was not created correctly." }
$BundleHash = (Get-FileHash -LiteralPath $BundleZip -Algorithm SHA256).Hash
"$BundleHash  $([System.IO.Path]::GetFileName($BundleZip))" | Set-Content -LiteralPath "$BundleZip.sha256" -Encoding ascii
Write-Host "Testing bundle: $BundleZip"
Write-Host ("Bundle size: {0} MB" -f [math]::Round((Get-Item $BundleZip).Length / 1MB, 2))
Write-Host "Bundle SHA256: $BundleHash"

Write-Host "========== BUILD SUCCESS =========="

foreach ($Installer in $Installers) {
    Write-Host $Installer.FullName
    Write-Host ("Size: {0} MB" -f [math]::Round($Installer.Length / 1MB, 2))
}

Set-Location $Root