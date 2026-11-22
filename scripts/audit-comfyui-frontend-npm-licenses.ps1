param(
    [Parameter(Mandatory = $true)]
    [string]$SourceRoot,
    [Parameter(Mandatory = $true)]
    [string]$OutputRoot,
    [Parameter(Mandatory = $true)]
    [string]$SourceCommit
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ($SourceCommit -notmatch '^[0-9a-f]{40}$') {
    throw "ComfyUI Frontend source commit must be a full immutable SHA."
}
$root = (Resolve-Path -LiteralPath $SourceRoot).Path
$packagePath = Join-Path $root "package.json"
$lockPath = Join-Path $root "pnpm-lock.yaml"
$storePath = Join-Path $root "node_modules/.pnpm"
if (!(Test-Path -LiteralPath $packagePath -PathType Leaf) -or
    !(Test-Path -LiteralPath $lockPath -PathType Leaf)) {
    throw "ComfyUI Frontend source root is missing package.json or pnpm-lock.yaml."
}

$rootPackage = Get-Content -LiteralPath $packagePath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($rootPackage.version -cne "1.59.6" -or
    $rootPackage.license -cne "GPL-3.0-only" -or
    $rootPackage.packageManager -cne "pnpm@11.13.1") {
    throw "ComfyUI Frontend version, GPL declaration, or pinned pnpm version does not match the audited source."
}

$nodeText = (& node --version 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $nodeText -notmatch '^v(\d+)\.(\d+)\.(\d+)$') {
    throw "Unable to identify the Node.js version for the frontend license audit."
}
$nodeVersion = [version]("$($Matches[1]).$($Matches[2]).$($Matches[3])")
if ($nodeVersion -lt [version]"26.8.2" -or $nodeVersion -ge [version]"27.0.0") {
    throw "ComfyUI Frontend v1.59.6 requires Node.js >=26.8.2 and <27; detected $nodeText."
}
$pnpmVersion = (& pnpm --version 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $pnpmVersion -cne "11.13.1") {
    throw "ComfyUI Frontend v1.59.6 requires pinned pnpm 11.13.1; detected $pnpmVersion."
}

Push-Location $root
try {
    & pnpm install --prod --frozen-lockfile --ignore-scripts --reporter=append-only
    if ($LASTEXITCODE -ne 0) {
        throw "Pinned production dependency installation failed; license inventory cannot proceed."
    }
} finally {
    Pop-Location
}
if (!(Test-Path -LiteralPath $storePath -PathType Container)) {
    throw "pnpm production dependency store was not created."
}

if (Test-Path -LiteralPath $OutputRoot) {
    Remove-Item -LiteralPath $OutputRoot -Recurse -Force
}
$licenseRoot = Join-Path $OutputRoot "licenses"
New-Item -ItemType Directory -Path $licenseRoot -Force | Out-Null

$packages = [System.Collections.Generic.Dictionary[string,object]]::new([System.StringComparer]::Ordinal)
$storeEntries = @(Get-ChildItem -LiteralPath $storePath -Directory -ErrorAction Stop)
foreach ($storeEntry in $storeEntries) {
    $moduleRoot = Join-Path $storeEntry.FullName "node_modules"
    if (!(Test-Path -LiteralPath $moduleRoot -PathType Container)) { continue }

    $candidateDirs = [System.Collections.Generic.List[System.IO.DirectoryInfo]]::new()
    foreach ($child in Get-ChildItem -LiteralPath $moduleRoot -Directory -ErrorAction SilentlyContinue) {
        if ($child.Name -eq ".bin") { continue }
        if ($child.Name.StartsWith("@", [System.StringComparison]::Ordinal)) {
            foreach ($scoped in Get-ChildItem -LiteralPath $child.FullName -Directory -ErrorAction SilentlyContinue) {
                $candidateDirs.Add($scoped)
            }
        } else {
            $candidateDirs.Add($child)
        }
    }

    foreach ($candidate in $candidateDirs) {
        $candidateManifest = Join-Path $candidate.FullName "package.json"
        if (!(Test-Path -LiteralPath $candidateManifest -PathType Leaf)) { continue }
        try {
            $metadata = Get-Content -LiteralPath $candidateManifest -Raw -Encoding UTF8 | ConvertFrom-Json
        } catch {
            throw "Invalid package metadata in pnpm store: $candidateManifest"
        }
        $name = [string]$metadata.name
        $version = [string]$metadata.version
        if ([string]::IsNullOrWhiteSpace($name) -or [string]::IsNullOrWhiteSpace($version)) {
            throw "Installed npm dependency has no name or version: $candidateManifest"
        }
        $key = "$name@$version"
        if (!$packages.ContainsKey($key)) {
            $packages[$key] = @{
                name = $name
                version = $version
                license = if ($metadata.license -is [string]) { [string]$metadata.license } elseif ($null -ne $metadata.license) { $metadata.license | ConvertTo-Json -Compress -Depth 8 } else { $null }
                repository = if ($metadata.repository -is [string]) { [string]$metadata.repository } elseif ($metadata.repository.url) { [string]$metadata.repository.url } else { $null }
                package_json_sha256 = (Get-FileHash -LiteralPath $candidateManifest -Algorithm SHA256).Hash.ToLowerInvariant()
                source_store_entry = $storeEntry.Name
                license_files = @()
                metadata_status = if ($metadata.license) { "license_expression_declared_review_pending" } else { "license_expression_missing_manual_review_required" }
            }
        } else {
            $existing = $packages[$key]
            if ($existing.license -ne $metadata.license) {
                throw "Conflicting license declarations for duplicate npm package $key."
            }
        }

        $record = $packages[$key]
        $candidateRoot = (Resolve-Path -LiteralPath $candidate.FullName).Path
        $licenseFiles = @(Get-ChildItem -LiteralPath $candidateRoot -File -Recurse -Depth 3 -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match '^(?i:LICENSE|LICENCE|COPYING|NOTICE)(?:[._-].*)?$' })
        foreach ($licenseFile in $licenseFiles) {
            if ($licenseFile.Length -le 0 -or $licenseFile.Length -gt 4MB) {
                throw "Npm license/notice file has an invalid size: $($licenseFile.FullName)"
            }
            $relative = [System.IO.Path]::GetRelativePath($candidateRoot, $licenseFile.FullName)
            if ([System.IO.Path]::IsPathRooted($relative) -or $relative -match '(^|[\\/])\.\.([\\/]|$)') {
                throw "Npm license/notice path escaped its package directory."
            }
            $safePackage = [regex]::Replace($key, '[^A-Za-z0-9._@+-]', '_')
            $destinationRelative = Join-Path $safePackage $relative
            $destination = Join-Path $licenseRoot $destinationRelative
            $destinationDirectory = Split-Path -Parent $destination
            New-Item -ItemType Directory -Path $destinationDirectory -Force | Out-Null
            Copy-Item -LiteralPath $licenseFile.FullName -Destination $destination -Force
            $record.license_files += @{
                path = ("licenses/" + $destinationRelative.Replace("\", "/"))
                size_bytes = [long]$licenseFile.Length
                sha256 = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant()
                original_path = $relative.Replace("\", "/")
            }
        }
    }
}

if ($packages.Count -lt 1) {
    throw "No production npm packages were found in the pnpm virtual store."
}
$records = @($packages.Values | Sort-Object name, version)
$unknown = @($records | Where-Object { [string]::IsNullOrWhiteSpace([string]$_.license) })
$withoutFiles = @($records | Where-Object { @($_.license_files).Count -eq 0 })
$licenseGroups = @($records | Group-Object { if ($_.license) { [string]$_.license } else { "UNKNOWN" } } |
    Sort-Object Name | ForEach-Object { @{ license = $_.Name; package_count = $_.Count } })
$licenseFileEntries = @($records | ForEach-Object { $_.license_files } | ForEach-Object { $_ })
$lockHash = (Get-FileHash -LiteralPath $lockPath -Algorithm SHA256).Hash.ToLowerInvariant()

$report = @{
    schema_version = 1
    product = "ComfyUI Frontend"
    frontend_version = "1.59.6"
    upstream_repository = "https://github.com/Comfy-Org/ComfyUI_frontend"
    source_commit = $SourceCommit
    source_lockfile_sha256 = $lockHash
    node_version = $nodeText
    pnpm_version = $pnpmVersion
    scan_scope = "All packages present in the pinned pnpm workspace production virtual store; intentionally broader than a single workspace package. This inventory is not proof that every package is embedded in the distributed frontend wheel."
    package_count = $records.Count
    packages = $records
}
$summary = @{
    schema_version = 1
    source_commit = $SourceCommit
    source_lockfile_sha256 = $lockHash
    package_count = $records.Count
    license_file_count = $licenseFileEntries.Count
    packages_missing_license_expression = $unknown.Count
    packages_without_detected_license_or_notice_file = $withoutFiles.Count
    license_groups = $licenseGroups
    status = "inventory_generated_manual_license_review_pending"
    distribution_approved = $false
    note = "This is an automated inventory, not legal approval. License expressions, copied texts/notices, bundled asset provenance, and exact wheel/source correspondence require human review."
}
New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null
$reportPath = Join-Path $OutputRoot "frontend-npm-dependency-licenses.json"
$summaryPath = Join-Path $OutputRoot "frontend-npm-dependency-summary.json"
$report | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $reportPath -Encoding UTF8
$summary | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $summaryPath -Encoding UTF8

Write-Host "Frontend production dependency packages: $($records.Count)"
Write-Host "Packages without a declared license expression: $($unknown.Count)"
Write-Host "Packages without detected license/notice files: $($withoutFiles.Count)"
Write-Host "Copied license/notice files: $($licenseFileEntries.Count)"
Write-Host "Review status: pending; distribution remains blocked."
