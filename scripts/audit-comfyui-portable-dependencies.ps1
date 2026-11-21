param(
    [Parameter(Mandatory = $true)]
    [string]$SevenZipPath,
    [Parameter(Mandatory = $true)]
    [string]$OutputPath,
    [Parameter(Mandatory = $true)]
    [string]$ReleaseTag,
    [Parameter(Mandatory = $true)]
    [string[]]$ArchivePaths,
    [int]$MaximumEntries = 50000,
    [int]$MaximumEvidenceFiles = 20000,
    [long]$MaximumMetadataBytes = 2MB,
    [long]$MaximumLicenseBytes = 4MB,
    [long]$MaximumTotalEvidenceBytes = 100MB
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
if ($MaximumEntries -lt 1 -or $MaximumEntries -gt 100000 -or
    $MaximumEvidenceFiles -lt 1 -or $MaximumEvidenceFiles -gt 50000 -or
    $MaximumMetadataBytes -lt 1 -or $MaximumMetadataBytes -gt 20MB -or
    $MaximumLicenseBytes -lt 1 -or $MaximumLicenseBytes -gt 20MB -or
    $MaximumTotalEvidenceBytes -lt 1 -or $MaximumTotalEvidenceBytes -gt 512MB) {
    throw "清点安全限制参数超出允许范围；条目最多 100000、证据文件最多 50000、单个元数据/许可证最多 20 MiB、证据总量最多 512 MiB。"
}


$ExpectedAssets = @(
    "ComfyUI_windows_portable_nvidia.7z",
    "ComfyUI_windows_portable_nvidia_cu126.7z",
    "ComfyUI_windows_portable_amd.7z",
    "ComfyUI_windows_portable_intel.7z"
)
$MetadataNames = @("METADATA", "PKG-INFO")
$LicenseNamePattern = '^(LICENSE|LICENCE|COPYING|NOTICE)(\.|$|[-_])'
$RuntimePattern = '(?i)(^|[/\\])(torch|torchvision|torchaudio|triton|onnxruntime|openvino|tensorrt|llvmlite|numpy\.libs|scipy\.libs)([/\\]|[-_.])|(^|[/\\])(cudart|cublas|cudnn|cusparse|hip|rocblas|rocsolver|mkl|sycl|ze_loader|libiomp|libomp|vcruntime|msvcp|python3?\d*|avcodec|avformat|avutil|swresample|swscale)[^/\\]*\.(dll|pyd)

function Get-ArchiveEntries {
    param([string]$Tool, [string]$Archive, [int]$EntryLimit)

    # Consume native output incrementally and cap listing lines before parsing.
    # This prevents an unexpectedly huge archive directory listing from being
    # materialized as one unbounded PowerShell array.
    $maximumListingLines = ([long]$EntryLimit * 12) + 1000
    $listing = New-Object System.Collections.Generic.List[string]
    & $Tool l -slt $Archive 2>&1 | ForEach-Object {
        if ($listing.Count -ge $maximumListingLines) {
            throw "归档目录清单输出超过安全上限 $maximumListingLines 行：$Archive"
        }
        $listing.Add([string]$_)
    }
    if ($LASTEXITCODE -ne 0) {
        throw "7-Zip 无法读取归档目录：$Archive"
    }

    $entries = New-Object System.Collections.Generic.List[object]
    $record = @{}
    foreach ($lineObject in $listing) {
        $line = [string]$lineObject
        if ([string]::IsNullOrWhiteSpace($line)) {
            if ($record.ContainsKey("Path") -and $record["Path"] -ne $Archive -and !$record.ContainsKey("Type")) {
                $entries.Add([pscustomobject]@{
                    Path = [string]$record["Path"]
                    Size = if ($record.ContainsKey("Size")) { [long]$record["Size"] } else { 0L }
                    IsFolder = ($record.ContainsKey("Folder") -and $record["Folder"] -eq "+")
                    Attributes = if ($record.ContainsKey("Attributes")) { [string]$record["Attributes"] } else { "" }
                    IsLink = ($record.ContainsKey("Symbolic Link") -or $record.ContainsKey("Hard Link"))
                })
                if ($entries.Count -gt $EntryLimit) {
                    throw "归档条目超过安全上限 $EntryLimit：$Archive"
                }
            }
            $record = @{}
            continue
        }
        $separator = $line.IndexOf(" = ")
        if ($separator -gt 0) {
            $key = $line.Substring(0, $separator)
            $value = $line.Substring($separator + 3)
            $record[$key] = $value
        }
    }

    if ($record.ContainsKey("Path") -and $record["Path"] -ne $Archive -and !$record.ContainsKey("Type")) {
        $entries.Add([pscustomobject]@{
            Path = [string]$record["Path"]
            Size = if ($record.ContainsKey("Size")) { [long]$record["Size"] } else { 0L }
            IsFolder = ($record.ContainsKey("Folder") -and $record["Folder"] -eq "+")
            Attributes = if ($record.ContainsKey("Attributes")) { [string]$record["Attributes"] } else { "" }
            IsLink = ($record.ContainsKey("Symbolic Link") -or $record.ContainsKey("Hard Link"))
        })
    }
    if ($entries.Count -gt $EntryLimit) {
        throw "归档条目超过安全上限 $EntryLimit：$Archive"
    }
    if ($entries.Count -eq 0) {
        throw "7-Zip 未返回任何归档文件条目，拒绝生成空清点报告：$Archive"
    }
    return $entries.ToArray()
}

function Get-MetadataValue {
    param([string]$Text, [string]$Field)
    $match = [regex]::Match($Text, "(?im)^" + [regex]::Escape($Field) + ":\s*(.+)$")
    if ($match.Success) { return $match.Groups[1].Value.Trim() }
    return $null
}

function Get-MetadataList {
    param([string]$Text, [string]$Field)
    return @([regex]::Matches($Text, "(?im)^" + [regex]::Escape($Field) + ":\s*(.+)$") |
        ForEach-Object { $_.Groups[1].Value.Trim() })
}

function Get-ExtractedEvidence {
    param(
        [string]$Tool,
        [string]$Archive,
        [string]$EntryPath,
        [string]$Destination,
        [long]$MaximumBytes
    )

    $hasDrivePrefix = $false
    if ($EntryPath.Length -ge 2) {
        $hasDrivePrefix = [char]::IsLetter($EntryPath[0]) -and $EntryPath[1] -eq [char]58
    }
    $normalizedEntryPath = $EntryPath.Replace("\", "/")
    $hasParentTraversal = @($normalizedEntryPath.Split("/") | Where-Object { $_ -eq ".." }).Count -gt 0
    if ([string]::IsNullOrWhiteSpace($EntryPath) -or $normalizedEntryPath.StartsWith("/") -or $hasDrivePrefix) {
        throw "拒绝异常归档内部路径：$EntryPath"
    }
    if ($hasParentTraversal) {
        throw "拒绝包含目录穿越的归档内部路径：$EntryPath"
    }

    $leaf = [System.IO.Path]::GetFileName($EntryPath)
    if (!$leaf) { throw "归档证据文件名无效：$EntryPath" }
    $null = New-Item -ItemType Directory -Path $Destination -Force
    $arguments = @("e", "-y", "-spd", "-o$Destination", $Archive, $EntryPath)
    $output = @(& $Tool @arguments 2>&1)
    if ($LASTEXITCODE -ne 0) {
        $outputText = $output -join [string][char]32
        throw "提取证据文件失败：$EntryPath；$outputText"
    }
    $extracted = Join-Path $Destination $leaf
    if (!(Test-Path -LiteralPath $extracted -PathType Leaf)) {
        throw "7-Zip 未生成预期的证据文件：$EntryPath"
    }
    $item = Get-Item -LiteralPath $extracted
    if ($item.Length -gt $MaximumBytes) {
        Remove-Item -LiteralPath $extracted -Force
        throw "证据文件超过大小上限：$EntryPath"
    }
    return $extracted
}

if (!(Test-Path -LiteralPath $SevenZipPath -PathType Leaf)) {
    throw "找不到 7zr.exe：$SevenZipPath"
}
$SevenZipPath = [System.IO.Path]::GetFullPath($SevenZipPath)
if ($ArchivePaths.Count -lt 1 -or $ArchivePaths.Count -gt 4) {
    throw "请提供 1 到 4 个官方 ComfyUI Windows portable 归档。"
}
if ($ReleaseTag -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$') {
    throw "ReleaseTag 格式无效：$ReleaseTag"
}

$resolvedArchives = @()
foreach ($archivePath in $ArchivePaths) {
    if (!(Test-Path -LiteralPath $archivePath -PathType Leaf)) {
        throw "找不到 portable 归档：$archivePath"
    }
    $resolved = (Resolve-Path -LiteralPath $archivePath).Path
    $assetName = [System.IO.Path]::GetFileName($resolved)
    if ($ExpectedAssets -notcontains $assetName) {
        throw "归档名称不是受支持的官方资产：$assetName"
    }
    if ($resolvedArchives | Where-Object { $_.Asset -eq $assetName }) {
        throw "同一 portable 变体不能重复清点：$assetName"
    }
    $resolvedArchives += [pscustomobject]@{ Path = $resolved; Asset = $assetName }
}

$temporaryRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("aicomics-comfyui-audit-" + [Guid]::NewGuid().ToString("N"))
$null = New-Item -ItemType Directory -Path $temporaryRoot
$report = @{
    schema_version = 1
    generated_utc = [DateTime]::UtcNow.ToString("o")
    release_tag = $ReleaseTag
    tool = "7zr.exe"
    audit_status = "pending"
    distribution_approved = $false
    note = "Automated inventory only. Extracted metadata is evidence, not a legal conclusion. Human review is required."
    portable_variants = @()
}

try {
    foreach ($archive in $resolvedArchives) {
        Write-Host "清点 $($archive.Asset)..."
        $entries = @(Get-ArchiveEntries -Tool $SevenZipPath -Archive $archive.Path -EntryLimit $MaximumEntries)
        $archiveHash = (Get-FileHash -LiteralPath $archive.Path -Algorithm SHA256).Hash.ToLowerInvariant()
        $variantRoot = Join-Path $temporaryRoot ([Guid]::NewGuid().ToString("N"))
        $components = New-Object System.Collections.Generic.List[object]
        $evidence = New-Object System.Collections.Generic.List[object]
        $runtimeFiles = New-Object System.Collections.Generic.List[string]
        $evidenceCount = 0
        $totalEvidenceBytes = 0L

        foreach ($entry in $entries) {
            if ($entry.IsFolder) { continue }
            if ($entry.Path -match $RuntimePattern) {
                $runtimeFiles.Add($entry.Path)
            }

            $leaf = [System.IO.Path]::GetFileName($entry.Path)
            $isMetadata = $MetadataNames -contains $leaf
            $isLicense = $leaf -match $LicenseNamePattern
            if (!$isMetadata -and !$isLicense) { continue }
            if ($entry.IsLink) {
                throw "拒绝提取符号链接或硬链接形式的许可证证据：$($entry.Path)"
            }

            $evidenceCount++
            if ($evidenceCount -gt $MaximumEvidenceFiles) {
                throw "证据文件数量超过安全上限 $MaximumEvidenceFiles：$($archive.Asset)"
            }
            $maxBytes = if ($isMetadata) { $MaximumMetadataBytes } else { $MaximumLicenseBytes }
            if (($totalEvidenceBytes + $entry.Size) -gt $MaximumTotalEvidenceBytes) {
                $evidence.Add([pscustomobject]@{
                    archive_path = $entry.Path
                    file_name = $leaf
                    size_bytes = $entry.Size
                    extraction_status = "skipped_total_evidence_limit"
                    reason = "Archive evidence extraction total exceeds configured limit."
                })
                continue
            }
            if ($entry.Size -gt $maxBytes) {
                $evidence.Add([pscustomobject]@{
                    archive_path = $entry.Path
                    file_name = $leaf
                    size_bytes = $entry.Size
                    extraction_status = "skipped_size_limit"
                    reason = "File exceeds configured evidence extraction limit."
                })
                continue
            }

            $destination = Join-Path $variantRoot ([Guid]::NewGuid().ToString("N"))
            $extractedPath = Get-ExtractedEvidence -Tool $SevenZipPath -Archive $archive.Path -EntryPath $entry.Path -Destination $destination -MaximumBytes $maxBytes
            $actualEvidenceBytes = (Get-Item -LiteralPath $extractedPath).Length
            if (($totalEvidenceBytes + $actualEvidenceBytes) -gt $MaximumTotalEvidenceBytes) {
                Remove-Item -LiteralPath $extractedPath -Force
                $evidence.Add([pscustomobject]@{
                    archive_path = $entry.Path
                    file_name = $leaf
                    size_bytes = $actualEvidenceBytes
                    extraction_status = "skipped_total_evidence_limit"
                    reason = "Extracted evidence exceeds configured total limit."
                })
                continue
            }
            $totalEvidenceBytes += $actualEvidenceBytes
            $evidence.Add([pscustomobject]@{
                archive_path = $entry.Path
                file_name = $leaf
                size_bytes = $entry.Size
                extraction_status = "extracted"
            })

            if ($isMetadata) {
                $metadataText = [System.IO.File]::ReadAllText($extractedPath, [System.Text.Encoding]::UTF8)
                $components.Add([pscustomobject]@{
                    name = Get-MetadataValue -Text $metadataText -Field "Name"
                    version = Get-MetadataValue -Text $metadataText -Field "Version"
                    license_expression = Get-MetadataValue -Text $metadataText -Field "License-Expression"
                    license = Get-MetadataValue -Text $metadataText -Field "License"
                    classifiers = @(Get-MetadataList -Text $metadataText -Field "Classifier" | Where-Object { $_ -like "License ::*" })
                    project_urls = @(Get-MetadataList -Text $metadataText -Field "Project-URL")
                    license_files = @(Get-MetadataList -Text $metadataText -Field "License-File")
                    metadata_path = $entry.Path
                    license_review_status = "pending"
                    evidence = @($entry.Path)
                })
            }
        }

        $report.portable_variants += @{
            asset = $archive.Asset
            archive_path = $archive.Path
            archive_size_bytes = (Get-Item -LiteralPath $archive.Path).Length
            sha256 = $archiveHash
            release_tag = $ReleaseTag
            inventory_status = "inventory_generated_review_pending"
            total_file_entries = @($entries | Where-Object { !$_.IsFolder }).Count
            extracted_evidence_bytes = $totalEvidenceBytes
            python_package_count = $components.Count
            evidence_extraction_limit_bytes = $MaximumTotalEvidenceBytes
            components = $components.ToArray()
            runtime_files = @($runtimeFiles.ToArray() | Sort-Object -Unique)
            runtime_file_entries = @($entries | Where-Object { !$_.IsFolder -and $_.Path -match $RuntimePattern } | ForEach-Object {
                [pscustomobject]@{
                    path = $_.Path
                    size_bytes = [long]$_.Size
                    review_status = "pending"
                    license_owner = $null
                }
            })
            evidence_files = $evidence.ToArray()
            unresolved_items = @(
                "Confirm each component version, license text, copyright and NOTICE obligations.",
                "Review bundled native libraries and transitive runtime dependencies.",
                "Check source-code and redistribution obligations for the exact release.",
                "Do not infer redistribution permission from a metadata License field."
            )
        }
        Remove-Item -LiteralPath $variantRoot -Recurse -Force -ErrorAction SilentlyContinue
    }

    $outputFullPath = [System.IO.Path]::GetFullPath($OutputPath)
    $outputDirectory = Split-Path -Parent $outputFullPath
    if ($outputDirectory) { $null = New-Item -ItemType Directory -Path $outputDirectory -Force }
    $json = $report | ConvertTo-Json -Depth 12
    [System.IO.File]::WriteAllText($outputFullPath, $json + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))
    Write-Host "依赖清点报告已生成：$outputFullPath"
    Write-Warning "报告仅为自动提取结果，所有组件许可证和分发义务仍需人工审核；没有任何组件被自动批准。"
}
finally {
    if (Test-Path -LiteralPath $temporaryRoot) {
        Remove-Item -LiteralPath $temporaryRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
