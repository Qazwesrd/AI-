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
    # A malformed manifest must be rejected before any output ZIP is created.
    $badManifest = Join-Path $temp "bad-order.parts.json"
    $badData = Get-Content -LiteralPath $manifest -Raw -Encoding UTF8 | ConvertFrom-Json
    $badData.parts[1].name = $badData.parts[0].name
    $badData | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $badManifest -Encoding UTF8
    $badManifestOutput = Join-Path $temp "bad-order.zip"
    $badManifestFailed = $false
    try { & $copyJoiner -ManifestPath $badManifest -OutputPath $badManifestOutput } catch { $badManifestFailed = $true }
    if (!$badManifestFailed -or (Test-Path $badManifestOutput)) { throw "重复/错序分卷清单未在创建输出文件前被拒绝。" }

    # Existing output files are never overwritten.
    $existingOutput = Join-Path $temp "existing.zip"
    [IO.File]::WriteAllText($existingOutput, "keep")
    $existingFailed = $false
    try { & $copyJoiner -ManifestPath $manifest -OutputPath $existingOutput } catch { $existingFailed = $true }
    if (!$existingFailed -or [IO.File]::ReadAllText($existingOutput) -cne "keep") { throw "合并工具覆盖了已有目标文件。" }

    # A collision on a later volume must not leave earlier volumes published.
    $collisionSource = Join-Path $temp "collision.zip"
    [IO.File]::WriteAllBytes($collisionSource, [byte[]]::new(5MB + 17))
    [IO.File]::WriteAllText((Join-Path $temp "collision.zip.part002"), "keep-existing")
    $collisionFailed = $false
    try { & $split -InputZip $collisionSource -PartSizeBytes 2MB } catch { $collisionFailed = $true }
    if (!$collisionFailed) { throw "遇到后续分卷文件冲突时，分卷脚本未失败。" }
    if (Test-Path (Join-Path $temp "collision.zip.part001")) { throw "分卷失败后留下了已发布的前序分卷。" }
    if ([IO.File]::ReadAllText((Join-Path $temp "collision.zip.part002")) -cne "keep-existing") { throw "分卷脚本覆盖了原有分卷文件。" }
    if (Test-Path (Join-Path $temp "collision.zip.parts.json")) { throw "分卷失败后仍发布了不完整清单。" }
    if (@(Get-ChildItem -LiteralPath $temp -Directory -Filter ".offline-parts-*").Count -ne 0) { throw "分卷失败后遗留暂存目录。" }

    Write-Host "离线安装套件分卷与合并测试通过。"
} finally {
    Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue
}
