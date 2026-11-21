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
4. 安装器检测 GPU，只解压并部署匹配的官方 ComfyUI 运行包；对未确认支持 GPU 加速的集成显卡，使用官方便携包的 CPU 模式，并生成独立的 CPU 启动脚本。
5. 图片生成模型是独立资源，后续必须显示下载来源、大小和磁盘空间，并由用户明确确认。

自动适配规则：NVIDIA GTX 10 系列及更旧型号选择 cu126 运行包，其余 NVIDIA 型号选择标准运行包；AMD 已核实的 RX 6000/7000/9000、Ryzen AI Max、Radeon AI PRO R9700 系列使用 GPU 模式；其他 AMD 型号（包括 Ryzen 7 4800U 的 Radeon Vega 集成显卡）选择官方 AMD 便携包，但明确以 CPU 模式运行，不会冒充 GPU 加速；Intel Arc 使用 GPU 模式，其他 Intel 集成显卡使用 CPU 模式。CPU 模式使用官方命令参数 `--cpu`，生成速度会明显较慢。

## 重要发布限制

- 该 ZIP 体积约 7.3 GiB，不能作为单个 GitHub Release 附件上传（GitHub 单附件有大小限制）。请使用获批的可分发渠道；不要将 7GB 文件上传到有额外计费或公开风险的云盘。
- ComfyUI 为 GPL-3.0。当前 ZIP 包含 ComfyUI 与 7-Zip 的许可证/源代码资料，但这不等于所有 Portable 依赖的完整许可证审查已完成。公开正式分发前必须审计所有随包二进制依赖及其许可证义务。
- GPU 加速自动选择范围采取保守策略。未确认支持的 AMD/Intel 集成显卡会降级为 CPU 模式；未知厂商或无法识别的设备仍会停止自动安装。
- 必须在真实 NVIDIA、AMD、Intel 目标设备上完成安装、启动、停止、健康检查及图片生成验收后，才能宣称正式版全面支持这些硬件。


## 构建失败保护与交付校验

- 下载或打包过程中任一官方资源长度、SHA-256、许可证、ZIP 目录或条目校验失败，构建会中止。
- ZIP 写入后的目录/条目完整性检查失败时，会先关闭 ZIP 文件句柄，再删除不完整 ZIP 和旧的校验文件，避免留下看似可交付的残缺文件。
- 最终 ZIP 大小必须不小于四种 ComfyUI 官方运行包、7-Zip 工具、7-Zip 源码包及 Setup.exe 的实际字节总和；该判断不把磁盘临时空间预留值误算为 ZIP 内容。
- SHA-256 校验文件写入后会回读，并要求哈希与 ZIP 文件名完全匹配；失败时同时清理 ZIP 与校验文件。
- 以上属于构建脚本保护措施。必须由 Windows 构建任务实际执行并生成完整 ZIP 后，才能确认大文件打包链路通过。
