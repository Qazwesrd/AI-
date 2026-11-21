$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$installerPath = Join-Path $repoRoot "apps/desktop/build-resources/installer.nsh"
$packagePath = Join-Path $repoRoot "apps/desktop/package.json"
$mainPath = Join-Path $repoRoot "apps/desktop/main.cjs"

foreach ($path in @($installerPath, $packagePath, $mainPath)) {
    if (!(Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "卸载数据保护检查缺少必要文件：$path"
    }
}

$installer = Get-Content -LiteralPath $installerPath -Raw -Encoding UTF8
$package = Get-Content -LiteralPath $packagePath -Raw -Encoding UTF8 | ConvertFrom-Json
$main = Get-Content -LiteralPath $mainPath -Raw -Encoding UTF8

# User projects, generated media, model weights and configuration live outside
# the application install directory. Uninstall must never recursively remove
# those user-owned roots or opt into electron-builder's app-data deletion.
$destructiveUserPath = '(?im)^\s*(?:RMDir\s+/r|Delete)\s+.*(?:\$PROFILE|AIComicStudio|APPDATA|LOCALAPPDATA)'
if ($installer -match $destructiveUserPath) {
    throw "NSIS 卸载/安装脚本包含可能删除用户数据目录的命令。"
}
if ($package.build.nsis.deleteAppDataOnUninstall -eq $true) {
    throw "禁止启用 deleteAppDataOnUninstall；用户数据必须默认保留。"
}

# The desktop process uses Electron's userData directory, while bundled
# ComfyUI/Ollama runtimes are stored under the user's home directory.
if ($main -notmatch 'app\.getPath\("userData"\)' -or
    $main -notmatch 'app\.getPath\("home"\)') {
    throw "桌面端用户数据与本地 AI 引擎必须使用独立的用户目录，不能混入应用安装目录。"
}
if ($installer -notmatch '\$PROFILE\\AIComicStudio\\ComfyUI' -or
    $installer -notmatch '\$PROFILE\\AIComicStudio\\Ollama') {
    throw "安装脚本未明确将 ComfyUI 和 Ollama 安装到用户数据目录。"
}

Write-Host "Windows 卸载数据保护静态检查通过：卸载默认保留项目、模型、图片、视频、配音、字幕和配置。"
