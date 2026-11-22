[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ManifestPath,
    [string]$OutputPath
)
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$ManifestPath = (Resolve-Path -LiteralPath $ManifestPath -ErrorAction Stop).Path
$manifest = Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($manifest.schema_version -ne 1 -or $manifest.product -cne "AI漫剧制作大师") { throw "分卷清单版本或产品标识无效。" }
if ([long]$manifest.archive_size_bytes -le 0 -or [string]$manifest.archive_sha256 -notmatch '^[0-9a-f]{64}$') { throw "完整 ZIP 的大小或 SHA-256 无效。" }
if ([long]$manifest.part_size_limit_bytes -lt 1048576 -or [long]$manifest.part_size_limit_bytes -gt 1900000000) { throw "分卷清单中的单卷大小上限无效。" }
$parts = @($manifest.parts)
if ($parts.Count -lt 1 -or $parts.Count -gt 1000) { throw "分卷数量无效。" }
$directory = Split-Path -Parent $ManifestPath
$expectedName = [IO.Path]::GetFileName([string]$manifest.archive_name)
if ($expectedName -ne [string]$manifest.archive_name -or $expectedName -notmatch '\.zip$') { throw "清单中的 ZIP 文件名无效。" }
if (!$OutputPath) { $OutputPath = Join-Path $directory $expectedName }
$OutputPath = [IO.Path]::GetFullPath($OutputPath)
$manifestFull = [IO.Path]::GetFullPath($ManifestPath)
if ($OutputPath -eq $manifestFull) { throw "输出 ZIP 不能覆盖分卷清单。" }
if (Test-Path -LiteralPath $OutputPath) { throw "目标 ZIP 已存在，为保护已有文件不覆盖：$OutputPath" }
$outputDirectory = Split-Path -Parent $OutputPath
if (!(Test-Path -LiteralPath $outputDirectory -PathType Container)) { throw "输出目录不存在：$outputDirectory" }

# Validate the complete manifest and every source volume before creating a large output file.
$totalDeclared = [long]0
$validatedParts = [Collections.Generic.List[object]]::new()
$seenNames = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
for ($i = 0; $i -lt $parts.Count; $i++) {
    $part = $parts[$i]
    $name = [string]$part.name
    $expectedPartName = "$expectedName.part$('{0:D3}' -f ($i + 1))"
    if ($name -cne $expectedPartName -or [IO.Path]::GetFileName($name) -ne $name -or !$seenNames.Add($name)) { throw "分卷名称、顺序或唯一性无效：$name" }
    $size = [long]$part.size_bytes
    if ($size -le 0 -or $size -gt [long]$manifest.part_size_limit_bytes -or [string]$part.sha256 -notmatch '^[0-9a-f]{64}$') { throw "分卷大小或清单校验值无效：$name" }
    $path = Join-Path $directory $name
    if (!(Test-Path -LiteralPath $path -PathType Leaf)) { throw "缺少分卷：$name。请把所有分卷放在合并工具和清单旁边。" }
    $file = Get-Item -LiteralPath $path
    if ([long]$file.Length -ne $size) { throw "分卷大小与清单不符：$name" }
    if ([IO.Path]::GetFullPath($path) -eq $OutputPath) { throw "输出 ZIP 路径与分卷冲突。" }
    $hash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($hash -cne [string]$part.sha256) { throw "分卷校验失败：$name。请重新下载该分卷。" }
    $totalDeclared += $size
    $validatedParts.Add([pscustomobject]@{ Path = $path; Name = $name; Size = $size })
}
if ($totalDeclared -ne [long]$manifest.archive_size_bytes) { throw "分卷声明总大小与完整 ZIP 大小不一致。" }
$driveRoot = [IO.Path]::GetPathRoot($OutputPath)
$driveInfo = [IO.DriveInfo]::new($driveRoot)
if ($driveInfo.AvailableFreeSpace -lt [long]$manifest.archive_size_bytes) {
    throw ("磁盘空间不足。合并至少需要 {0:N2} GiB，可用 {1:N2} GiB。" -f ([long]$manifest.archive_size_bytes / 1GB), ($driveInfo.AvailableFreeSpace / 1GB))
}
$tempOutput = "$OutputPath.$([guid]::NewGuid().ToString('N')).partial"
$outputStream = [IO.File]::Open($tempOutput, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
try {
    foreach ($part in $validatedParts) {
        $partInputStream = [IO.File]::OpenRead($part.Path)
        try { $partInputStream.CopyTo($outputStream, 4MB) } finally { $partInputStream.Dispose() }
        Write-Host "已验证并合并：$($part.Name)"
    }
    $outputStream.Flush($true)
} catch {
    $outputStream.Dispose()
    Remove-Item -LiteralPath $tempOutput -Force -ErrorAction SilentlyContinue
    throw
} finally { $outputStream.Dispose() }
if ((Get-Item -LiteralPath $tempOutput).Length -ne [long]$manifest.archive_size_bytes) {
    Remove-Item -LiteralPath $tempOutput -Force -ErrorAction SilentlyContinue
    throw "合并后的完整 ZIP 大小不匹配。"
}
$finalHash = (Get-FileHash -LiteralPath $tempOutput -Algorithm SHA256).Hash.ToLowerInvariant()
if ($finalHash -cne [string]$manifest.archive_sha256) {
    Remove-Item -LiteralPath $tempOutput -Force -ErrorAction SilentlyContinue
    throw "合并后的完整 ZIP SHA-256 不匹配，拒绝生成安装包。"
}
try { [IO.File]::Move($tempOutput, $OutputPath) }
catch {
    Remove-Item -LiteralPath $tempOutput -Force -ErrorAction SilentlyContinue
    throw
}
Write-Host ""
Write-Host "完整离线安装 ZIP 已通过 SHA-256 校验："
Write-Host $OutputPath
Write-Host "现在请解压 ZIP，再双击 Setup.exe。"
