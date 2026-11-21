# Windows 完整离线安装套件制作

本流程从项目源码制作一个完整的 Windows x64 离线 ZIP。套件包含桌面端 Setup.exe、四种 ComfyUI 官方 Portable 运行包、Ollama Windows x64 本地运行环境、Qwen3 4B 文本模型、安装脚本、SHA-256 清单和第三方许可证资料。

安装目标机不需要联网下载 ComfyUI、Ollama 或 Qwen3 4B。ComfyUI 图片生成检查点仍是独立可选资源，不包含在本套件中。

## 构建环境和空间要求

- Windows 10/11 x64。
- 在项目源码根目录执行。
- 已按项目开发说明安装 Node.js、Python、项目依赖和 API 虚拟环境。
- scripts/build-windows.ps1 所需的 FFmpeg/ffprobe 已放在项目指定目录或已加入 PATH。
- 本地 AI 载荷构建临时盘至少预留 12 GiB。
- 如果临时盘和 ZIP 输出盘是同一磁盘，开始前建议至少预留 18 GiB 可用空间；若分属不同磁盘，临时盘至少 12 GiB、输出盘至少 13 GiB。安装目标盘还需要额外空间，安装器会分别检查 ComfyUI 和本地 AI 的实际空间需求。
- 网络需要能够访问 GitHub、Ollama 官方模型库及许可证来源。完整资源约 11 GiB，下载时间取决于网络速度。

## 一键制作

在项目根目录打开 PowerShell，执行：

~~~powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\create-comfyui-offline-installer.ps1
~~~

脚本依次执行：

1. 构建当前源码对应的 Windows 桌面安装程序。
2. 下载并验证 Ollama 官方 Windows x64 运行包，使用隔离的临时服务下载固定模型 qwen3:4b，核验模型 manifest、每个模型 blob 的大小和 SHA-256。
3. 下载 ComfyUI 四种官方 Portable 资产和 7-Zip 工具/源代码，校验官方 SHA-256。
4. 将 Setup.exe、ComfyUI-Payload、LocalAI-Payload、许可证资料和清单写入一个完整 ZIP。
5. 重新打开 ZIP，检查必需条目、文件长度和 SHA-256，再生成 ZIP 自身的 .sha256 文件。

预期输出：

~~~text
apps\desktop\release\AI漫剧制作大师-完整离线安装套件.zip
apps\desktop\release\AI漫剧制作大师-完整离线安装套件.zip.sha256
~~~

构建过程只准备离线安装载荷，不会在构建电脑上安装 ComfyUI 或 Ollama。模型下载仅发生在用户主动执行完整套件构建脚本时。

## 使用已有 Setup.exe

如果已有本次源码构建的桌面安装程序，可以跳过桌面构建：

~~~powershell
.\scripts\create-comfyui-offline-installer.ps1 -SetupExe ".\apps\desktop\release\AI漫剧制作大师-Setup-1.0.19.exe"
~~~

将路径替换为实际的 Setup.exe 路径。

## 重新生成

如果目标 ZIP 已存在，脚本默认拒绝覆盖。确认要重新制作时：

~~~powershell
.\scripts\create-comfyui-offline-installer.ps1 -Force
~~~

## 安装

1. 将完整 ZIP 复制到目标 Windows x64 电脑。
2. 解压整个 ZIP，不要只提取或移动 Setup.exe。
3. 双击根目录中的 Setup.exe。
4. 安装器检测 GPU，只部署匹配的一个 ComfyUI Windows x64 运行包。对未确认支持 GPU 加速的集成显卡，使用 CPU 模式，不会冒充 GPU 加速。
5. 安装器同时部署 Ollama 本地运行环境和 Qwen3 4B 文本模型。模型存储在用户目录 AIComicStudio\Ollama\models。
6. 桌面软件启动时，会启动内置 Ollama 服务并通过本机回环地址 127.0.0.1:11435 使用本地模型；设置 OLLAMA_NO_CLOUD=1，关闭 Ollama 云端功能，不会将小说、图片或项目内容自动发送到云端。运行参数限制为单并发、单模型驻留，降低内存占用。Qwen3 4B 建议至少 8 GiB 内存，推荐 16 GiB；低于最低要求时安装器会发出警告，不会隐瞒硬件限制。
7. ComfyUI 图片生成检查点是独立资源，后续下载前必须显示来源、大小、磁盘空间并由用户明确确认。

