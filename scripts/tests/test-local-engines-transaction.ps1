$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$transaction = Join-Path $repoRoot "scripts\install-local-engines-transaction.ps1"
if (!(Test-Path -LiteralPath $transaction -PathType Leaf)) {
    throw "找不到本地引擎事务安装脚本。"
}

$temp = Join-Path ([System.IO.Path]::GetTempPath()) ("aicomics-engine-transaction-" + [Guid]::NewGuid().ToString("N"))
$comfyPayload = Join-Path $temp "ComfyUI-Payload"
$localPayload = Join-Path $temp "LocalAI-Payload"
$fakeComfy = Join-Path $temp "fake-comfy-installer.ps1"
$fakeLocal = Join-Path $temp "fake-local-installer.ps1"
$logDirectory = Join-Path $temp "logs"

function Write-Utf8([string]$Path, [string]$Value) {
    $parent = Split-Path -Parent $Path
    if ($parent) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    [System.IO.File]::WriteAllText($Path, $Value, [System.Text.UTF8Encoding]::new($false))
}

try {
    New-Item -ItemType Directory -Path $comfyPayload, $localPayload -Force | Out-Null

    Write-Utf8 $fakeComfy @'
param(
    [string]$PayloadRoot,
    [string]$InstallRoot,
    [string]$LogPath,
    [object[]]$DetectedAdapters,
    [switch]$ValidateOnly
)
$ErrorActionPreference = "Stop"
if ($ValidateOnly) { return }
New-Item -ItemType Directory -Path (Join-Path $InstallRoot "ComfyUI"), (Join-Path $InstallRoot "python_embeded") -Force | Out-Null
[System.IO.File]::WriteAllText((Join-Path $InstallRoot "ComfyUI\main.py"), "mock")
[System.IO.File]::WriteAllText((Join-Path $InstallRoot "python_embeded\python.exe"), "mock")
[System.IO.File]::WriteAllText((Join-Path $InstallRoot "runtime-manifest.json"), '{"schema_version":1,"distribution_approved":false}')
'@

    Write-Utf8 $fakeLocal @'
param(
    [string]$PayloadRoot,
    [string]$InstallRoot,
    [string]$LogPath,
    [switch]$ValidateOnly
)
$ErrorActionPreference = "Stop"
if ($ValidateOnly) {
    if (Test-Path -LiteralPath (Join-Path $PayloadRoot "fail-preflight")) { throw "Injected local AI preflight failure." }
    return
}
if (Test-Path -LiteralPath (Join-Path $PayloadRoot "fail-install")) { throw "Injected local AI install failure." }
New-Item -ItemType Directory -Path (Join-Path $InstallRoot "runtime") -Force | Out-Null
[System.IO.File]::WriteAllText((Join-Path $InstallRoot "runtime\ollama.exe"), "mock")
[System.IO.File]::WriteAllText((Join-Path $InstallRoot "install-manifest.json"), '{"schema_version":1,"cloud_features_disabled":true,"distribution_approved":false,"model":"qwen3:4b"}')
'@

    # Scenario 1: both engines are new; Ollama fails after ComfyUI deployment.
    $rollbackRoot = Join-Path $temp "scenario-rollback"
    $rollbackComfy = Join-Path $rollbackRoot "ComfyUI"
    $rollbackLocal = Join-Path $rollbackRoot "Ollama"
    Write-Utf8 (Join-Path $localPayload "fail-install") "fail"
    $failed = $false
    try {
        & $transaction -ComfyPayloadRoot $comfyPayload -LocalAIPayloadRoot $localPayload -ComfyInstallRoot $rollbackComfy -LocalAIInstallRoot $rollbackLocal -LogDirectory $logDirectory -ComfyInstallerPath $fakeComfy -LocalAIInstallerPath $fakeLocal
    } catch {
        if ($_.Exception.Message -match "Injected local AI install failure") { $failed = $true } else { throw }
    }
    if (!$failed -or (Test-Path -LiteralPath $rollbackComfy) -or (Test-Path -LiteralPath $rollbackLocal)) {
        throw "事务回滚测试失败：Ollama 部署失败后没有清理本次新建的 ComfyUI。"
    }

    # Scenario 2: a pre-existing ComfyUI directory contains user data.
    Remove-Item -LiteralPath (Join-Path $localPayload "fail-install") -Force
    $existingRoot = Join-Path $temp "scenario-existing"
    $existingComfy = Join-Path $existingRoot "ComfyUI"
    $existingLocal = Join-Path $existingRoot "Ollama"
    New-Item -ItemType Directory -Path $existingComfy -Force | Out-Null
    Write-Utf8 (Join-Path $existingComfy "user-models-sentinel.txt") "preserve-user-data"
    Write-Utf8 (Join-Path $localPayload "fail-install") "fail"
    $failed = $false
    try {
        & $transaction -ComfyPayloadRoot $comfyPayload -LocalAIPayloadRoot $localPayload -ComfyInstallRoot $existingComfy -LocalAIInstallRoot $existingLocal -LogDirectory $logDirectory -ComfyInstallerPath $fakeComfy -LocalAIInstallerPath $fakeLocal
    } catch {
        if ($_.Exception.Message -match "Injected local AI install failure") { $failed = $true } else { throw }
    }
    if (!$failed -or (Get-Content -LiteralPath (Join-Path $existingComfy "user-models-sentinel.txt") -Raw) -ne "preserve-user-data") {
        throw "事务回滚测试失败：回滚触碰了安装前已存在的 ComfyUI 用户数据。"
    }

    # Scenario 3: the second payload fails preflight. No engine may be installed.
    Remove-Item -LiteralPath (Join-Path $localPayload "fail-install") -Force
    Write-Utf8 (Join-Path $localPayload "fail-preflight") "fail"
    $preflightRoot = Join-Path $temp "scenario-preflight"
    $preflightComfy = Join-Path $preflightRoot "ComfyUI"
    $preflightLocal = Join-Path $preflightRoot "Ollama"
    $failed = $false
    try {
        & $transaction -ComfyPayloadRoot $comfyPayload -LocalAIPayloadRoot $localPayload -ComfyInstallRoot $preflightComfy -LocalAIInstallRoot $preflightLocal -LogDirectory $logDirectory -ComfyInstallerPath $fakeComfy -LocalAIInstallerPath $fakeLocal
    } catch {
        if ($_.Exception.Message -match "Injected local AI preflight failure") { $failed = $true } else { throw }
    }
    if (!$failed -or (Test-Path -LiteralPath $preflightComfy) -or (Test-Path -LiteralPath $preflightLocal)) {
        throw "事务预检测试失败：第二套载荷预检失败前就开始安装了引擎。"
    }

    # Scenario 4: both engines install successfully.
    Remove-Item -LiteralPath (Join-Path $localPayload "fail-preflight") -Force
    $successRoot = Join-Path $temp "scenario-success"
    $successComfy = Join-Path $successRoot "ComfyUI"
    $successLocal = Join-Path $successRoot "Ollama"
    & $transaction -ComfyPayloadRoot $comfyPayload -LocalAIPayloadRoot $localPayload -ComfyInstallRoot $successComfy -LocalAIInstallRoot $successLocal -LogDirectory $logDirectory -ComfyInstallerPath $fakeComfy -LocalAIInstallerPath $fakeLocal
    if (!(Test-Path -LiteralPath (Join-Path $successComfy "runtime-manifest.json")) -or
        !(Test-Path -LiteralPath (Join-Path $successLocal "install-manifest.json"))) {
        throw "事务成功测试失败：两个本地引擎没有全部完成部署。"
    }

    Write-Host "本地引擎事务集成测试通过：双载荷预检、第二引擎失败回滚、已有 ComfyUI 用户数据保护、完整成功部署。"
}
finally {
    if (Test-Path -LiteralPath $temp) {
        Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue
    }
}
