[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$InputBundle,
    [Parameter(Mandatory = $true)][string]$OutputZip
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$InputBundle = (Resolve-Path -LiteralPath $InputBundle -ErrorAction Stop).Path
$OutputZip = [IO.Path]::GetFullPath($OutputZip)
$outputDir = Split-Path -Parent $OutputZip
New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
if ($InputBundle -eq $OutputZip) { throw "资源包输出路径不能覆盖原始完整离线套件。" }
if (Test-Path -LiteralPath $OutputZip) { throw "资源包目标文件已存在，为保护已有文件不覆盖：$OutputZip" }
$tempZip = "$OutputZip.partial"
$tempHash = "$OutputZip.sha256.partial"
if (Test-Path -LiteralPath $tempZip) { Remove-Item -LiteralPath $tempZip -Force }

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

$sourceStream = [IO.File]::OpenRead($InputBundle)
$sourceArchive = $null
$targetStream = $null
$targetArchive = $null
try {
    $sourceArchive = [IO.Compression.ZipArchive]::new($sourceStream, [IO.Compression.ZipArchiveMode]::Read, $false)
    if ($sourceArchive.Entries.Count -gt 100000) { throw "原始离线套件条目数超过安全上限。" }

    $names = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    $required = @(
        "ComfyUI-Payload/manifest.json",
        "ComfyUI-Payload/install-comfyui-offline.ps1",
        "ComfyUI-Payload/install-local-engines-transaction.ps1",
        "ComfyUI-Payload/7zr.exe",
        "LocalAI-Payload/manifest.json",
        "LocalAI-Payload/install-local-ai-offline.ps1",
        "LocalAI-Payload/ollama-windows-amd64.zip",
        "THIRD_PARTY_NOTICES/comfyui-portable-dependency-audit.json",
        "THIRD_PARTY_NOTICES/Stable-Diffusion-CreativeML-OpenRAIL-M-LICENSE.txt"
    )
    $entryByName = @{}
    foreach ($entry in $sourceArchive.Entries) {
        $name = [string]$entry.FullName
        if ([string]::IsNullOrWhiteSpace($name) -or $name.StartsWith("/") -or $name -match '(^|/)\.\.(/|$)' -or $name -match '^[A-Za-z]:') {
            throw "原始离线套件包含不安全 ZIP 路径：$name"
        }
        if (!$names.Add($name)) { throw "原始离线套件包含重复路径：$name" }
        $entryByName[$name] = $entry
    }
    foreach ($name in $required) {
        if (!$entryByName.ContainsKey($name)) { throw "完整离线套件缺少资源包必要文件：$name" }
    }
    if (!$entryByName.ContainsKey("Setup.exe")) { throw "原始套件缺少 Setup.exe，拒绝生成资源包。" }

    $comfyReader = [IO.StreamReader]::new($entryByName["ComfyUI-Payload/manifest.json"].Open(), [Text.Encoding]::UTF8)
    try { $comfyManifest = $comfyReader.ReadToEnd() | ConvertFrom-Json } finally { $comfyReader.Dispose() }
    $localReader = [IO.StreamReader]::new($entryByName["LocalAI-Payload/manifest.json"].Open(), [Text.Encoding]::UTF8)
    try { $localManifest = $localReader.ReadToEnd() | ConvertFrom-Json } finally { $localReader.Dispose() }
    if ($comfyManifest.bundle_type -ne "complete-offline-comfyui-engine-internal-validation-only" -or
        $comfyManifest.distribution_approved -ne $false -or
        $localManifest.bundle_type -ne "internal-offline-local-ai-runtime-and-model" -or
        $localManifest.distribution_approved -ne $false -or
        $localManifest.model.name -cne "qwen3:4b") {
        throw "原始套件的引擎或模型清单与当前内部离线资源包策略不匹配。"
    }

    $targetStream = [IO.File]::Create($tempZip)
    $targetArchive = [IO.Compression.ZipArchive]::new($targetStream, [IO.Compression.ZipArchiveMode]::Create, $false)
    $inventory = [Collections.Generic.List[object]]::new()
    $buffer = New-Object byte[] (4MB)
    [long]$totalBytes = 0
    foreach ($entry in $sourceArchive.Entries) {
        $name = [string]$entry.FullName
        if ($name -eq "Setup.exe" -or $name -eq "Setup.exe.sha256" -or $name.EndsWith("/")) { continue }
        $input = $entry.Open()
        $output = $targetArchive.CreateEntry($name, [IO.Compression.CompressionLevel]::NoCompression).Open()
        $sha = [Security.Cryptography.SHA256]::Create()
        [long]$copied = 0
        try {
            while (($read = $input.Read($buffer, 0, $buffer.Length)) -gt 0) {
                $output.Write($buffer, 0, $read)
                [void]$sha.TransformBlock($buffer, 0, $read, $buffer, 0)
                $copied += $read
            }
            [void]$sha.TransformFinalBlock([byte[]]@(), 0, 0)
            if ($copied -ne [long]$entry.Length) { throw "资源条目长度校验失败：$name" }
            $digest = ([BitConverter]::ToString($sha.Hash)).Replace("-", "").ToLowerInvariant()
        } finally {
            $sha.Dispose()
            $output.Dispose()
            $input.Dispose()
        }
        $inventory.Add([ordered]@{ path=$name; size_bytes=$copied; sha256=$digest })
        $totalBytes += $copied
    }

    $resourceManifest = [ordered]@{
        schema_version = 1
        bundle_type = "ai-comic-studio-offline-ai-resource-pack"
        architecture = "windows-x64"
        distribution_approved = $false
        audit_status = "pending"
        source_bundle_name = [IO.Path]::GetFileName($InputBundle)
        created_utc = [DateTime]::UtcNow.ToString("o")
        resource_roots = @("ComfyUI-Payload", "LocalAI-Payload", "THIRD_PARTY_NOTICES")
        entry_count = $inventory.Count
        expanded_size_bytes = $totalBytes
        entries = @($inventory)
    }
    $manifestEntry = $targetArchive.CreateEntry("AI-Resource-Pack.manifest.json", [IO.Compression.CompressionLevel]::Optimal)
    $manifestStream = $manifestEntry.Open()
    try {
        $json = $resourceManifest | ConvertTo-Json -Depth 12
        $bytes = [Text.UTF8Encoding]::new($false).GetBytes($json)
        $manifestStream.Write($bytes, 0, $bytes.Length)
    } finally { $manifestStream.Dispose() }
} finally {
    if ($targetArchive) { $targetArchive.Dispose() }
    if ($targetStream) { $targetStream.Dispose() }
    if ($sourceArchive) { $sourceArchive.Dispose() }
    $sourceStream.Dispose()
}

try {
    $packHash = (Get-FileHash -LiteralPath $tempZip -Algorithm SHA256).Hash.ToLowerInvariant()
    [IO.File]::WriteAllText($tempHash, $packHash + [Environment]::NewLine, [Text.Encoding]::ASCII)
    [IO.File]::Move($tempZip, $OutputZip)
    [IO.File]::Move($tempHash, "$OutputZip.sha256")
} catch {
    Remove-Item -LiteralPath $tempZip, $tempHash -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $OutputZip -Force -ErrorAction SilentlyContinue
    throw
}
$pack = Get-Item -LiteralPath $OutputZip
Write-Host "独立 AI 资源包已生成：$OutputZip"
Write-Host ("资源包大小：{0:N2} GiB" -f ($pack.Length / 1GB))
Write-Host "SHA-256：$packHash"
Write-Host "包含 ComfyUI、Stable Diffusion、Ollama、Qwen3 4B 和第三方许可证；不包含 Setup.exe。"
Write-Host "许可证审查仍为 pending，当前仅供内部验证，不可公开分发。"
