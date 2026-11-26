param(
    [string]$InstallerPath = (Join-Path $PSScriptRoot "AI漫剧制作大师-Setup.exe"),
    [string]$ResourceBundlePath = (Join-Path $PSScriptRoot "AI漫剧大师-AI离线资源包-2026.11.zip"),
    [string]$InstallDirectory = "",
    [string]$DataDirectory = ""
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.IO.Compression.FileSystem

function Read-Choice([string]$Prompt, [string]$Default) {
    $value = Read-Host "$Prompt（直接回车使用：$Default）"
    if ([string]::IsNullOrWhiteSpace($value)) { return $Default }
    return [System.IO.Path]::GetFullPath($value)
}

if (!(Test-Path -LiteralPath $InstallerPath -PathType Leaf)) { throw "找不到桌面安装程序：$InstallerPath" }
if (!(Test-Path -LiteralPath $ResourceBundlePath -PathType Leaf)) { throw "找不到完整 AI 离线资源包：$ResourceBundlePath" }

$hashFile = "$ResourceBundlePath.sha256"
if (!(Test-Path -LiteralPath $hashFile -PathType Leaf)) { throw "缺少资源包 SHA-256 校验文件：$hashFile" }
$expectedHash = ((Get-Content -LiteralPath $hashFile -Raw).Trim() -split '\s+')[0].ToLowerInvariant()
if ($expectedHash -notmatch '^[a-f0-9]{64}$') { throw "资源包 SHA-256 文件格式无效。" }
$actualHash = (Get-FileHash -LiteralPath $ResourceBundlePath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualHash -ne $expectedHash) { throw "资源包 SHA-256 校验失败，已停止安装。" }

$defaultInstall = Join-Path $env:ProgramFiles "AI漫剧制作大师"
$defaultData = Join-Path $env:LOCALAPPDATA "AI漫剧制作大师-Data"
if (!$InstallDirectory) { $InstallDirectory = Read-Choice "程序安装目录" $defaultInstall }
if (!$DataDirectory) { $DataDirectory = Read-Choice "AI 模型和项目数据目录（建议选择空间充足的磁盘）" $defaultData }
$InstallDirectory = [System.IO.Path]::GetFullPath($InstallDirectory)
$DataDirectory = [System.IO.Path]::GetFullPath($DataDirectory)

function Normalize-DirectoryPath([string]$Path) {
    $full = [System.IO.Path]::GetFullPath($Path)
    $root = [System.IO.Path]::GetPathRoot($full)
    if ($full.Length -gt $root.Length) { $full = $full.TrimEnd([char[]]@('\','/')) }
    return $full
}
function Is-ChildPath([string]$Parent, [string]$Candidate) {
    $parentFull = Normalize-DirectoryPath $Parent
    if (!$parentFull.EndsWith([System.IO.Path]::DirectorySeparatorChar)) { $parentFull += [System.IO.Path]::DirectorySeparatorChar }
    return (Normalize-DirectoryPath $Candidate).StartsWith($parentFull, [System.StringComparison]::OrdinalIgnoreCase)
}
$InstallDirectory = Normalize-DirectoryPath $InstallDirectory
$DataDirectory = Normalize-DirectoryPath $DataDirectory
if ($InstallDirectory.Equals($DataDirectory, [System.StringComparison]::OrdinalIgnoreCase) -or
    (Is-ChildPath $InstallDirectory $DataDirectory) -or (Is-ChildPath $DataDirectory $InstallDirectory)) {
    throw "程序安装目录与 AI 数据目录不能相同或互相包含，请选择两个独立目录。"
}

function Assert-ManagedEnginesStopped {
    $engineRoot = Normalize-DirectoryPath (Join-Path $DataDirectory "engines")
    $enginePrefix = $engineRoot.TrimEnd([char[]]@([char]92,[char]47)) + [System.IO.Path]::DirectorySeparatorChar
    try {
        $processes = @(Get-CimInstance -ClassName Win32_Process -ErrorAction Stop)
    } catch {
        throw "无法安全检查 AI 漫剧大师和本地引擎是否仍在运行；为避免替换被占用的文件，已停止安装。错误：$($_.Exception.Message)"
    }
    $running = @($processes | Where-Object {
        $name = [string]$_.Name
        $exe = [string]$_.ExecutablePath
        $commandLine = [string]$_.CommandLine
        $knownApp = $name -ieq "AI漫剧制作大师.exe" -or $name -ieq "ai-comic-api.exe"
        $exeMatches = $exe -and $exe.StartsWith($enginePrefix, [System.StringComparison]::OrdinalIgnoreCase)
        $commandMatches = $commandLine -and $commandLine.IndexOf($enginePrefix, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
        return ($knownApp -or $exeMatches -or $commandMatches)
    })
    if ($running.Count -gt 0) {
        $details = ($running | ForEach-Object { "$($_.Name) (PID $($_.ProcessId))" }) -join ", "
        throw "检测到 AI 漫剧大师或受管本地引擎仍在运行：$details。请先正常退出软件后重试；安装器不会强制结束任何进程。"
    }
}

# Check before extracting the large resource archive; check again immediately before
# replacing managed engine directories to avoid overwriting files held by live processes.
Assert-ManagedEnginesStopped

$driveRoot = [System.IO.Path]::GetPathRoot($DataDirectory)
$drive = [System.IO.DriveInfo]::new($driveRoot)
$zip = [System.IO.Compression.ZipFile]::OpenRead($ResourceBundlePath)
try {
    [long]$uncompressedBytes = 0
    $zipFilePaths = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($entry in $zip.Entries) {
        $entryPath = [string]$entry.FullName
        if ([string]::IsNullOrWhiteSpace($entryPath)) { continue }
        if ([System.IO.Path]::IsPathRooted($entryPath) -or $entryPath.Contains(':') -or
            $entryPath -match '(^|[\\/])\.\.([\\/]|$)') { throw "资源 ZIP 包含不安全路径：$entryPath" }
        $unixFileType = ($entry.ExternalAttributes -shr 16) -band 0xF000
        $windowsAttributes = $entry.ExternalAttributes -band 0xFFFF
        if ($unixFileType -eq 0xA000 -or
            ($windowsAttributes -band [int][IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "资源 ZIP 不允许包含符号链接或重解析点：$entryPath"
        }
        # Validate directory entries too; only regular files participate in the inventory.
        if ([string]::IsNullOrEmpty($entry.Name)) { continue }
        $normalizedZipPath = $entryPath.Replace('\','/')
        if (!$zipFilePaths.Add($normalizedZipPath)) { throw "资源 ZIP 包含重复文件路径：$entryPath" }
        $uncompressedBytes += [long]$entry.Length
    }
} finally { $zip.Dispose() }
$requiredFree = $uncompressedBytes + 1GB
if ($drive.AvailableFreeSpace -lt $requiredFree) {
    throw ("AI 数据盘空间不足。按 ZIP 解压大小计算，暂存至少需要 {0:N2} GiB（含 1 GiB 余量）；当前 {1:N2} GiB。" -f ($requiredFree / 1GB), ($drive.AvailableFreeSpace / 1GB))
}
New-Item -ItemType Directory -Path $DataDirectory -Force | Out-Null
$oldUserData = [Environment]::GetEnvironmentVariable("AICOMICS_DATA_DIR", "User")
$oldProcessData = $env:AICOMICS_DATA_DIR
Write-Host "[1/5] 资源包 SHA-256、ZIP 路径和空间预检通过，尚未启动安装器。"

$staging = Join-Path $DataDirectory (".ai-resource-staging-" + [guid]::NewGuid().ToString("N"))
$engines = Join-Path $DataDirectory "engines"
New-Item -ItemType Directory -Path $staging -Force | Out-Null
try {
    Write-Host "[3/5] 解压 AI 离线资源包（约 18.5 GiB），请勿关闭窗口。"
    [System.IO.Compression.ZipFile]::ExtractToDirectory($ResourceBundlePath, $staging)

    $manifestPath = Join-Path $staging "resource-manifest.json"
    $inventoryPath = Join-Path $staging "file-inventory.json"
    foreach ($required in @($manifestPath, $inventoryPath, (Join-Path $staging "Ollama\runtime\ollama.exe"),
        (Join-Path $staging "Ollama\models\blobs"), (Join-Path $staging "FFmpeg\ffmpeg.exe"),
        (Join-Path $staging "FFmpeg\ffprobe.exe"), (Join-Path $staging "VACE\models\wan2.1-v3-vace-1.3b-q4_0.gguf"),
        (Join-Path $staging "VACE\models\umt5-xxl-encoder-q4_k_m.gguf"),
        (Join-Path $staging "VACE\models\pig_wan_vae_fp32-f16.gguf"), (Join-Path $staging "StableDiffusionCpp"))) {
        if (!(Test-Path -LiteralPath $required)) { throw "资源包解压后缺少必要文件或目录：$required" }
    }
    $comfyLayouts = @(
        @{ Python = "ComfyUI\python_embeded\python.exe"; Main = "ComfyUI\ComfyUI\main.py" },
        @{ Python = "ComfyUI\venv\Scripts\python.exe"; Main = "ComfyUI\main.py" }
    )
    $comfyValid = $false
    foreach ($layout in $comfyLayouts) {
        if ((Test-Path -LiteralPath (Join-Path $staging $layout.Python) -PathType Leaf) -and
            (Test-Path -LiteralPath (Join-Path $staging $layout.Main) -PathType Leaf)) { $comfyValid = $true; break }
    }
    if (!$comfyValid) { throw "ComfyUI 运行环境不完整，未找到受支持的 Python 与 main.py 布局。" }
    $videoCli = Get-ChildItem -LiteralPath (Join-Path $staging "StableDiffusionCpp") -Filter "sd-cli.exe" -File -Recurse | Select-Object -First 1
    if (!$videoCli) { throw "资源包缺少 stable-diffusion.cpp 的 sd-cli.exe。" }

    Write-Host "[4/5] 校验资源清单中的全部文件 SHA-256。"
    $inventory = @(Get-Content -LiteralPath $inventoryPath -Raw | ConvertFrom-Json)
    if ($inventory.Count -eq 0) { throw "资源文件清单为空。" }
    $seenInventoryPaths = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($entry in $inventory) {
        $relative = [string]$entry.path
        if (!$seenInventoryPaths.Add($relative.Replace('\', '/'))) { throw "资源清单存在重复路径：$relative" }
        if ([System.IO.Path]::IsPathRooted($relative) -or $relative.Contains(':') -or $relative -match '(^|[\\/])\.\.([\\/]|$)') {
            throw "资源清单包含不安全路径：$relative"
        }
        $file = [System.IO.Path]::GetFullPath((Join-Path $staging $relative))
        $prefix = [System.IO.Path]::GetFullPath($staging).TrimEnd([char[]]@('\','/')) + [System.IO.Path]::DirectorySeparatorChar
        if (!$file.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) { throw "资源路径越界：$relative" }
        if (!(Test-Path -LiteralPath $file -PathType Leaf)) { throw "资源文件缺失：$relative" }
        $item = Get-Item -LiteralPath $file -Force
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "资源文件不允许是重解析点：$relative" }
        $directoryToCheck = Split-Path -Path $file -Parent
        while ($directoryToCheck -and $directoryToCheck.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            $directoryItem = Get-Item -LiteralPath $directoryToCheck -Force
            if (($directoryItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "资源目录不允许是重解析点：$relative"
            }
            $directoryToCheck = Split-Path -Path $directoryToCheck -Parent
        }
        if ($null -eq $entry.size -or $item.Length -ne [long]$entry.size) { throw "资源文件大小不匹配：$relative" }
        $actual = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne ([string]$entry.sha256).ToLowerInvariant()) { throw "资源文件 SHA-256 不匹配：$relative" }
        if (!$zipFilePaths.Contains($relative.Replace('\','/'))) { throw "资源清单文件不在 ZIP 中：$relative" }
    }
    if ($seenInventoryPaths.Contains("file-inventory.json")) { throw "file-inventory.json 不得把自身列入哈希清单。" }
    $extraZipFiles = New-Object 'System.Collections.Generic.List[string]'
    foreach ($zipPath in $zipFilePaths) {
        if (!$seenInventoryPaths.Contains($zipPath)) { $extraZipFiles.Add($zipPath) }
    }
    if ($extraZipFiles.Count -ne 1 -or $extraZipFiles[0] -ne "file-inventory.json") {
        throw "ZIP 文件集合与 file-inventory.json 不一致；只允许 file-inventory.json 自身作为唯一额外文件。"
    }
    if ($seenInventoryPaths.Count -ne ($zipFilePaths.Count - 1)) {
        throw "ZIP 文件集合与 file-inventory.json 的数量不一致。"
    }

    Write-Host "[5/5] 事务式全量替换托管引擎（不清理用户项目、作品、素材和存档）。"
    $backup = Join-Path $DataDirectory (".ai-resource-backup-" + [guid]::NewGuid().ToString("N"))
    $deployed = New-Object System.Collections.Generic.List[object]
    $manifestWasUpdated = $false
    $hadManifest = $false
    $deploymentSucceeded = $false
    New-Item -ItemType Directory -Path $backup -Force | Out-Null
    New-Item -ItemType Directory -Path $engines -Force | Out-Null
    try {
        Assert-ManagedEnginesStopped
        foreach ($name in @("Ollama", "ComfyUI", "FFmpeg", "StableDiffusionCpp", "VACE")) {
            $source = Join-Path $staging $name
            if (!(Test-Path -LiteralPath $source -PathType Container)) { throw "资源包缺少引擎目录：$name" }
            $destination = Join-Path $engines $name
            $backupPath = Join-Path $backup $name
            $hadPrevious = Test-Path -LiteralPath $destination
            if ($hadPrevious) { Move-Item -LiteralPath $destination -Destination $backupPath }
            # Record before moving the new directory so outer rollback can restore
            # the previous engine if deployment fails midway.
            $deployed.Add([pscustomobject]@{ Destination = $destination; Backup = $backupPath; HadPrevious = $hadPrevious })
            Move-Item -LiteralPath $source -Destination $destination
        }
        $ollama = Join-Path $engines "Ollama\runtime\ollama.exe"
        $ffmpeg = Join-Path $engines "FFmpeg\ffmpeg.exe"
        $ffprobe = Join-Path $engines "FFmpeg\ffprobe.exe"
        foreach ($file in @($ollama, $ffmpeg, $ffprobe)) { if (!(Test-Path -LiteralPath $file -PathType Leaf)) { throw "安装后引擎检查失败：$file" } }
        $videoCliInstalled = Get-ChildItem -LiteralPath (Join-Path $engines "StableDiffusionCpp") -Filter "sd-cli.exe" -File -Recurse | Select-Object -First 1
        if (!$videoCliInstalled) { throw "安装后缺少 stable-diffusion.cpp sd-cli.exe。" }
        $comfyInstalled = @(
            @{ Python = "ComfyUI\python_embeded\python.exe"; Main = "ComfyUI\ComfyUI\main.py" },
            @{ Python = "ComfyUI\venv\Scripts\python.exe"; Main = "ComfyUI\main.py" }
        ) | Where-Object { (Test-Path -LiteralPath (Join-Path $engines $_.Python) -PathType Leaf) -and (Test-Path -LiteralPath (Join-Path $engines $_.Main) -PathType Leaf) } | Select-Object -First 1
        if (!$comfyInstalled) { throw "安装后 ComfyUI Python/main.py 组合检查失败。" }
        $manifestDestination = Join-Path $DataDirectory "resource-manifest.json"
        $manifestBackup = Join-Path $backup "resource-manifest.json"
        $hadManifest = Test-Path -LiteralPath $manifestDestination
        if ($hadManifest) { Copy-Item -LiteralPath $manifestDestination -Destination $manifestBackup -Force }
        # Mark before copying so a partial manifest write is also rolled back.
        $manifestWasUpdated = $true
        Copy-Item -LiteralPath $manifestPath -Destination $manifestDestination -Force
        Write-Host "[6/6] 启动桌面程序安装器。请确认程序安装目录：$InstallDirectory"
        $installerArgument = '/D="' + $InstallDirectory + '"'
        $installer = Start-Process -FilePath $InstallerPath -ArgumentList $installerArgument -Wait -PassThru
        if ($installer.ExitCode -ne 0) { throw "桌面程序安装失败，退出代码：$($installer.ExitCode)" }
        [Environment]::SetEnvironmentVariable("AICOMICS_DATA_DIR", $DataDirectory, "User")
        $env:AICOMICS_DATA_DIR = $DataDirectory
        $deploymentSucceeded = $true
    } catch {
        $originalError = $_.Exception.Message
        $rollbackErrors = New-Object 'System.Collections.Generic.List[string]'
        Write-Host "安装失败，正在回滚本次替换的托管引擎……" -ForegroundColor Yellow
        for ($i = $deployed.Count - 1; $i -ge 0; $i--) {
            $item = $deployed[$i]
            try {
                if (Test-Path -LiteralPath $item.Destination) {
                    Remove-Item -LiteralPath $item.Destination -Recurse -Force
                }
            } catch {
                $rollbackErrors.Add("无法移除新引擎目录 $($item.Destination)：$($_.Exception.Message)")
            }
            if ($item.HadPrevious -and (Test-Path -LiteralPath $item.Backup)) {
                try {
                    if (Test-Path -LiteralPath $item.Destination) {
                        throw "目标目录仍存在，不能覆盖恢复。"
                    }
                    Move-Item -LiteralPath $item.Backup -Destination $item.Destination
                } catch {
                    $rollbackErrors.Add("无法恢复旧引擎 $($item.Destination)，备份位于 $($item.Backup)：$($_.Exception.Message)")
                }
            }
        }
        $manifestDestination = Join-Path $DataDirectory "resource-manifest.json"
        $manifestBackup = Join-Path $backup "resource-manifest.json"
        try {
            if ($manifestWasUpdated -and (Test-Path -LiteralPath $manifestBackup)) {
                Copy-Item -LiteralPath $manifestBackup -Destination $manifestDestination -Force
            } elseif ($manifestWasUpdated -and !$hadManifest -and (Test-Path -LiteralPath $manifestDestination)) {
                Remove-Item -LiteralPath $manifestDestination -Force
            }
        } catch {
            $rollbackErrors.Add("无法恢复 resource-manifest.json：$($_.Exception.Message)")
        }
        try {
            [Environment]::SetEnvironmentVariable("AICOMICS_DATA_DIR", $oldUserData, "User")
            $env:AICOMICS_DATA_DIR = $oldProcessData
        } catch {
            $rollbackErrors.Add("无法恢复 AICOMICS_DATA_DIR 环境变量：$($_.Exception.Message)")
        }
        if ($rollbackErrors.Count -eq 0) {
            try {
                if (Test-Path -LiteralPath $backup) { Remove-Item -LiteralPath $backup -Recurse -Force }
            } catch {
                $rollbackErrors.Add("无法清理回滚备份；备份已保留：$($_.Exception.Message)")
            }
        }
        if ($rollbackErrors.Count -gt 0) {
            $details = $rollbackErrors -join [Environment]::NewLine
            throw "安装失败：$originalError" + [Environment]::NewLine +
                "回滚未完全成功，旧引擎备份必须保留在：$backup" + [Environment]::NewLine +
                "回滚错误：" + [Environment]::NewLine + $details
        }
        throw $originalError
    } finally {
        if ($deploymentSucceeded -and (Test-Path -LiteralPath $backup)) { Remove-Item -LiteralPath $backup -Recurse -Force -ErrorAction SilentlyContinue }
    }
} finally {
    if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
}

Write-Host ""
Write-Host "离线安装与资源导入完成。"
Write-Host "程序目录：$InstallDirectory"
Write-Host "AI 数据目录：$DataDirectory"
Write-Host "请重新登录 Windows 或重新启动桌面程序，使数据目录环境变量生效。"
Write-Host "本脚本不会联网下载模型、调用付费 API 或自动购买；只全量替换软件托管引擎目录。"
