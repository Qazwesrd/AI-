[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ComfyPayloadRoot,
    [Parameter(Mandatory = $true)]
    [string]$LocalAIPayloadRoot,
    [Parameter(Mandatory = $true)]
    [string]$ComfyInstallRoot,
    [Parameter(Mandatory = $true)]
    [string]$LocalAIInstallRoot,
    [string]$LogDirectory = (Join-Path $env:TEMP "AIComicStudio-install"),
    [object[]]$DetectedAdapters,
    [string]$ComfyInstallerPath,
    [string]$LocalAIInstallerPath
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$scriptRoot = $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($ComfyInstallerPath)) {
    $ComfyInstallerPath = Join-Path $scriptRoot "install-comfyui-offline.ps1"
}
if ([string]::IsNullOrWhiteSpace($LocalAIInstallerPath)) {
    $LocalAIInstallerPath = Join-Path $LocalAIPayloadRoot "install-local-ai-offline.ps1"
}

foreach ($requiredPath in @($ComfyPayloadRoot, $LocalAIPayloadRoot, $ComfyInstallerPath, $LocalAIInstallerPath)) {
    if (!(Test-Path -LiteralPath $requiredPath)) {
        throw "整套本地引擎安装缺少必要载荷或脚本：$requiredPath"
    }
}
New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null

$comfyTarget = [System.IO.Path]::GetFullPath($ComfyInstallRoot)
$localTarget = [System.IO.Path]::GetFullPath($LocalAIInstallRoot)
if ($comfyTarget.Equals($localTarget, [System.StringComparison]::OrdinalIgnoreCase) -or
    $comfyTarget.StartsWith($localTarget + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase) -or
    $localTarget.StartsWith($comfyTarget + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "ComfyUI 与 Ollama 安装目录不能相同或互相嵌套。"
}

$comfyExistedBefore = Test-Path -LiteralPath $comfyTarget
$localAIExistedBefore = Test-Path -LiteralPath $localTarget

function Test-NewComfyInstall([string]$Path) {
    $manifestPath = Join-Path $Path "runtime-manifest.json"
    if (!(Test-Path -LiteralPath $manifestPath -PathType Leaf) -or
        !(Test-Path -LiteralPath (Join-Path $Path "ComfyUI\main.py") -PathType Leaf) -or
        !(Test-Path -LiteralPath (Join-Path $Path "python_embeded\python.exe") -PathType Leaf)) {
        return $false
    }
    try {
        $state = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
        return ($state.schema_version -eq 1 -and $state.distribution_approved -eq $false)
    } catch { return $false }
}

function Test-NewLocalAIInstall([string]$Path) {
    $manifestPath = Join-Path $Path "install-manifest.json"
    if (!(Test-Path -LiteralPath $manifestPath -PathType Leaf) -or
        !(Test-Path -LiteralPath (Join-Path $Path "runtime\ollama.exe") -PathType Leaf)) {
        return $false
    }
    try {
        $state = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
        return ($state.schema_version -eq 1 -and $state.cloud_features_disabled -eq $true -and
            $state.distribution_approved -eq $false -and $state.model -eq "qwen3:4b")
    } catch { return $false }
}

function Remove-TransactionOwnedInstall([string]$Path, [string]$Kind) {
    if (!(Test-Path -LiteralPath $Path)) { return }
    $isOwned = $false
    if ($Kind -eq "ComfyUI") { $isOwned = Test-NewComfyInstall $Path }
    elseif ($Kind -eq "LocalAI") { $isOwned = Test-NewLocalAIInstall $Path }
    if (!$isOwned) {
        throw "回滚保护停止：$Kind 目标目录存在，但无法确认它是本次事务创建的完整安装；为保护数据，不自动删除：$Path"
    }
    Remove-Item -LiteralPath $Path -Recurse -Force
    if (Test-Path -LiteralPath $Path) {
        throw "$Kind 新安装回滚后目录仍然存在：$Path"
    }
    Write-Warning "已回滚本次事务新建的 $Kind 安装目录：$Path"
}

try {
    Write-Host "步骤 1/3：先验证两套离线载荷，不进行任何安装或覆盖。"
    & $ComfyInstallerPath -PayloadRoot $ComfyPayloadRoot -InstallRoot $comfyTarget -LogPath (Join-Path $LogDirectory "ComfyUI-preflight.log") -DetectedAdapters $DetectedAdapters -ValidateOnly
    & $LocalAIInstallerPath -PayloadRoot $LocalAIPayloadRoot -InstallRoot $localTarget -LogPath (Join-Path $LogDirectory "Ollama-preflight.log") -ValidateOnly

    Write-Host "步骤 2/3：部署 ComfyUI。安装前已存在的目录将由引擎安装器保护，不会覆盖。"
    & $ComfyInstallerPath -PayloadRoot $ComfyPayloadRoot -InstallRoot $comfyTarget -LogPath (Join-Path $LogDirectory "ComfyUI-install.log") -DetectedAdapters $DetectedAdapters
    if (!$comfyExistedBefore -and !(Test-NewComfyInstall $comfyTarget)) {
        throw "ComfyUI 安装命令结束，但未发现可验证的新安装标记。"
    }

    Write-Host "步骤 3/3：部署 Ollama 与 Qwen3 4B。若失败，只回滚本次事务新建的引擎。"
    & $LocalAIInstallerPath -PayloadRoot $LocalAIPayloadRoot -InstallRoot $localTarget -LogPath (Join-Path $LogDirectory "Ollama-install.log")
    if (!$localAIExistedBefore -and !(Test-NewLocalAIInstall $localTarget)) {
        throw "Ollama 安装命令结束，但未发现可验证的新安装标记。"
    }

    Write-Host "本地引擎整套安装事务完成。原有安装和用户模型数据未被覆盖。"
} catch {
    $originalError = $_
    $rollbackErrors = New-Object System.Collections.Generic.List[string]

    if (!$localAIExistedBefore -and (Test-Path -LiteralPath $localTarget)) {
        try { Remove-TransactionOwnedInstall $localTarget "LocalAI" }
        catch { $rollbackErrors.Add($_.Exception.Message) }
    }
    if (!$comfyExistedBefore -and (Test-Path -LiteralPath $comfyTarget)) {
        try { Remove-TransactionOwnedInstall $comfyTarget "ComfyUI" }
        catch { $rollbackErrors.Add($_.Exception.Message) }
    }

    if ($rollbackErrors.Count -gt 0) {
        throw ("整套本地引擎安装失败：{0}；同时发生回滚异常：{1}" -f $originalError.Exception.Message, ($rollbackErrors -join "；"))
    }
    throw $originalError
}
