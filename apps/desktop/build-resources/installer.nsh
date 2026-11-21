!macro customInstall
  \${ifNot} \${isUpdated}
    IfFileExists "$EXEDIR\ComfyUI-Payload\install-comfyui-offline.ps1" 0 comfyui_payload_missing

    DetailPrint "正在检测显卡并安装匹配的 ComfyUI 本地引擎..."
    ExecWait '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$EXEDIR\ComfyUI-Payload\install-comfyui-offline.ps1" -PayloadRoot "$EXEDIR\ComfyUI-Payload" -InstallRoot "$PROFILE\AIComicStudio\ComfyUI"' $0

    \${if} $0 != 0
      MessageBox MB_ICONSTOP|MB_OK "ComfyUI 本地引擎部署失败（错误代码：$0）。安装已停止。请确认完整离线 ZIP 已解压，且电脑有受支持的 NVIDIA、AMD 或 Intel 显卡及足够磁盘空间。"
      Abort
    \${endIf}
    Goto comfyui_payload_done

    comfyui_payload_missing:
      MessageBox MB_ICONSTOP|MB_OK "缺少 ComfyUI-Payload 离线数据包。请先完整解压整个安装 ZIP，再运行根目录中的 Setup.exe。不要单独移动 Setup.exe。"
      Abort

    comfyui_payload_done:
  \${endIf}
!macroend
