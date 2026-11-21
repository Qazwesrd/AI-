# Windows 完整离线安装套件制作

本流程用于从项目源码制作一个完整 ZIP。ZIP 内包含桌面端 \`Setup.exe\`、四种 ComfyUI 官方 Windows x64 运行包、离线安装脚本、SHA-256 清单和第三方许可证资料。安装时只部署与当前显卡匹配的一种运行包；不会在安装后联网下载 ComfyUI，也不会自动下载图片生成模型。

## 运行环境

- Windows 10/11 x64。
- 在项目源码根目录执行。
- 已按项目开发说明安装 Node.js、Python、项目依赖和 API 虚拟环境。
- \`scripts/build-windows.ps1\` 所需的 FFmpeg/ffprobe 已放在项目指定目录或已加入 PATH。
- 输出磁盘至少预留 10 GiB；实际会在下载前按官方资产大小再次检查可用空间。
- 网络可以访问 GitHub 官方发布源。资源下载量约 7.3 GiB，耗时取决于网络速度。

## 一键制作

在项目根目录打开 PowerShell，执行：

\`\`\`powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\create-comfyui-offline-installer.ps1
\`\`\`

脚本会先运行项目的 Windows 正式安装程序构建流程，再从 ComfyUI 与 7-Zip 官方 Release 获取资产，校验官方 SHA-256，最后生成：

\`\`\`text
apps\desktop\release\AI漫剧制作大师-完整离线安装套件.zip
apps\desktop\release\AI漫剧制作大师-完整离线安装套件.zip.sha256
\`\`\`

脚本只制作安装套件，不会在运行脚本的电脑上安装 ComfyUI，也不会下载图像模型。

## 使用已有 Setup.exe

如果已经有本次源码构建的桌面安装程序，可以跳过本地构建：

\`\`\`powershell
.\scripts\create-comfyui-offline-installer.ps1 -SetupExe ".\apps\desktop\release\AI漫剧制作大师-Setup-1.0.19.exe"
\`\`\`

将路径替换为实际的 Setup.exe 路径。

## 重新生成

如果目标 ZIP 已存在，脚本默认拒绝覆盖。确认要重新制作时：

\`\`\`powershell
.\scripts\create-comfyui-offline-installer.ps1 -Force
\`\`\`

## 安装

1. 将完整 ZIP 复制到目标 Windows x64 电脑。
2. 解压整个 ZIP，不要只提取或移动 \`Setup.exe\`。
3. 双击根目录的 \`Setup.exe\`。
4. 安装器检测 GPU，只解压并部署匹配的官方 ComfyUI 运行包。
5. 图片生成模型是独立资源，后续必须显示下载来源、大小和磁盘空间，并由用户明确确认。

自动适配规则：NVIDIA GTX 10 系列及更旧型号选择 cu126 运行包，其余 NVIDIA 型号选择标准运行包；AMD 仅对已核实的 RX 6000/7000/9000、Ryzen AI Max、Radeon AI PRO R9700 系列自动选择；Intel 仅对 Arc 自动选择。未识别的 AMD/Intel 型号或其他未知显卡会停止自动安装，不会猜测，也不会联网回退。

## 重要发布限制

- 该 ZIP 体积约 7.3 GiB，不能作为单个 GitHub Release 附件上传（GitHub 单附件有大小限制）。请使用获批的可分发渠道；不要将 7GB 文件上传到有额外计费或公开风险的云盘。
- ComfyUI 为 GPL-3.0。当前 ZIP 包含 ComfyUI 与 7-Zip 的许可证/源代码资料，但这不等于所有 Portable 依赖的完整许可证审查已完成。公开正式分发前必须审计所有随包二进制依赖及其许可证义务。
- 当前 GPU 自动选择范围采取保守策略。未识别的型号会停止，不代表该显卡一定不能通过其他方式运行 ComfyUI。
- 必须在真实 NVIDIA、AMD、Intel 目标设备上完成安装、启动、停止、健康检查及图片生成验收后，才能宣称正式版全面支持这些硬件。
