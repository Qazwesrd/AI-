$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$root = Split-Path -Parent $PSScriptRoot
$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("ai-comic-comfyui-smoke-" + [guid]::NewGuid().ToString("N"))
$payloadRoot = Join-Path $tempRoot "ComfyUI-Payload"
$noticeRoot = Join-Path $tempRoot "THIRD_PARTY_NOTICES"
$zipPath = Join-Path $tempRoot "synthetic-offline-bundle.zip"
$setupPath = Join-Path $tempRoot "Setup.exe"

function Assert-Contains([string]$Text, [string]$Pattern, [string]$Message) {
    if ($Text -notmatch $Pattern) { throw $Message }
}

try {
    New-Item -ItemType Directory -Path $payloadRoot, $noticeRoot -Force | Out-Null
    [byte[]]$setupBytes = 0..255
    [System.IO.File]::WriteAllBytes($setupPath, $setupBytes)

    $assetNames = @(
        "ComfyUI_windows_portable_nvidia.7z",
        "ComfyUI_windows_portable_nvidia_cu126.7z",
        "ComfyUI_windows_portable_amd.7z",
        "ComfyUI_windows_portable_intel.7z"
    )
    $assets = @()
    foreach ($name in $assetNames) {
        $file = Join-Path $payloadRoot $name
        [System.IO.File]::WriteAllText($file, "synthetic-$name", [System.Text.Encoding]::UTF8)
        $assets += @{
            name = $name
            source = "https://github.com/Comfy-Org/ComfyUI/releases/download/v-smoke/$name"
            size_bytes = (Get-Item $file).Length
            sha256 = (Get-FileHash $file -Algorithm SHA256).Hash.ToLowerInvariant()
            verified = $true
        }
    }

    [System.IO.File]::WriteAllText((Join-Path $payloadRoot "7zr.exe"), "synthetic-7zr")
    [System.IO.File]::WriteAllText((Join-Path $payloadRoot "manifest.json"), (@{
        schema_version = 1
        product = "AI漫剧制作大师"
        bundle_type = "complete-offline-comfyui-engine"
        comfyui_version = "v-smoke"
        target_architecture = "windows-x64"
        assets = $assets
        tools = @(@{ name = "7zr.exe"; source = "https://github.com/ip7z/7zip/releases/download/v-smoke/7zr.exe"; sha256 = (Get-FileHash (Join-Path $payloadRoot "7zr.exe") -Algorithm SHA256).Hash.ToLowerInvariant() })
        license = @{ license_file = "THIRD_PARTY_NOTICES/ComfyUI-GPL-3.0-LICENSE.txt" }
        seven_zip = @{ license_file = "THIRD_PARTY_NOTICES/7-Zip-LICENSE.txt"; source_archive = "THIRD_PARTY_NOTICES/7zip-source.7z"; source_sha256 = "0" * 64; version = "v-smoke" }
    } | ConvertTo-Json -Depth 8), [System.Text.Encoding]::UTF8)

    [System.IO.File]::WriteAllText((Join-Path $payloadRoot "install-comfyui-offline.ps1"), "# synthetic installer")
    [System.IO.File]::WriteAllText((Join-Path $noticeRoot "ComfyUI-GPL-3.0-LICENSE.txt"), "GNU GENERAL PUBLIC LICENSE")
    [System.IO.File]::WriteAllText((Join-Path $noticeRoot "7-Zip-LICENSE.txt"), "GNU LGPL")
    [System.IO.File]::WriteAllText((Join-Path $noticeRoot "7zip-source.7z"), "synthetic-source")
    [System.IO.File]::WriteAllText((Join-Path $tempRoot "安装说明.txt"), "synthetic instructions")
    $setupHash = (Get-FileHash $setupPath -Algorithm SHA256).Hash
    [System.IO.File]::WriteAllText((Join-Path $tempRoot "Setup.exe.sha256"), "$setupHash  Setup.exe")

    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $stream = [System.IO.File]::Open($zipPath, [System.IO.FileMode]::CreateNew)
    try {
        $archive = [System.IO.Compression.ZipArchive]::new($stream, [System.IO.Compression.ZipArchiveMode]::Create, $true)
        try {
            foreach ($file in Get-ChildItem $tempRoot -File -Recurse | Where-Object FullName -ne $zipPath) {
                $relative = $file.FullName.Substring($tempRoot.Length + 1).Replace("\", "/")
                [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($archive, $file.FullName, $relative) | Out-Null
            }
        } finally { $archive.Dispose() }
    } finally { $stream.Dispose() }

    $readStream = [System.IO.File]::OpenRead($zipPath)
    try {
        $readArchive = [System.IO.Compression.ZipArchive]::new($readStream, [System.IO.Compression.ZipArchiveMode]::Read, $false)
        try {
            $names = @($readArchive.Entries | ForEach-Object FullName)
            foreach ($required in @(
                "Setup.exe",
                "Setup.exe.sha256",
                "安装说明.txt",
                "ComfyUI-Payload/manifest.json",
                "ComfyUI-Payload/7zr.exe",
                "ComfyUI-Payload/install-comfyui-offline.ps1",
                "THIRD_PARTY_NOTICES/ComfyUI-GPL-3.0-LICENSE.txt",
                "THIRD_PARTY_NOTICES/7-Zip-LICENSE.txt",
                "THIRD_PARTY_NOTICES/7zip-source.7z"
            )) {
                if (@($names | Where-Object { $_ -ceq $required }).Count -ne 1) {
                    throw "Synthetic bundle is missing or duplicates required entry: $required"
                }
            }
            if (@($names | Where-Object { $_ -like "ComfyUI-Payload/*.7z" }).Count -ne 4) {
                throw "Synthetic bundle must contain exactly four GPU archive slots."
            }
        } finally { $readArchive.Dispose() }
    } finally { $readStream.Dispose() }

    $installer = Get-Content (Join-Path $root "scripts/install-comfyui-offline.ps1") -Raw
    Assert-Contains $installer 'ConvertFrom-Json' "Installer must parse the offline manifest."
    Assert-Contains $installer 'Get-Sha256' "Installer must verify SHA-256."
    Assert-Contains $installer 'Get-ArchiveEntries' "Installer must inspect archive paths and sizes."
    Assert-Contains $installer 'runtime_mode = "cpu"' "Installer must preserve CPU fallback."
    Assert-Contains $installer 'Move-Item -LiteralPath \$portableRoot -Destination \$installFullPath' "Installer must stage before deploying the portable root."
    Assert-Contains $installer '不会猜测或联网下载' "Installer must fail closed instead of guessing or downloading a fallback."

    $nsh = Get-Content (Join-Path $root "apps/desktop/build-resources/installer.nsh") -Raw
    Assert-Contains $nsh 'ComfyUI-Payload\\install-comfyui-offline.ps1' "NSIS must invoke the adjacent offline payload installer."
    Assert-Contains $nsh '完整解压整个安装 ZIP' "NSIS must explain the full-ZIP prerequisite."

    Write-Host "PASS: synthetic offline ZIP structure and required payload paths."
    Write-Host "PASS: installer source retains manifest, checksum, archive-safety, and CPU fallback guards."
    Write-Host "PASS: NSIS expects the adjacent ComfyUI-Payload directory."
    Write-Host "NOTE: this is a contract smoke test only; it does not download, extract, install, or run ComfyUI."
} finally {
    if (Test-Path $tempRoot) { Remove-Item $tempRoot -Recurse -Force -ErrorAction SilentlyContinue }
}
