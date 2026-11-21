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

# User projects, generated media, model weights and configuration live outside
# the application install directory. Uninstall must never recursively remove
# those user-owned roots or opt into electron-builder's app-data deletion.
$destructiveUserPath = '(?im)^\s*(?:RMDir\s+/r|Delete)\s+.*(?:\$PROFILE|AIComicStudio|APPDATA|LOCALAPPDATA)'
if ($installer -match $destructiveUserPath -or $installer -match '(?im)^\s*!macro\s+customUnInstall\b') {
    throw "NSIS 卸载/安装脚本包含可能删除用户数据目录的命令。"
}
if ($package.build.nsis.include -ne "build-resources/installer.nsh") {
    throw "NSIS 安装器未使用经过审计的数据保护脚本。"
}
$deleteAppDataSetting = $package.build.nsis.PSObject.Properties["deleteAppDataOnUninstall"]
if ($null -ne $deleteAppDataSetting -and $deleteAppDataSetting.Value -eq $true) {
    throw "禁止启用 deleteAppDataOnUninstall；用户数据必须默认保留。"
}

# The desktop process uses Electron's userData directory, while bundled
# ComfyUI/Ollama runtimes are stored under the user's home directory.
if ($main -notmatch 'const dataDir = app\.getPath\("userData"\)' -or
    $main -notmatch 'APP_DATA_DIR:\s*dataDir' -or
    $main -notmatch 'app\.getPath\("home"\)' -or
    $database -notmatch 'DATA_ROOT = Path\(os\.environ\.get\("APP_DATA_DIR"' -or
    $database -notmatch 'STORAGE_DIR = DATA_ROOT / "storage"') {
    throw "桌面端用户数据与本地 AI 引擎必须使用独立的用户目录，不能混入应用安装目录。"
}
if ($installer -notmatch '\$PROFILE\\AIComicStudio\\ComfyUI' -or
    $installer -notmatch '\$PROFILE\\AIComicStudio\\Ollama' -or
    $comfyRuntime -notmatch 'Path\.home\(\) / "AIComicStudio" / "ComfyUI"') {
    throw "安装脚本未明确将 ComfyUI 和 Ollama 安装到用户数据目录。"
}

Write-Host "Windows 卸载数据保护静态检查通过：NSIS 不定义自定义删除钩子；数据库/项目存储位于 Electron userData，ComfyUI、Ollama 和模型位于用户目录；卸载默认保留这些数据。"
