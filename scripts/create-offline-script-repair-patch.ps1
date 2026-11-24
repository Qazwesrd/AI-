param(
    [string]$OutputDirectory = (Join-Path $PSScriptRoot "..\apps\desktop\release")
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$outputRoot = [System.IO.Path]::GetFullPath($OutputDirectory)
$stage = Join-Path ([System.IO.Path]::GetTempPath()) ("aicomics-script-patch-" + [Guid]::NewGuid().ToString("N"))
$payloadFiles = @(
    @{ Source = "scripts/install-comfyui-offline.ps1"; Target = "ComfyUI-Payload/install-comfyui-offline.ps1" },
    @{ Source = "scripts/install-local-engines-transaction.ps1"; Target = "ComfyUI-Payload/install-local-engines-transaction.ps1" },
    @{ Source = "scripts/audit-comfyui-portable-dependencies.ps1"; Target = "ComfyUI-Payload/audit-comfyui-portable-dependencies.ps1" },
    @{ Source = "scripts/install-local-ai-offline.ps1"; Target = "LocalAI-Payload/install-local-ai-offline.ps1" }
)

try {
    New-Item -ItemType Directory -Path $outputRoot, $stage -Force | Out-Null
    $records = @()
    foreach ($item in $payloadFiles) {
        $source = Join-Path $repoRoot $item.Source
        if (!(Test-Path -LiteralPath $source -PathType Leaf)) { throw "修复脚本缺失：$($item.Source)" }
        $target = Join-Path $stage ($item.Target.Replace("/", "\"))
        New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
        $bytes = [System.IO.File]::ReadAllBytes($source)
        $hasBom = $bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF
        if (!$hasBom) {
            $normalized = [byte[]]::new($bytes.Length + 3)
            $normalized[0] = 0xEF; $normalized[1] = 0xBB; $normalized[2] = 0xBF
            [System.Buffer]::BlockCopy($bytes, 0, $normalized, 3, $bytes.Length)
            $bytes = $normalized
        }
        [System.IO.File]::WriteAllBytes($target, $bytes)
        $records += @{
            path = $item.Target
            size_bytes = [long]$bytes.Length
            sha256 = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    }

    $instructions = @"
AI 漫剧制作大师 - 本地 AI 安装脚本修复补丁

用途：只替换安装脚本，不包含模型、ComfyUI 运行包、Ollama 或 FFmpeg。
不会重新下载或覆盖任何 AI 模型和运行时资源。

使用方法：
1. 先备份 D:\完整安装文件 中的 ComfyUI-Payload 和 LocalAI-Payload 文件夹。
2. 将本补丁 ZIP 解压到 D:\完整安装文件，选择覆盖同名 .ps1 文件。
3. 保持 Setup.exe、ComfyUI-Payload、LocalAI-Payload 位于同一目录。
4. 再运行 Setup.exe。若仍失败，保留 %TEMP% 下 AIComicStudio-install 日志用于定位。

本补丁不改变第三方许可证审核状态，也不代表公开分发许可已批准。
"@
    [System.IO.File]::WriteAllText((Join-Path $stage "PATCH-INSTRUCTIONS.txt"), $instructions, [System.Text.UTF8Encoding]::new($true))
    $manifest = @{
        schema_version = 1
        patch_type = "offline-installer-scripts-only"
        contains_models = $false
        contains_runtimes = $false
        files = $records
    }
    [System.IO.File]::WriteAllText((Join-Path $stage "PATCH-MANIFEST.json"), ($manifest | ConvertTo-Json -Depth 5), [System.Text.UTF8Encoding]::new($true))

    $zip = Join-Path $outputRoot "AIComicStudio-Script-Repair-Patch.zip"
    $hashFile = "$zip.sha256"
    if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
    if (Test-Path -LiteralPath $hashFile) { Remove-Item -LiteralPath $hashFile -Force }
    Compress-Archive -Path (Join-Path $stage "*") -DestinationPath $zip -CompressionLevel Optimal
    $hash = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant()
    [System.IO.File]::WriteAllText($hashFile, "$hash  $(Split-Path -Leaf $zip)$([Environment]::NewLine)", [System.Text.Encoding]::ASCII)
    Write-Host "Script repair patch: $zip"
    Write-Host "Size: $((Get-Item -LiteralPath $zip).Length) bytes"
    Write-Host "SHA256: $hash"
}
finally {
    if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue }
}
