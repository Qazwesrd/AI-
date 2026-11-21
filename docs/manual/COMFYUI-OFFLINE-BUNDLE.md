# Windows 完整离线安装套件制作

本流程用于从项目源码制作一个完整 ZIP。ZIP 内包含桌面端 \`Setup.exe\`、四种 ComfyUI 官方 Windows x64 运行包、离线安装脚本、SHA-256 清单和第三方许可证资料。安装时只部署与当前显卡匹配的一种运行包；不会在安装后联网下载 ComfyUI，也不会自动下载图片生成模型。

## 运行环境

- Windows 10/11 x64。
- 在项目源码根目录执行。
- 已按项目开发说明安装 Node.js、Python、项目依赖和 API 虚拟环境。
- \`scripts/build-windows.ps1\` 所需的 FFmpeg/ffprobe 已放在项目指定目录或已加入 PATH。
- 构建临时盘至少预留 12 GiB，输出盘建议至少预留 15 GiB；脚本会按实际官方资产大小再次检查空间。
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
6. 图片生成检查点是独立资源，后续必须显示下载来源、大小和磁盘空间，并由用户明确确认。

自动适配规则：NVIDIA GTX 10 系列及更旧型号选择 cu126 运行包，其余 NVIDIA 型号选择标准运行包；AMD 已核实的 RX 6000/7000/9000、Ryzen AI Max、Radeon AI PRO R9700 系列使用 GPU 模式；其他 AMD 型号（包括 Ryzen 7 4800U 的 Radeon Vega 集成显卡）选择官方 AMD 便携包，但明确以 CPU 模式运行，不会冒充 GPU 加速；Intel Arc 使用 GPU 模式，其他 Intel 集成显卡使用 CPU 模式。CPU 模式使用官方命令参数 `--cpu`，生成速度会明显较慢。

## 第三方依赖审查与分发门禁

仓库中的 `docs/compliance/comfyui-portable-dependency-audit.json` 是随版本维护的审查登记表。当前状态为 `pending`，四种官方 Portable 变体的逐项清点均未完成，`distribution_approved` 必须保持 `false`。

离线 ZIP 构建脚本会验证登记表存在且状态符合当前内部构建策略，并将其原样放入 `THIRD_PARTY_NOTICES/comfyui-portable-dependency-audit.json`。生成的 manifest 明确标记 `distribution_approved: false` 和 `blocked-pending-third-party-dependency-audit`。这只是内部安装/兼容性验证包，不是可对外分发的正式安装包。

审查时必须以实际下载的 Release tag 和每个官方资产 SHA-256 为基准，逐个清点 NVIDIA、NVIDIA cu126、AMD、Intel 包中的文件。重点包括嵌入式 Python 发行版、Python `*.dist-info/METADATA` 与许可证文件、PyTorch/torchvision/torchaudio、CUDA/ROCm/XPU 运行库、原生 DLL，以及 ComfyUI 本身。不同 GPU 变体的依赖可能不同，不能用一个变体的清单代替其他变体。对每个组件记录名称、版本、来源、许可证、版权/NOTICE、源代码提供义务、再分发限制和审核证据；未知或缺失信息保持 pending，不得猜测许可证。

### 自动清点工具

仓库提供 `scripts/audit-comfyui-portable-dependencies.ps1`，完整离线 ZIP 也会在 `ComfyUI-Payload/audit-comfyui-portable-dependencies.ps1` 中携带该工具。将四种官方 7z 归档放在同一目录，在 PowerShell 中从项目根目录执行：

```powershell
$archives = @(
  ".\ComfyUI_windows_portable_nvidia.7z",
  ".\ComfyUI_windows_portable_nvidia_cu126.7z",
  ".\ComfyUI_windows_portable_amd.7z",
  ".\ComfyUI_windows_portable_intel.7z"
)
.\scripts\audit-comfyui-portable-dependencies.ps1 `
  -SevenZipPath ".\apps\desktop\release\ComfyUI-Payload\7zr.exe" `
  -ArchivePaths $archives `
  -ReleaseTag "填写实际官方 Release tag" `
  -OutputPath ".\apps\desktop\release\comfyui-dependency-inventory.json"
```

工具会记录每个归档的 SHA-256、文件清单、Python 包元数据、许可证文件线索及 PyTorch/CUDA/ROCm/XPU 等运行库候选项。为避免异常归档造成资源耗尽，工具限制归档条目数、证据文件数、单文件提取大小和每个变体的证据提取总量；临时提取目录会在结束时清理。报告中的许可证状态始终为 pending，跳过或无法识别的内容仍需人工补查。建议每次只对同一个官方 Release tag 的四种资产运行，并保留原始归档和报告供复核。

只有四个变体都完成逐项清点、所有义务有对应材料、并经过人工复核后，才可以更新审查登记表的状态。仅修改 JSON 的状态字段不构成法律审核或自动批准；发布负责人仍须逐项核验并明确签署。模型权重许可另行审查，不能由软件依赖审查代替。

## 重要发布限制

- 该 ZIP 体积约 7.3 GiB，不能作为单个 GitHub Release 附件上传（GitHub 单附件有大小限制）。请使用获批的可分发渠道；不要将 7GB 文件上传到有额外计费或公开风险的云盘。
- ComfyUI 为 GPL-3.0。当前 ZIP 包含 ComfyUI 与 7-Zip 的许可证/源代码资料，但这不等于所有 Portable 依赖的完整许可证审查已完成。公开正式分发前必须审计所有随包二进制依赖及其许可证义务。
- GPU 加速自动选择范围采取保守策略。未确认支持的 AMD/Intel 集成显卡会降级为 CPU 模式；未知厂商或无法识别的设备仍会停止自动安装。
- 必须在真实 NVIDIA、AMD、Intel 目标设备上完成安装、启动、停止、健康检查及图片生成验收后，才能宣称正式版全面支持这些硬件。
