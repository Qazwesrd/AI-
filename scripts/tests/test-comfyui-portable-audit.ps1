$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$auditScript = Join-Path $repoRoot "scripts\audit-comfyui-portable-dependencies.ps1"
$installScript = Join-Path $repoRoot "scripts\install-comfyui-offline.ps1"
if (!(Test-Path -LiteralPath $auditScript -PathType Leaf) -or
    !(Test-Path -LiteralPath $installScript -PathType Leaf)) {
    throw "找不到 ComfyUI 依赖清点或离线安装脚本。"
}

function Write-Utf8([string]$Path, [string]$Value) {
    $parent = Split-Path -Parent $Path
    if ($parent) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    [System.IO.File]::WriteAllText($Path, $Value, [System.Text.UTF8Encoding]::new($false))
}
function Get-Sha256([string]$Path) {
    (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

$temp = Join-Path ([System.IO.Path]::GetTempPath()) ("aicomics-comfyui-audit-test-" + [Guid]::NewGuid().ToString("N"))
$sevenZip = Join-Path $temp "7zr.exe"
$fixtureRoot = Join-Path $temp "fixture"
$comfyRoot = Join-Path $fixtureRoot "ComfyUI"
$archive = Join-Path $temp "ComfyUI_windows_portable_amd.7z"
$assetNames = @(
    "ComfyUI_windows_portable_nvidia.7z",
    "ComfyUI_windows_portable_nvidia_cu126.7z",
    "ComfyUI_windows_portable_amd.7z",
    "ComfyUI_windows_portable_intel.7z"
)
$reportPath = Join-Path $temp "audit-report.json"
$bundleRoot = Join-Path $temp "installer-bundle"
$payloadRoot = Join-Path $bundleRoot "ComfyUI-Payload"
$noticesRoot = Join-Path $bundleRoot "THIRD_PARTY_NOTICES"
$installRoot = Join-Path $temp "installed\ComfyUI"
$installLog = Join-Path $temp "comfyui-install.log"

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
        (Get-Sha256 $sevenZip) -ne $expectedHash) {
        throw "7zr.exe 大小或 SHA-256 校验失败。"
    }

    $sitePackages = Join-Path $comfyRoot "python_embeded\Lib\site-packages"
    $metadataDir = Join-Path $sitePackages "audit_fixture-1.0.dist-info"
    $torchLib = Join-Path $sitePackages "torch\lib"
    $pythonRoot = Join-Path $comfyRoot "python_embeded"
    $comfyAppRoot = Join-Path $comfyRoot "ComfyUI"
    New-Item -ItemType Directory -Path $metadataDir, $torchLib, $pythonRoot, $comfyAppRoot -Force | Out-Null
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
    "mock embedded python executable" | Set-Content -LiteralPath (Join-Path $pythonRoot "python.exe") -Encoding ASCII
    "print('ComfyUI integration fixture')" | Set-Content -LiteralPath (Join-Path $comfyAppRoot "main.py") -Encoding UTF8

    Push-Location $fixtureRoot
    try {
        & $sevenZip a -t7z -mx=1 $archive "ComfyUI\*" | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "无法使用官方 7zr.exe 创建测试归档。"
        }
    } finally {
        Pop-Location
    }

    $archivePaths = @()
    foreach ($name in $assetNames) {
        $archivePath = Join-Path $temp $name
        if ($name -cne "ComfyUI_windows_portable_amd.7z") {
            Copy-Item -LiteralPath $archive -Destination $archivePath
        }
        $archivePaths += $archivePath
    }

    try {
        & $auditScript -SevenZipPath $sevenZip -ArchivePaths $archivePaths -ReleaseTag "fixture-v1.0.0" -OutputPath $reportPath
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
    if ($report.audit_status -ne "pending" -or $report.distribution_approved -ne $false -or
        $report.release_tag -ne "fixture-v1.0.0") {
        throw "依赖清点脚本错误地批准了未审核组件或记录了错误版本。"
    }
    $variants = @($report.portable_variants)
    $reportNames = @($variants | ForEach-Object { [string]$_.asset } | Sort-Object -Unique)
    $reportNameDifferences = @(Compare-Object -ReferenceObject $assetNames -DifferenceObject $reportNames)
    if ($variants.Count -ne 4 -or $reportNames.Count -ne 4 -or $reportNameDifferences.Count -gt 0) {
        throw "依赖清点报告没有覆盖四种官方 Portable 变体。"
    }
    foreach ($variant in $variants) {
        $variant.archive_path = "ComfyUI-Payload/$($variant.asset)"
        if ($variant.inventory_status -ne "inventory_generated_review_pending" -or
            $variant.python_package_count -ne 1 -or
            $variant.components[0].name -ne "audit-fixture-package" -or
            $variant.components[0].license_review_status -ne "pending") {
            throw "依赖清点脚本没有正确提取 Python 包元数据或保持人工审核状态：$($variant.asset)"
        }
        if (@($variant.runtime_files | Where-Object { $_ -match "cudart64_130\.dll$" }).Count -ne 1) {
            throw "依赖清点脚本没有识别 CUDA 运行库文件：$($variant.asset)"
        }
        if (@($variant.evidence_files | Where-Object { $_.extraction_status -eq "extracted" }).Count -lt 2) {
            throw "依赖清点脚本没有实际提取 METADATA 和许可证证据：$($variant.asset)"
        }
    }

    New-Item -ItemType Directory -Path $payloadRoot, $noticesRoot -Force | Out-Null
    foreach ($archivePath in $archivePaths) {
        Copy-Item -LiteralPath $archivePath -Destination $payloadRoot
    }
    Copy-Item -LiteralPath $sevenZip -Destination $payloadRoot
    $comfyLicenseRelative = "THIRD_PARTY_NOTICES/ComfyUI-GPL-3.0-LICENSE.txt"
    $sevenZipLicenseRelative = "THIRD_PARTY_NOTICES/7-Zip-LICENSE.txt"
    $sevenZipSourceRelative = "THIRD_PARTY_NOTICES/7z-source-fixture.7z"
    Write-Utf8 (Join-Path $bundleRoot ($comfyLicenseRelative.Replace("/", "\"))) "GNU GENERAL PUBLIC LICENSE Version 3 - fixture notice"
    Write-Utf8 (Join-Path $bundleRoot ($sevenZipLicenseRelative.Replace("/", "\"))) "GNU LGPL - fixture notice"
    $sourceArchive = Join-Path $bundleRoot ($sevenZipSourceRelative.Replace("/", "\"))
    Write-Utf8 $sourceArchive "fixture 7-Zip source archive"
    $sourceHash = Get-Sha256 $sourceArchive
    $fixtureComfyVersion = "fixture-v1.0.0"
    $manifestAssets = @()
    foreach ($name in $assetNames) {
        $assetPath = Join-Path $payloadRoot $name
        $manifestAssets += @{
            name = $name
            source = "https://github.com/Comfy-Org/ComfyUI/releases/download/$fixtureComfyVersion/$name"
            size_bytes = [long](Get-Item -LiteralPath $assetPath).Length
            sha256 = Get-Sha256 $assetPath
            verified = $true
        }
    }

    $bundleManifest = @{
        target_architecture = "windows-x64"
        bundle_type = "complete-offline-comfyui-engine-internal-validation-only"
        distribution_approved = $false
        distribution_status = "blocked-pending-third-party-dependency-audit"
        comfyui_version = $fixtureComfyVersion
        dependency_inventory_report = "THIRD_PARTY_NOTICES/comfyui-portable-dependency-inventory.json"
        assets = $manifestAssets
        license = @{ license_file = $comfyLicenseRelative }
        seven_zip = @{
            version = $release.tag_name
            license_file = $sevenZipLicenseRelative
            source_archive = $sevenZipSourceRelative
            source_sha256 = $sourceHash
        }
        tools = @(@{
            name = "7zr.exe"
            source = $asset.browser_download_url
            sha256 = $expectedHash
            size_bytes = [long]$asset.size
            verified = $true
        })
    }
    Write-Utf8 (Join-Path $payloadRoot "manifest.json") ($bundleManifest | ConvertTo-Json -Depth 8)
    Write-Utf8 (Join-Path $noticesRoot "comfyui-portable-dependency-inventory.json") ($report | ConvertTo-Json -Depth 12)
    $bundleAuditRegister = @{
        audit_status = "pending"
        distribution_approved = $false
        portable_variants = @()
    }
    foreach ($name in $assetNames) {
        $assetPath = Join-Path $payloadRoot $name
        $bundleAuditRegister.portable_variants += @{
            asset = $name
            inventory_status = "inventory_generated_review_pending"
            sha256 = Get-Sha256 $assetPath
            release_tag = $fixtureComfyVersion
        }
    }
    Write-Utf8 (Join-Path $noticesRoot "comfyui-portable-dependency-audit.json") ($bundleAuditRegister | ConvertTo-Json -Depth 8)

    $inventoryPath = Join-Path $noticesRoot "comfyui-portable-dependency-inventory.json"
    $validInventoryText = Get-Content -LiteralPath $inventoryPath -Raw -Encoding UTF8
    $tamperedInventory = $report
    $tamperedInventory.portable_variants[0].sha256 = "0" * 64
    Write-Utf8 $inventoryPath ($tamperedInventory | ConvertTo-Json -Depth 12)
    $tamperedInventoryRejected = $false
    try {
        & $installScript -PayloadRoot $payloadRoot -InstallRoot $installRoot -LogPath $installLog -DetectedAdapters @()
    } catch {
        if ($_.Exception.Message -match "依赖清点报告与资产 SHA-256") {
            $tamperedInventoryRejected = $true
        } else {
            throw
        }
    }
    if (!$tamperedInventoryRejected) {
        throw "安装器未能阻止 SHA-256 被篡改的依赖清点报告。"
    }
    Write-Utf8 $inventoryPath $validInventoryText

    $auditRegisterPath = Join-Path $noticesRoot "comfyui-portable-dependency-audit.json"
    $validAuditRegisterText = Get-Content -LiteralPath $auditRegisterPath -Raw -Encoding UTF8
    $tamperedAuditRegister = $validAuditRegisterText | ConvertFrom-Json
    $tamperedAuditRegister.portable_variants[0].sha256 = "f" * 64
    Write-Utf8 $auditRegisterPath ($tamperedAuditRegister | ConvertTo-Json -Depth 8)
    $tamperedRegisterRejected = $false
    try {
        & $installScript -PayloadRoot $payloadRoot -InstallRoot $installRoot -LogPath $installLog -DetectedAdapters @()
    } catch {
        if ($_.Exception.Message -match "第三方审查登记表与依赖清点报告不匹配") {
            $tamperedRegisterRejected = $true
        } else {
            throw
        }
    }
    if (!$tamperedRegisterRejected) {
        throw "安装器未能阻止被篡改的第三方审查登记表。"
    }
    Write-Utf8 $auditRegisterPath $validAuditRegisterText

    $detectedAdapters = @([pscustomobject]@{
        Name = "AMD Radeon(TM) Graphics"
        PNPDeviceID = "PCI\VEN_1002&DEV_1636"
    })
    try {
        & $installScript -PayloadRoot $payloadRoot -InstallRoot $installRoot -LogPath $installLog -DetectedAdapters $detectedAdapters
    } catch {
        Write-Host "ComfyUI installer diagnostic:"
        Write-Host ($_ | Format-List * -Force | Out-String)
        Write-Host (Get-PSCallStack | Out-String)
        throw
    }

    $installedMain = Join-Path $installRoot "ComfyUI\main.py"
    $installedPython = Join-Path $installRoot "python_embeded\python.exe"
    $cpuLauncher = Join-Path $installRoot "AI漫剧大师-CPU模式启动.bat"
    $runtimeManifestPath = Join-Path $installRoot "runtime-manifest.json"
    if (!(Test-Path -LiteralPath $installedMain -PathType Leaf) -or
        !(Test-Path -LiteralPath $installedPython -PathType Leaf) -or
        !(Test-Path -LiteralPath $cpuLauncher -PathType Leaf) -or
        !(Test-Path -LiteralPath $runtimeManifestPath -PathType Leaf)) {
        throw "ComfyUI 离线安装测试失败：运行环境、CPU 启动器或运行清单缺失。"
    }
    $runtimeManifest = Get-Content -LiteralPath $runtimeManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($runtimeManifest.runtime_mode -ne "cpu" -or
        $runtimeManifest.gpu_vendor -ne "AMD" -or
        $runtimeManifest.selected_asset -ne "ComfyUI_windows_portable_amd.7z") {
        throw "ComfyUI 硬件适配测试失败：Ryzen Vega 模拟设备没有选择安全的 CPU 模式。"
    }

    $sentinel = Join-Path $installRoot "ComfyUI\user\preserve-user-data.txt"
    Write-Utf8 $sentinel "preserve existing ComfyUI user data"
    try {
        & $installScript -PayloadRoot $payloadRoot -InstallRoot $installRoot -LogPath $installLog -DetectedAdapters $detectedAdapters
    } catch {
        Write-Host "ComfyUI repeat-install diagnostic:"
        Write-Host ($_ | Format-List * -Force | Out-String)
        Write-Host (Get-PSCallStack | Out-String)
        throw
    }
    if ((Get-Content -LiteralPath $sentinel -Raw) -ne "preserve existing ComfyUI user data") {
        throw "ComfyUI 升级/重复安装测试失败：现有用户数据被覆盖。"
    }

    Write-Host "ComfyUI 集成测试通过：真实 7zr 目录清点、许可证证据提取、AMD Vega CPU 模式安装、运行清单、重复安装数据保护。"
}
finally {
    if (Test-Path -LiteralPath $temp) {
        Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue
    }
}
