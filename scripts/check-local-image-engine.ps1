$ErrorActionPreference = "Continue"

Write-Host "========== AI漫剧制作大师：本地图片引擎环境预检 ==========" -ForegroundColor Cyan
Write-Host "本脚本只检测环境，不安装驱动、不下载模型、不修改系统。" -ForegroundColor Yellow

Write-Host ""
Write-Host "[1/5] Windows"
Get-CimInstance Win32_OperatingSystem | Select-Object Caption, Version, OSArchitecture | Format-List

Write-Host ""
Write-Host "[2/5] CPU / 内存"
Get-CimInstance Win32_Processor | Select-Object -First 1 -ExpandProperty Name
Get-CimInstance Win32_ComputerSystem | Select-Object @{Name="物理内存GB";Expression={[math]::Round($_.TotalPhysicalMemory / 1GB, 2)}} | Format-List

Write-Host ""
Write-Host "[3/5] 显卡 / 驱动"
Get-CimInstance Win32_VideoController | Select-Object Name, DriverVersion, DriverDate, AdapterRAM | Format-List

Write-Host ""
Write-Host "[4/5] Vulkan 运行环境"
$VulkanDll = Join-Path $env:WINDIR "System32\vulkan-1.dll"
Write-Host "Vulkan Loader 路径：$VulkanDll"
Test-Path -LiteralPath $VulkanDll
Get-Command vulkaninfo.exe -ErrorAction SilentlyContinue | Select-Object Name, Source | Format-List
Get-ItemProperty "HKLM:\SOFTWARE\Khronos\Vulkan\Drivers" -ErrorAction SilentlyContinue | Format-List
Get-ItemProperty "HKLM:\SOFTWARE\WOW6432Node\Khronos\Vulkan\Drivers" -ErrorAction SilentlyContinue | Format-List

Write-Host ""
Write-Host "[5/5] DirectX 诊断信息"
$DxDiag = Join-Path $env:TEMP "ai-manga-dxdiag.txt"
$Arguments = '/t "' + $DxDiag + '"'
Start-Process -FilePath "dxdiag.exe" -ArgumentList $Arguments -Wait -WindowStyle Hidden
Select-String -Path $DxDiag -Pattern "Card name:|Manufacturer:|Driver Version:|Driver Date/Size:|Feature Levels:|Dedicated Memory:|Shared Memory:" -ErrorAction SilentlyContinue | Select-Object -First 30

Write-Host ""
Write-Host "========== 检测完成 ==========" -ForegroundColor Cyan
Write-Host "请把完整输出发回。暂时不要下载 SDXL、Flux 或大型模型。" -ForegroundColor Yellow
