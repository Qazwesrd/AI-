[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$InstallerPath,

    [switch]$VerifyOnly
)

$ErrorActionPreference = "Stop"

# Desktop-only update contract:
# - Verify and launch the signed-off desktop installer only.
# - Never inspect, extract, move, replace, or delete AI engines, models, or project data.
# - electron-builder installs application files under the selected program directory;
#   Electron userData and the separately configured AI data directory remain untouched.

$installer = (Resolve-Path -LiteralPath $InstallerPath -ErrorAction Stop).Path
if ([IO.Path]::GetExtension($installer) -ne ".exe") {
    throw "InstallerPath must point to a Windows Setup .exe."
}

$checksumPath = "$installer.sha256"
if (!(Test-Path -LiteralPath $checksumPath -PathType Leaf)) {
    throw "Installer SHA-256 file is missing: $checksumPath"
}

$expected = ((Get-Content -LiteralPath $checksumPath -Raw).Trim() -split '\\s+')[0].ToLowerInvariant()
if ($expected -notmatch '^[a-f0-9]{64}$') {
    throw "Installer SHA-256 file has an invalid format."
}

$actual = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actual -ne $expected) {
    throw "Installer SHA-256 verification failed. No installation was started."
}

Write-Host "Desktop installer SHA-256 verified: $actual"
Write-Host "Existing Ollama, ComfyUI, FFmpeg, StableDiffusionCpp, VACE, model files, and project data are not accessed by this updater."

if ($VerifyOnly) {
    Write-Host "VerifyOnly requested; installer was not started."
    exit 0
}

$process = Start-Process -FilePath $installer -Wait -PassThru
if ($process.ExitCode -notin @(0, 3010)) {
    throw "Desktop installer failed with exit code $($process.ExitCode)."
}

Write-Host "Desktop application update completed. Existing AI engines and model data were not modified by this script."
