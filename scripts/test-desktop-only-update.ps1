$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$updater = Join-Path $PSScriptRoot "update-desktop-only.ps1"
$source = Get-Content -LiteralPath $updater -Raw

# Guard the updater contract against accidental resource deployment logic.
$forbidden = @(
    'Expand-Archive',
    'Move-Item',
    'Copy-Item',
    'Remove-Item',
    'DataDirectory',
    'AICOMICS_DATA_DIR',
    'LOCALAPPDATA.*engines',
    'Ollama.*\\models'
)
foreach ($pattern in $forbidden) {
    if ($source -match $pattern) {
        throw "Desktop-only updater contains forbidden resource/data operation: $pattern"
    }
}

# The full offline provisioner may add missing engine folders, but must not
# move or replace an already present engine/model directory.
$offlineInstaller = Get-Content -LiteralPath (Join-Path $PSScriptRoot "install-offline-bundle.ps1") -Raw
if ($offlineInstaller -notmatch 'if \(Test-Path -LiteralPath \$destination -PathType Container\)\s*\{[\s\S]*?continue') {
    throw "Full offline installer does not skip existing engine directories."
}
if ($offlineInstaller -match 'Move-Item -LiteralPath \$destination -Destination \$backupPath') {
    throw "Full offline installer still moves an existing engine directory into backup."
}
if ($offlineInstaller -notmatch 'if \(!\$hadManifest\)') {
    throw "Full offline installer does not preserve an existing resource manifest."
}

$temp = Join-Path ([IO.Path]::GetTempPath()) ("ai-comic-update-test-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $temp -Force | Out-Null
try {
    $stub = Join-Path $temp "AI漫剧制作大师-Setup.exe"
    [IO.File]::WriteAllBytes($stub, [byte[]](1,2,3,4,5,6,7,8))
    $hash = (Get-FileHash -LiteralPath $stub -Algorithm SHA256).Hash
    "$hash  $(Split-Path -Leaf $stub)" | Set-Content -LiteralPath "$stub.sha256" -Encoding ascii

    & $updater -InstallerPath $stub -VerifyOnly

    [IO.File]::AppendAllText($stub, "tampered")
    $failedAsExpected = $false
    try {
        & $updater -InstallerPath $stub -VerifyOnly
    } catch {
        $failedAsExpected = $_.Exception.Message -match "SHA-256 verification failed"
    }
    if (!$failedAsExpected) {
        throw "Updater did not reject a modified installer."
    }

    Write-Host "Desktop-only updater tests passed: valid checksum accepted; tampered installer rejected; no AI resource operations present."
} finally {
    Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue
}
