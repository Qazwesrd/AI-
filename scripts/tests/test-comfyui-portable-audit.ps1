$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$auditScript = Join-Path $repoRoot "scripts\audit-comfyui-portable-dependencies.ps1"
if (!(Test-Path -LiteralPath $auditScript -PathType Leaf)) {
    throw "找不到 ComfyUI 依赖清点脚本：$auditScript"
}

$temp = Join-Path ([System.IO.Path]::GetTempPath()) ("aicomics-comfyui-audit-test-" + [Guid]::NewGuid().ToString("N"))
$sevenZip = Join-Path $temp "7zr.exe"
$fixtureRoot = Join-Path $temp "fixture"
$comfyRoot = Join-Path $fixtureRoot "ComfyUI"
$archive = Join-Path $temp "ComfyUI_windows_portable_nvidia.7z"
$reportPath = Join-Path $temp "audit-report.json"

try {
    New-Item -ItemType Directory -Path $temp, $comfyRoot -Force | Out-Null

    $release = Invoke-RestMethod -Uri "https://api.github.com/repos/ip7z/7zip/releases/latest" -Headers @{
        Accept = "application/vnd.github+json"
        "X-GitHub-Api-Version" = "2022-11-28"
        "User-Agent" = "AI-Comic-Studio-Dependency-Audit-Test"
    } -TimeoutSec 30
    $asset = @($release.assets | Where-Object { $_.name -ceq "7zr.exe" }) | Select-Object -First 1
    if (!$asset -or $asset.digest -notmatch '^sha256:([0-9a-fA-F]{64})$') {
        throw "7-Zip 官方 Release 缺少带 SHA-256 的 7zr.exe。"
    }
    $expectedHash = ([regex]::Match($asset.digest, '^sha256:([0-9a-fA-F]{64})$')).Groups[1].Value.ToLowerInvariant()
    $assetUri = [Uri]$asset.browser_download_url
    $expectedPath = "/ip7z/7zip/releases/download/$($release.tag_name)/7zr.exe"
    if ($assetUri.Scheme -ne "https" -or $assetUri.Host -ne "github.com" -or
        $assetUri.AbsolutePath -cne $expectedPath -or $assetUri.Query -or $assetUri.Fragment) {
        throw "7-Zip 7zr.exe 下载地址与官方 Release 不匹配。"
    }
    Invoke-WebRequest -Uri $assetUri.AbsoluteUri -OutFile $sevenZip -TimeoutSec 120
    if ((Get-Item -LiteralPath $sevenZip).Length -ne [long]$asset.size -or
        (Get-FileHash -LiteralPath $sevenZip -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedHash) {
        throw "7zr.exe 大小或 SHA-256 校验失败。"
    }

    $sitePackages = Join-Path $comfyRoot "python_embeded\Lib\site-packages"
    $metadataDir = Join-Path $sitePackages "audit_fixture-1.0.dist-info"
    $torchLib = Join-Path $sitePackages "torch\lib"
    New-Item -ItemType Directory -Path $metadataDir, $torchLib -Force | Out-Null
    @(
        "Metadata-Version: 2.1",
        "Name: audit-fixture-package",
        "Version: 1.0.0",
        "License: MIT",
        "Classifier: License :: OSI Approved :: MIT License",
        "Project-URL: Homepage, https://example.invalid/audit-fixture"
    ) | Set-Content -LiteralPath (Join-Path $metadataDir "METADATA") -Encoding UTF8
    "MIT License - audit fixture only." | Set-Content -LiteralPath (Join-Path $comfyRoot "LICENSE.txt") -Encoding UTF8
    "fixture runtime marker" | Set-Content -LiteralPath (Join-Path $torchLib "cudart64_130.dll") -Encoding ASCII

    Push-Location $fixtureRoot
    try {
        & $sevenZip a -t7z -mx=1 $archive "ComfyUI\*" | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "无法使用官方 7zr.exe 创建测试归档。"
        }
    } finally {
        Pop-Location
    }

    try {
        & $auditScript -SevenZipPath $sevenZip -ArchivePaths @($archive) -ReleaseTag $release.tag_name -OutputPath $reportPath
    } catch {
        Write-Host "ComfyUI audit diagnostic:"
        Write-Host ($_ | Format-List * -Force | Out-String)
        Write-Host (Get-PSCallStack | Out-String)
        throw
    }
    if (!(Test-Path -LiteralPath $reportPath -PathType Leaf)) {
        throw "依赖清点脚本没有生成报告。"
    }
    $report = Get-Content -LiteralPath $reportPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($report.audit_status -ne "pending" -or $report.distribution_approved -ne $false) {
        throw "依赖清点脚本错误地批准了未审核组件。"
    }
    if ($report.portable_variants.Count -ne 1 -or
        $report.portable_variants[0].asset -ne "ComfyUI_windows_portable_nvidia.7z" -or
        $report.portable_variants[0].inventory_status -ne "inventory_generated_review_pending") {
        throw "依赖清点脚本没有正确记录 portable 变体。"
    }
    if ($report.portable_variants[0].python_package_count -ne 1 -or
        $report.portable_variants[0].components[0].name -ne "audit-fixture-package" -or
        $report.portable_variants[0].components[0].license_review_status -ne "pending") {
        throw "依赖清点脚本没有正确提取 Python 包元数据或保持人工审核状态。"
    }
    if (@($report.portable_variants[0].runtime_files | Where-Object { $_ -match "cudart64_130\.dll$" }).Count -ne 1) {
        throw "依赖清点脚本没有识别 CUDA 运行库文件。"
    }
    if (@($report.portable_variants[0].evidence_files | Where-Object { $_.extraction_status -eq "extracted" }).Count -lt 2) {
        throw "依赖清点脚本没有实际提取 METADATA 和许可证证据。"
    }

    Write-Host "ComfyUI 依赖清点集成测试通过：真实 7zr 归档列表、证据提取、运行库识别、pending 审核门禁。"
}
finally {
    if (Test-Path -LiteralPath $temp) {
        Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue
    }
}
