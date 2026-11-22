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
    $copyJoiner = Join-Path $temp "join-offline-bundle.ps1"
    & $split -InputZip $source -PartSizeBytes 2MB -JoinerPath $copyJoiner
    $manifest = Join-Path $temp "sample.zip.parts.json"
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
    $collisionManifest = Join-Path $temp "collision.zip.parts.json"
    $collisionJoiner = Join-Path $temp "collision-joiner.ps1"
    try { & $split -InputZip $collisionSource -PartSizeBytes 2MB -ManifestPath $collisionManifest -JoinerPath $collisionJoiner } catch { $collisionFailed = $true }
    if (!$collisionFailed) { throw "遇到后续分卷文件冲突时，分卷脚本未失败。" }
    if (Test-Path (Join-Path $temp "collision.zip.part001")) { throw "分卷失败后留下了已发布的前序分卷。" }
    if ([IO.File]::ReadAllText((Join-Path $temp "collision.zip.part002")) -cne "keep-existing") { throw "分卷脚本覆盖了原有分卷文件。" }
    if (Test-Path (Join-Path $temp "collision.zip.parts.json")) { throw "分卷失败后仍发布了不完整清单。" }
    if (@(Get-ChildItem -LiteralPath $temp -Directory -Filter ".offline-parts-*").Count -ne 0) { throw "分卷失败后遗留暂存目录。" }

    # Reproduce the three GitHub artifact downloads: volumes 1-4, 5-8,
    # then remaining volumes plus the manifest, joiner and archive checksum.
    $artifactSource = Join-Path $temp "artifact-layout.zip"
    $artifactBytes = [byte[]]::new(9MB + 321)
    [Security.Cryptography.RandomNumberGenerator]::Fill($artifactBytes)
    [IO.File]::WriteAllBytes($artifactSource, $artifactBytes)
    $artifactManifest = Join-Path $temp "artifact-layout.zip.parts.json"
    $artifactJoiner = Join-Path $temp "artifact-joiner.ps1"
    & $split -InputZip $artifactSource -PartSizeBytes 1MB -ManifestPath $artifactManifest -JoinerPath $artifactJoiner
    $artifactParts = @(Get-ChildItem -LiteralPath $temp -File -Filter "artifact-layout.zip.part*" | Where-Object { $_.Name -match '^artifact-layout\.zip\.part\d{3}$' } | Sort-Object Name)
    if ($artifactParts.Count -ne 10) { throw "三组下载布局测试未生成预期的 10 个分卷。" }

    $artifactStage = Join-Path $temp "artifact-stage"
    $downloaded = Join-Path $temp "simulated-downloads"
    foreach ($group in 1..3) {
        $groupPath = Join-Path $artifactStage "group$group"
        New-Item -ItemType Directory -Path $groupPath -Force | Out-Null
    }
    for ($i = 0; $i -lt $artifactParts.Count; $i++) {
        $group = [math]::Floor($i / 4) + 1
        Copy-Item -LiteralPath $artifactParts[$i].FullName -Destination (Join-Path $artifactStage "group$group")
    }
    Copy-Item -LiteralPath $artifactManifest -Destination (Join-Path $artifactStage "group3")
    Copy-Item -LiteralPath $artifactJoiner -Destination (Join-Path $artifactStage "group3/AI漫剧制作大师-离线套件合并工具.ps1")
    $artifactHash = (Get-FileHash -LiteralPath $artifactSource -Algorithm SHA256).Hash.ToLowerInvariant()
    "$artifactHash  artifact-layout.zip" | Set-Content -LiteralPath (Join-Path $artifactStage "group3/artifact-layout.zip.sha256") -Encoding ascii

    $expectedGroupCounts = @(4, 4, 5)
    for ($group = 1; $group -le 3; $group++) {
        $actualCount = @(Get-ChildItem -LiteralPath (Join-Path $artifactStage "group$group") -File).Count
        if ($actualCount -ne $expectedGroupCounts[$group - 1]) { throw "模拟下载分组 $group 文件数量不正确。" }
        New-Item -ItemType Directory -Path $downloaded -Force | Out-Null
        Get-ChildItem -LiteralPath (Join-Path $artifactStage "group$group") -File | Copy-Item -Destination $downloaded
    }
    $downloadedManifest = Join-Path $downloaded "artifact-layout.zip.parts.json"
    $downloadedJoiner = Join-Path $downloaded "AI漫剧制作大师-离线套件合并工具.ps1"
    $assembled = Join-Path $downloaded "artifact-layout-reassembled.zip"
    & $downloadedJoiner -ManifestPath $downloadedManifest -OutputPath $assembled
    if (!(Test-Path -LiteralPath $assembled) -or
        (Get-FileHash -LiteralPath $artifactSource -Algorithm SHA256).Hash -ne (Get-FileHash -LiteralPath $assembled -Algorithm SHA256).Hash) {
        throw "三个下载分组无法还原与原始 ZIP 完全一致的文件。"
    }
    $downloadedChecksum = (Get-Content -LiteralPath (Join-Path $downloaded "artifact-layout.zip.sha256") -Raw).Trim()
    if (!$downloadedChecksum.StartsWith("$artifactHash  artifact-layout.zip")) {
        throw "模拟下载的完整 ZIP SHA-256 文件内容不正确。"
    }

    Write-Host "离线安装套件分卷与合并测试通过。"
} finally {
    Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue
}
