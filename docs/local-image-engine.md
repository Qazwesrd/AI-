# 本地图片生成引擎（AMD Ryzen 7 4800U）部署说明

## 当前硬件判断

用户当前设备检测结果：
- CPU：AMD Ryzen 7 4800U with Radeon Graphics
- GPU：AMD Radeon(TM) Graphics，Vega 集成显卡
- 内存：约 15.37 GB
- WMI 报告的 AdapterRAM：512 MB（集成显卡共享系统内存，该字段不代表可用图形内存总量）

这台机器不应默认使用 ROCm / ComfyUI Windows ROCm，也不应下载 SDXL、Flux 等大型模型。先验证 Vulkan 驱动和稳定性。

## 候选引擎

候选：[Local-AI-Image-Generator](https://github.com/Adam-s-tech/Local-AI-Image-Generator)

其 README 描述 Windows AMD 使用 stable-diffusion.cpp Vulkan 后端；集成显卡或未检测到可用 GPU 时会回退 CPU，CPU 生成可能很慢。该项目是独立第三方项目，不是本产品的依赖，也没有经过本机实测。

## 分阶段启用原则

1. 运行 scripts/check-local-image-engine.ps1，只做硬件、驱动、Vulkan Loader / ICD 预检。
2. 如果系统有可用 Vulkan ICD，再下载候选引擎的 Windows 源码/发行包，先核对文件来源和版本。
3. 首次只测试 SD 1.5 级别的轻量模型，512×512、低步数、单张生成；不要一开始下载 SDXL / Flux。
4. 只有实际确认 Vulkan 后端被引擎识别并成功生成，才把该引擎接入桌面软件。
5. 若只能 CPU 回退，默认关闭自动生成，提示用户性能限制；不得静默切换云端 API，也不得产生费用。

## 产品集成目标

最终由 AI漫剧制作大师统一管理本地引擎：
- 设置页：本地图片引擎启用开关、引擎路径、连接状态、模型路径。
- 人物 / 场景 / 道具生成：沿用现有素材任务，提交正向与反向提示词、尺寸、种子等参数。
- 输出：生成图片回传素材库，保留模型名、提示词、尺寸、种子和生成时间等元数据。
- 网络策略：本地推理不依赖外部图片 API；不得自动切换到付费云端服务。
- 资源保护：单张生成、队列并发默认为 1；提供取消任务和显存/内存不足错误提示。

## 本机检测

在 PowerShell 运行：

~~~powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
& "scripts/check-local-image-engine.ps1"
~~~

脚本只读取系统信息，不修改系统、不安装驱动、不下载模型。
