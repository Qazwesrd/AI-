Unicode true
!macro customInstall
  IfFileExists "$EXEDIR\ComfyUI-Payload\install-comfyui-offline.ps1" 0 comfyui_payload_missing
  IfFileExists "$EXEDIR\LocalAI-Payload\manifest.json" 0 localai_payload_missing
  IfFileExists "$EXEDIR\ComfyUI-Payload\install-local-engines-transaction.ps1" 0 transaction_script_missing

  DetailPrint "正在预检并部署 ComfyUI、Ollama 和 Qwen3 4B 本地引擎..."
  ; Keep large AI runtimes and models on the same drive selected on the installer directory page.
  ; AIComicStudio-Data is intentionally outside electron-builder's file manifest so uninstall preserves projects and models.
  CreateDirectory "$INSTDIR\AIComicStudio-Data\engines"
  ExecWait '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$EXEDIR\ComfyUI-Payload\install-local-engines-transaction.ps1" -ComfyPayloadRoot "$EXEDIR\ComfyUI-Payload" -LocalAIPayloadRoot "$EXEDIR\LocalAI-Payload" -ComfyInstallRoot "$INSTDIR\AIComicStudio-Data\engines\ComfyUI" -LocalAIInstallRoot "$INSTDIR\AIComicStudio-Data\engines\Ollama" -LogDirectory "$TEMP\AIComicStudio-install"' $0
  IntCmp $0 0 engines_install_success engines_install_failed engines_install_failed

  engines_install_failed:
    MessageBox MB_ICONSTOP|MB_OK "本地 AI 引擎整套部署失败（错误代码：$0）。安装事务已尝试回滚本次新建的引擎；安装前已有的数据不会被删除。请检查日志：$TEMP\AIComicStudio-install。"
    Abort

  engines_install_success:
    Goto engines_payload_done

  comfyui_payload_missing:
    MessageBox MB_ICONSTOP|MB_OK "缺少 ComfyUI-Payload 离线数据包。请完整解压整个安装 ZIP，再运行根目录中的 Setup.exe。"
    Abort

  localai_payload_missing:
    MessageBox MB_ICONSTOP|MB_OK "缺少 LocalAI-Payload 离线数据包。请完整解压整个安装 ZIP，再运行根目录中的 Setup.exe。"
    Abort

  transaction_script_missing:
    MessageBox MB_ICONSTOP|MB_OK "缺少本地引擎事务安装脚本。请重新下载完整安装套件，不要单独移动 Setup.exe。"
    Abort

  engines_payload_done:
!macroend
