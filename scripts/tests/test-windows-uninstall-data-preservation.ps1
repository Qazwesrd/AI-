$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$installerPath = Join-Path $repoRoot "apps/desktop/build-resources/installer.nsh"
$packagePath = Join-Path $repoRoot "apps/desktop/package.json"
$mainPath = Join-Path $repoRoot "apps/desktop/main.cjs"
$databasePath = Join-Path $repoRoot "apps/api/app/database.py"
$comfyRuntimePath = Join-Path $repoRoot "apps/api/app/routers/local_image_runtime.py"

foreach ($path in @($installerPath, $packagePath, $mainPath, $databasePath, $comfyRuntimePath)) {
    if (!(Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "卸载数据保护检查缺少必要文件：$path"
    }
}

$installer = Get-Content -LiteralPath $installerPath -Raw -Encoding UTF8
$package = Get-Content -LiteralPath $packagePath -Raw -Encoding UTF8 | ConvertFrom-Json
$main = Get-Content -LiteralPath $mainPath -Raw -Encoding UTF8
$database = Get-Content -LiteralPath $databasePath -Raw -Encoding UTF8
$comfyRuntime = Get-Content -LiteralPath $comfyRuntimePath -Raw -Encoding UTF8

# The chosen install directory is mirrored into a sibling persistent data root.
# All application-owned data and engines must resolve from that one root.
$installRootPattern = 'path\.join\(path\.dirname\(installRoot\), path\.basename\(installRoot\) \+ "-Data"\)'
if ($main -notmatch $installRootPattern -or
    $main -notmatch 'app\.setPath\("userData", persistentDataRoot\)' -or
    $main -notmatch 'APP_DATA_DIR:\s*dataDir' -or
    $database -notmatch 'DATA_ROOT = Path\(os\.environ\.get\("APP_DATA_DIR"' -or
    $database -notmatch 'STORAGE_DIR = DATA_ROOT / "storage"' -or
    $comfyRuntime -notmatch 'def _data_root\(\)' -or
    $comfyRuntime -notmatch 'return _data_root\(\) / "engines" / "ComfyUI"') {
    throw "项目数据库、用户作品和 ComfyUI 必须统一使用所选安装盘旁边的持久数据目录。"
}

if ($installer -notmatch '\$INSTDIR-Data\\engines\\ComfyUI' -or
    $installer -notmatch '\$INSTDIR-Data\\engines\\Ollama') {
    throw "NSIS 必须将 ComfyUI 和 Ollama 安装到所选磁盘的数据目录。"
}
if ($installer -match '(?im)^\s*(?:RMDir\s+/r|Delete)\s+.*(?:\$PROFILE|AIComicStudio|APPDATA|LOCALAPPDATA)' -or
    $installer -match '(?im)^\s*!macro\s+customUnInstall\b') {
    throw "NSIS 卸载/安装脚本包含可能删除用户数据目录的命令。"
}
if ($package.build.nsis.include -ne "build-resources/installer.nsh") {
    throw "NSIS 安装器未使用经过审计的数据保护脚本。"
}
$deleteAppDataSetting = $package.build.nsis.PSObject.Properties["deleteAppDataOnUninstall"]
if ($null -ne $deleteAppDataSetting -and $deleteAppDataSetting.Value -eq $true) {
    throw "禁止启用 deleteAppDataOnUninstall；用户数据必须默认保留。"
}

Write-Host "Windows 数据目录一致性静态检查通过：数据库、作品、ComfyUI、Ollama 均跟随所选磁盘；卸载默认保留独立数据目录。"
