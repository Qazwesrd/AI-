$ErrorActionPreference = "Continue"

Write-Host "========== AI漫剧制作大师：本地图片引擎环境预检 ==========" -ForegroundColor Cyan
Write-Host "本脚本只检测环境，不安装驱动、不下载模型、不修改系统。" -ForegroundColor Yellow

Write-Host ""
Write-Host "[1/5] Windows"
Get-CimInstance Win32_OperatingSystem | Select-Object Caption, Version, OSArchitecture

Write-Host ""
Write-Host "[2/5] CPU / 内存"
Get-CimInstance Win32_Processor | Select-Object -First 1 -ExpandProperty Name
Get-CimInstance Win32_ComputerSystem | Select-Object @{Name="物理内存GB";Expression={[math]::Round($_.TotalPhysicalMemory / 1GB, 2)}}

Write-Host ""
Write-Host "[3/5] 显卡 / 驱动"
Get-CimInstance Win32_VideoController | Select-Object Name, DriverVersion, DriverDate, AdapterRAM

Write-Host ""
Write-Host "[4/5] Vulkan 运行环境"
$VulkanDll = Join-Path $env:WINDIR "System32\vulkan-1.dll"
if (Test-Path -LiteralPath $VulkanDll) {
    Write-Host "找到 Vulkan Loader：$VulkanDll" -ForegroundColor Green
} else {
    Write-Host "未找到 Vulkan Loader（vulkan-1.dll）" -ForegroundColor Red
}

$VulkanInfo = Get-Command vulkaninfo.exe -ErrorAction SilentlyContinue
if ($VulkanInfo) {
    Write-Host "找到 vulkaninfo：$($VulkanInfo.Source)" -ForegroundColor Green
    & $VulkanInfo.Source --summary 2>&1 | Select-Object -First 80
} else {
    Write-Host "未找到 vulkaninfo.exe；这不等于显卡一定不支持 Vulkan。" -ForegroundColor Yellow
}

$IcdPaths = @(
    "HKLM:\SOFTWARE\Khronos\Vulkan\Drivers",
    "HKLM:\SOFTWARE\WOW6432Node\Khronos\Vulkan\Drivers"
)
foreach ($Key in $IcdPaths) {
    if (Test-Path $Key) {
        Write-Host ""
        Write-Host "注册表：$Key"
        Get-ItemProperty $Key | Format-List
    }
}

Write-Host ""
Write-Host "[5/5] DirectX 诊断信息（仅列出关键项）"
$DxDiag = Join-Path $env:TEMP "ai-manga-dxdiag.txt"
try {
    $Arguments = '/t "' + $DxDiag + '"'
    Start-Process -FilePath "dxdiag.exe" -ArgumentList $Arguments -Wait -WindowStyle Hidden
    if (Test-Path $DxDiag) {
        Select-String -Path $DxDiag -Pattern "Card name:|Manufacturer:|Driver Version:|Driver Date/Size:|Feature Levels:|Dedicated Memory:|Shared Memory:" | Select-Object -First 30
    }
} catch {
    Write-Host "无法读取 dxdiag 输出：$($_.Exception.Message)" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "========== 检测完成 ==========" -ForegroundColor Cyan
Write-Host "请把从本脚本开始到结束的输出发回。暂时不要下载 SDXL、Flux 或大型模型。" -ForegroundColor Yellow
