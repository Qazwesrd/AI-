[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$InputZip,
    [long]$PartSizeBytes = 1800000000,
    [string]$ManifestPath,
    [string]$JoinerPath
)
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$InputZip = (Resolve-Path -LiteralPath $InputZip -ErrorAction Stop).Path
if ($PartSizeBytes -lt 1048576 -or $PartSizeBytes -gt 1900000000) { throw "分卷大小必须在 1 MiB 到 1,900,000,000 字节之间，确保低于 GitHub 单文件限制。" }
$source = Get-Item -LiteralPath $InputZip
if ($source.Length -le 0) { throw "输入 ZIP 为空。" }
$directory = Split-Path -Parent $InputZip
$baseName = [IO.Path]::GetFileName($InputZip)
if (!$ManifestPath) { $ManifestPath = Join-Path $directory "$baseName.parts.json" }
if (!$JoinerPath) { $JoinerPath = Join-Path $directory "AI漫剧制作大师-离线套件合并工具.ps1" }
$manifestFull = [IO.Path]::GetFullPath($ManifestPath)
$joinerFull = [IO.Path]::GetFullPath($JoinerPath)
$sourceFull = [IO.Path]::GetFullPath($InputZip)
if ($manifestFull -eq $sourceFull -or $joinerFull -eq $sourceFull -or $manifestFull -eq $joinerFull) { throw "输出路径冲突。" }
if (!(Test-Path -LiteralPath (Split-Path -Parent $manifestFull) -PathType Container) -or !(Test-Path -LiteralPath (Split-Path -Parent $joinerFull) -PathType Container)) { throw "分卷清单或合并工具的目标目录不存在。" }
if (Test-Path -LiteralPath $manifestFull) { throw "分卷清单已存在，为保护已有文件取消：$manifestFull" }
if (Test-Path -LiteralPath $joinerFull) { throw "合并工具已存在，为保护已有文件取消：$joinerFull" }
$joinerSource = Join-Path $PSScriptRoot "join-offline-bundle.ps1"
if (!(Test-Path -LiteralPath $joinerSource -PathType Leaf)) { throw "缺少合并工具源码：$joinerSource" }

# Stage every output privately. Nothing is published until all volumes, hashes,
# manifest and joiner have been generated successfully.
$stageRoot = Join-Path $directory (".offline-parts-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $stageRoot -Force | Out-Null
$parts = [Collections.Generic.List[object]]::new()
$published = [Collections.Generic.List[string]]::new()
try {
    $sourceHashBefore = (Get-FileHash -LiteralPath $InputZip -Algorithm SHA256).Hash.ToLowerInvariant()
    $sourceStream = [IO.File]::OpenRead($InputZip)
    try {
        $index = 0
        while ($sourceStream.Position -lt $sourceStream.Length) {
            $index++
            if ($index -gt 1000) { throw "分卷数量超过安全上限。" }
            $name = "$baseName.part$('{0:D3}' -f $index)"
            $finalPath = Join-Path $directory $name
            if (Test-Path -LiteralPath $finalPath) { throw "分卷已存在，为保护已有文件取消：$finalPath" }
            $path = Join-Path $stageRoot $name
            $partStream = [IO.File]::Open($path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
            $hasher = [Security.Cryptography.IncrementalHash]::CreateHash([Security.Cryptography.HashAlgorithmName]::SHA256)
            $buffer = [byte[]]::new(4MB)
            [long]$written = 0
            try {
                while ($written -lt $PartSizeBytes -and $sourceStream.Position -lt $sourceStream.Length) {
                    $want = [int][Math]::Min($buffer.Length, $PartSizeBytes - $written)
                    $read = $sourceStream.Read($buffer, 0, $want)
                    if ($read -le 0) { throw "读取源 ZIP 时意外到达文件末尾。" }
                    $partStream.Write($buffer, 0, $read)
                    $hasher.AppendData($buffer, 0, $read)
                    $written += $read
                }
                if ($written -le 0) { throw "生成了空分卷：$name" }
                $hash = [Convert]::ToHexString($hasher.GetHashAndReset()).ToLowerInvariant()
            } finally {
                $hasher.Dispose()
                $partStream.Dispose()
            }
            $parts.Add(@{ name = $name; size_bytes = $written; sha256 = $hash })
            Write-Host ("已生成暂存分卷 {0}（{1:N2} MiB）" -f $name, ($written / 1MB))
        }
    } finally { $sourceStream.Dispose() }

    $sourceAfter = Get-Item -LiteralPath $InputZip
    $archiveHash = (Get-FileHash -LiteralPath $InputZip -Algorithm SHA256).Hash.ToLowerInvariant()
    if ([long]$sourceAfter.Length -ne [long]$source.Length -or $archiveHash -cne $sourceHashBefore) { throw "分卷过程中源 ZIP 发生变化，取消发布。" }
    $partTotal = [long](($parts | Measure-Object -Property size_bytes -Sum).Sum)
    if ($partTotal -ne [long]$source.Length) { throw "分卷总字节数与源 ZIP 不一致。" }

    $manifest = @{
        schema_version = 1
        product = "AI漫剧制作大师"
        archive_name = $baseName
        archive_size_bytes = [long]$source.Length
        archive_sha256 = $archiveHash
        part_size_limit_bytes = $PartSizeBytes
        parts = @($parts)
        assembly_tool = [IO.Path]::GetFileName($joinerFull)
    }
    $stagedManifest = Join-Path $stageRoot ([IO.Path]::GetFileName($manifestFull))
    $manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $stagedManifest -Encoding UTF8
    $stagedJoiner = Join-Path $stageRoot ([IO.Path]::GetFileName($joinerFull))
    Copy-Item -LiteralPath $joinerSource -Destination $stagedJoiner

    # Re-check all destinations immediately before publication.
    $publishItems = @()
    foreach ($part in $parts) { $publishItems += @{ Source = (Join-Path $stageRoot $part.name); Destination = (Join-Path $directory $part.name) } }
    $publishItems += @{ Source = $stagedManifest; Destination = $manifestFull }
    $publishItems += @{ Source = $stagedJoiner; Destination = $joinerFull }
    foreach ($item in $publishItems) {
        if (Test-Path -LiteralPath $item.Destination) { throw "发布目标已存在，为保护已有文件取消：$($item.Destination)" }
    }
    foreach ($item in $publishItems) {
        [IO.File]::Move($item.Source, $item.Destination)
        $published.Add($item.Destination)
    }
    Write-Host "分卷清单：$manifestFull"
    Write-Host "自动合并工具：$joinerFull"
    Write-Host ("总分卷数：{0}；完整 ZIP SHA-256：{1}" -f $parts.Count, $archiveHash)
} catch {
    foreach ($path in $published) { Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue }
    throw
} finally {
    if (Test-Path -LiteralPath $stageRoot) { Remove-Item -LiteralPath $stageRoot -Recurse -Force -ErrorAction SilentlyContinue }
}
