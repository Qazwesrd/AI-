param()
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$split = Join-Path $root "scripts/split-offline-bundle.ps1"
$join = Join-Path $root "scripts/join-offline-bundle.ps1"
$temp = Join-Path ([IO.Path]::GetTempPath()) ("aicomics-volume-test-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $temp -Force | Out-Null
try {
    $source = Join-Path $temp "sample.zip"
    $bytes = [byte[]]::new(5MB + 123)
    [Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    [IO.File]::WriteAllBytes($source, $bytes)
    & $split -InputZip $source -PartSizeBytes 2MB
    $manifest = Join-Path $temp "sample.zip.parts.json"
    $copyJoiner = Join-Path $temp "join-offline-bundle.ps1"
    Copy-Item $join $copyJoiner
    $result = Join-Path $temp "reassembled.zip"
    & $copyJoiner -ManifestPath $manifest -OutputPath $result
    if (!(Test-Path $result) -or (Get-FileHash $source -Algorithm SHA256).Hash -ne (Get-FileHash $result -Algorithm SHA256).Hash) { throw "分卷合并后的文件与源文件不一致。" }

    $part = Join-Path $temp "sample.zip.part002"
    $stream = [IO.File]::Open($part, [IO.FileMode]::Open, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try { $stream.WriteByte(0) } finally { $stream.Dispose() }
    $badOutput = Join-Path $temp "must-not-exist.zip"
    $failed = $false
    try { & $copyJoiner -ManifestPath $manifest -OutputPath $badOutput } catch { $failed = $true }
    if (!$failed -or (Test-Path $badOutput)) { throw "损坏分卷未被正确拒绝。" }
    Write-Host "离线安装套件分卷与合并测试通过。"
} finally {
    Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue
}