### GPU 适配说明

NVIDIA GTX 10 系列及更旧型号选择 cu126 运行包，其余 NVIDIA 型号选择标准运行包。官方已核实支持的 AMD RX 6000/7000/9000、Ryzen AI Max 和 Radeon AI PRO R9700 系列使用 AMD GPU 模式；其他 AMD 型号（包括 Ryzen 7 4800U 的 Radeon Vega 集成显卡）采用 CPU 模式。Intel Arc 使用 Intel GPU 运行包，其他 Intel 集成显卡采用 CPU 模式。CPU 模式速度会明显较慢。

## 第三方依赖审查与分发门禁

仓库中的 docs/compliance/comfyui-portable-dependency-audit.json 是统一审查登记表。当前状态仍为 pending，distribution_approved 必须保持 false。因此本套件只能作为内部安装和兼容性验证包，不是可公开或客户分发的正式安装包。

审查范围包括：

- ComfyUI NVIDIA、NVIDIA cu126、AMD、Intel 四种 Portable 变体，分别清点嵌入式 Python、Python 包、PyTorch、CUDA/ROCm/XPU 运行库、原生 DLL、许可证、NOTICE 和源代码义务。
- 7-Zip 精确版本、可执行文件、源代码包及其许可证义务。
- Ollama Windows x64 standalone 运行包及其传递依赖。
- Qwen3 4B 的确切模型 manifest、每个 blob 的 SHA-256、模型许可证和归属声明。
- 任何未知或缺失的许可信息均保持 pending，不得仅凭元数据推断再分发许可。

ComfyUI 依赖清点工具为 scripts/audit-comfyui-portable-dependencies.ps1。将四种官方 7z 归档放在同一目录后，可运行：

~~~powershell
$archives = @(
  ".\ComfyUI_windows_portable_nvidia.7z",
  ".\ComfyUI_windows_portable_nvidia_cu126.7z",
  ".\ComfyUI_windows_portable_amd.7z",
  ".\ComfyUI_windows_portable_intel.7z"
)
.\scripts\audit-comfyui-portable-dependencies.ps1 -SevenZipPath ".\apps\desktop\release\ComfyUI-Payload\7zr.exe" -ArchivePaths $archives -ReleaseTag "填写实际官方 Release tag" -OutputPath ".\apps\desktop\release\comfyui-dependency-inventory.json"
~~~

Ollama 和 Qwen3 的自动清点结果随 LocalAI-Payload\manifest.json 保存。自动清点和哈希核验不等于法律审核；四种 ComfyUI 变体、Ollama 传递依赖及模型许可都必须经过人工复核后，才能考虑解除分发门禁。

## 重要发布限制

- 完整 ZIP 约 11 GiB，不能作为单个 GitHub Release 附件上传。必须先确定获批且不产生未经授权费用的分发渠道。
- ComfyUI、7-Zip、Ollama 和 Qwen3 的许可证资料随包提供，但这不代表全部依赖审核已经完成。
- 模型权重许可与软件依赖许可分别审核。
- 必须在真实 NVIDIA、AMD、Intel 目标设备上完成安装、启动、停止、健康检查及图片生成验收后，才能宣称正式版全面支持这些硬件。
- 未完成逐项许可证审核、Windows 实机安装/升级/卸载/数据恢复验证和最终 SHA-256 验收前，不得发布正式下载。
