Unicode true
!macro customInstall
  IfFileExists "$EXEDIR\ComfyUI-Payload\install-comfyui-offline.ps1" 0 comfyui_payload_missing

  DetailPrint "正在检测显卡并安装匹配的 ComfyUI 本地引擎..."
  ExecWait '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$EXEDIR\ComfyUI-Payload\install-comfyui-offline.ps1" -PayloadRoot "$EXEDIR\ComfyUI-Payload" -InstallRoot "$PROFILE\AIComicStudio\ComfyUI" -LogPath "$TEMP\AIComicStudio-ComfyUI-install.log"' $0

  IntCmp $0 0 comfyui_install_success comfyui_install_failed comfyui_install_failed

  comfyui_install_failed:
    MessageBox MB_ICONSTOP|MB_OK "ComfyUI 本地引擎部署失败（错误代码：$0）。安装已停止。日志位置：$TEMP\AIComicStudio-ComfyUI-install.log。请根据日志排查后重试。"
    Abort

  comfyui_install_success:
  Goto comfyui_payload_done

  comfyui_payload_missing:
    MessageBox MB_ICONSTOP|MB_OK "缺少 ComfyUI-Payload 离线数据包。请先完整解压整个安装 ZIP，再运行根目录中的 Setup.exe。不要单独移动 Setup.exe。"
    Abort

  comfyui_payload_done:

  IfFileExists "$EXEDIR\LocalAI-Payload\install-local-ai-offline.ps1" 0 localai_payload_missing
  DetailPrint "正在部署内置 Ollama 本地运行环境和 Qwen3 4B 模型..."
  ExecWait '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$EXEDIR\LocalAI-Payload\install-local-ai-offline.ps1" -PayloadRoot "$EXEDIR\LocalAI-Payload" -InstallRoot "$PROFILE\AIComicStudio\Ollama" -LogPath "$TEMP\AIComicStudio-Ollama-install.log"' $0
  IntCmp $0 0 localai_install_success localai_install_failed localai_install_failed

  localai_install_failed:
    MessageBox MB_ICONSTOP|MB_OK "本地 AI 运行环境或 Qwen3 4B 模型部署失败（错误代码：$0）。安装已停止。请检查磁盘空间和日志：$TEMP\AIComicStudio-Ollama-install.log。"
    Abort

  localai_install_success:
    Goto localai_payload_done

  localai_payload_missing:
    MessageBox MB_ICONSTOP|MB_OK "缺少 LocalAI-Payload 离线数据包。请完整解压整个安装 ZIP，再运行根目录中的 Setup.exe。不要单独移动 Setup.exe。"
    Abort

  localai_payload_done:
!macroend
