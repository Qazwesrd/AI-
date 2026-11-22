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
if ([long]$manifest.archive_size_bytes -le 0 -or $manifest.archive_sha256 -notmatch '^[0-9a-f]{64}$') { throw "完整 ZIP 的大小或 SHA-256 无效。" }
$parts = @($manifest.parts)
if ($parts.Count -lt 1 -or $parts.Count -gt 1000) { throw "分卷数量无效。" }
$directory = Split-Path -Parent $ManifestPath
if (!$OutputPath) { $OutputPath = Join-Path $directory ([string]$manifest.archive_name) }
$OutputPath = [IO.Path]::GetFullPath($OutputPath)
$expectedName = [IO.Path]::GetFileName([string]$manifest.archive_name)
if ($expectedName -ne [string]$manifest.archive_name -or $expectedName -notmatch '\.zip$') { throw "清单中的 ZIP 文件名无效。" }
$tempOutput = "$OutputPath.$([guid]::NewGuid().ToString('N')).partial"
$total = [long]0
$outputStream = [IO.File]::Open($tempOutput, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
try {
    foreach ($part in $parts) {
        $name = [string]$part.name
        if ([IO.Path]::GetFileName($name) -ne $name -or $name -notmatch ('^' + [regex]::Escape($expectedName) + '\.part[0-9]{3}$')) { throw "分卷文件名无效：$name" }
        $path = Join-Path $directory $name
        if (!(Test-Path -LiteralPath $path -PathType Leaf)) { throw "缺少分卷：$name。请把所有分卷放在合并工具和清单旁边。" }
        $file = Get-Item -LiteralPath $path
        if ([long]$file.Length -ne [long]$part.size_bytes -or [long]$part.size_bytes -le 0 -or [string]$part.sha256 -notmatch '^[0-9a-f]{64}$') { throw "分卷大小或清单校验值无效：$name" }
        $hash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($hash -ne [string]$part.sha256) { throw "分卷校验失败：$name。请重新下载该分卷。" }
        $partInputStream = [IO.File]::OpenRead($path)
        try { $partInputStream.CopyTo($outputStream, 4MB) } finally { $partInputStream.Dispose() }
        $total += [long]$file.Length
        Write-Host "已验证并合并：$name"
    }
    $outputStream.Flush($true)
} catch {
    $outputStream.Dispose()
    Remove-Item -LiteralPath $tempOutput -Force -ErrorAction SilentlyContinue
    throw
} finally { $outputStream.Dispose() }
if ($total -ne [long]$manifest.archive_size_bytes) {
    Remove-Item -LiteralPath $tempOutput -Force -ErrorAction SilentlyContinue
    throw "分卷总大小与完整 ZIP 声明不一致。"
}
$finalHash = (Get-FileHash -LiteralPath $tempOutput -Algorithm SHA256).Hash.ToLowerInvariant()
if ($finalHash -ne [string]$manifest.archive_sha256) {
    Remove-Item -LiteralPath $tempOutput -Force -ErrorAction SilentlyContinue
    throw "合并后的完整 ZIP SHA-256 不匹配，拒绝生成安装包。"
}
if (Test-Path -LiteralPath $OutputPath) { throw "目标 ZIP 已存在，为保护已有文件不覆盖：$OutputPath" }
[IO.File]::Move($tempOutput, $OutputPath)
Write-Host ""
Write-Host "完整离线安装 ZIP 已通过 SHA-256 校验："
Write-Host $OutputPath
Write-Host "现在请解压 ZIP，再双击 Setup.exe。"
