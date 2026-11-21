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
$parts = [Collections.Generic.List[object]]::new()
$sourceStream = [IO.File]::OpenRead($InputZip)
try {
    $index = 0
    while ($sourceStream.Position -lt $sourceStream.Length) {
        $index++
        if ($index -gt 1000) { throw "分卷数量超过安全上限。" }
        $name = "$baseName.part$('{0:D3}' -f $index)"
        $path = Join-Path $directory $name
        if (Test-Path -LiteralPath $path) { throw "分卷已存在，为保护已有文件取消：$path" }
        $partStream = [IO.File]::Open($path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
        $hasher = [Security.Cryptography.IncrementalHash]::CreateHash([Security.Cryptography.HashAlgorithmName]::SHA256)
        $buffer = [byte[]]::new(4MB)
        [long]$written = 0
        try {
            while ($written -lt $PartSizeBytes -and $sourceStream.Position -lt $sourceStream.Length) {
                $want = [int][Math]::Min($buffer.Length, $PartSizeBytes - $written)
                $read = $sourceStream.Read($buffer, 0, $want)
                if ($read -le 0) { break }
                $partStream.Write($buffer, 0, $read)
                $hasher.AppendData($buffer, 0, $read)
                $written += $read
            }
            $hash = [Convert]::ToHexString($hasher.GetHashAndReset()).ToLowerInvariant()
        } catch {
            $partStream.Dispose()
            Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
            throw
        } finally {
            $hasher.Dispose()
            $partStream.Dispose()
        }
        $parts.Add(@{ name = $name; size_bytes = $written; sha256 = $hash })
        Write-Host ("已生成 {0}（{1:N2} MiB）" -f $name, ($written / 1MB))
    }
} finally { $sourceStream.Dispose() }
$archiveHash = (Get-FileHash -LiteralPath $InputZip -Algorithm SHA256).Hash.ToLowerInvariant()
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
$tempManifest = "$manifestFull.$([guid]::NewGuid().ToString('N')).partial"
$manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $tempManifest -Encoding UTF8
Move-Item -LiteralPath $tempManifest -Destination $manifestFull
$joinerSource = Join-Path $PSScriptRoot "join-offline-bundle.ps1"
if (!(Test-Path -LiteralPath $joinerSource -PathType Leaf)) { throw "缺少合并工具源码：$joinerSource" }
Copy-Item -LiteralPath $joinerSource -Destination $joinerFull -Force
Write-Host "分卷清单：$manifestFull"
Write-Host "自动合并工具：$joinerFull"
Write-Host ("总分卷数：{0}；完整 ZIP SHA-256：{1}" -f $parts.Count, $archiveHash)
