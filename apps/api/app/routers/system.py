import ctypes
import json
import os
import re
import shutil
import sqlite3
import subprocess
import platform
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import DATABASE_PATH, STORAGE_DIR, get_db, get_schema_version
from app.models.studio import AIRuntimeSetting
from app.models.task_runtime import TaskExecutionLease, TaskQueueEntry
from app.services.media_service import _bundled_media_path

router = APIRouter(prefix="/api/system", tags=["系统诊断"])


def _check_database():
    if not DATABASE_PATH.is_file():
        return {"status": "error", "exists": False, "message": "数据库文件不存在"}
    try:
        connection = sqlite3.connect(str(DATABASE_PATH), timeout=10)
        try:
            result = connection.execute("PRAGMA quick_check").fetchone()
            tables = connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
            ).fetchone()[0]
        finally:
            connection.close()
        if not result or result[0] != "ok":
            return {"status": "error", "exists": True, "tables": tables,
                    "message": "SQLite 完整性检查未通过"}
        return {"status": "ok", "exists": True, "tables": tables,
                "size_bytes": DATABASE_PATH.stat().st_size,
                "schema_version": get_schema_version(),
                "message": "数据库可读取，完整性检查通过"}
    except Exception as exc:
        return {"status": "error", "exists": True, "message": str(exc)[:500]}


def _check_binary(name):
    try:
        path = _bundled_media_path(name)
        return {"status": "ok", "path": path, "message": "已找到"}
    except Exception as exc:
        return {"status": "error", "path": None, "message": str(exc)[:500]}



def _check_local_ai_tools():
    """Detect optional local AI runtimes without starting processes or using the network."""
    candidates = [
        {
            "id": "ollama",
            "name": "Ollama",
            "purpose": "本地文本模型运行环境",
            "commands": ["ollama"],
            "install_hint": "可从 Ollama 官方网站下载安装；安装和模型许可请自行核实。",
        },
        {
            "id": "llama_cpp",
            "name": "llama.cpp",
            "purpose": "本地 GGUF 文本模型运行环境",
            "commands": ["llama-cli", "main"],
            "install_hint": "可选择官方开源项目的预编译版本；模型文件需单独准备。",
        },
        {
            "id": "whisper_cpp",
            "name": "whisper.cpp",
            "purpose": "本地离线语音识别环境",
            "commands": ["whisper-cli", "main"],
            "install_hint": "可选择官方开源项目的预编译版本；语音模型文件需单独准备。",
        },
    ]
    results = []
    for item in candidates:
        executable = next((shutil.which(command) for command in item["commands"] if shutil.which(command)), None)
        results.append({
            "id": item["id"],
            "name": item["name"],
            "purpose": item["purpose"],
            "status": "installed" if executable else "not_detected",
            "executable": executable,
            "network_used": False,
            "auto_downloaded": False,
            "install_hint": item["install_hint"],
        })
    return {
        "mode": "local_only_detection",
        "network_used": False,
        "auto_downloaded": False,
        "tools": results,
        "note": "仅检查电脑 PATH 中是否存在本地命令，不启动程序、不连接网络、不下载模型。检测到软件不代表已安装模型或可以立即生成内容。",
    }


def _physical_memory_bytes():
    """Return physical RAM in bytes where the OS exposes it, otherwise None."""
    try:
        if os.name == "nt":
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
            status = MEMORYSTATUSEX()
            status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.ullTotalPhys)
        if hasattr(os, "sysconf"):
            pages = os.sysconf("SC_PHYS_PAGES")
            page_size = os.sysconf("SC_PAGE_SIZE")
            if pages > 0 and page_size > 0:
                return int(pages * page_size)
    except (AttributeError, OSError, ValueError):
        return None
    return None


def classify_display_adapter_vendor(name, pnp_device_id=None):
    """Classify a Windows display adapter from its Plug-and-Play vendor ID."""
    pnp = str(pnp_device_id or "").upper()
    label = str(name or "")
    if "VEN_10DE" in pnp or re.search(r"(?i)NVIDIA|GEFORCE|QUADRO", label):
        return "NVIDIA"
    if "VEN_1002" in pnp or re.search(r"(?i)AMD|RADEON", label):
        return "AMD"
    if "VEN_8086" in pnp or re.search(r"(?i)INTEL|ARC GRAPHICS", label):
        return "Intel"
    return "unknown"


def _detect_windows_display_adapter():
    """Read display adapter identity through local WMI; never trust AdapterRAM."""
    if os.name != "nt":
        return None
    powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    if not powershell:
        return None
    script = (
        "$ErrorActionPreference='Stop'; "
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
        "Get-CimInstance -ClassName Win32_VideoController | "
        "Select-Object Name,PNPDeviceID | ConvertTo-Json -Compress"
    )
    try:
        result = subprocess.run(
            [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=5, check=False,
            encoding="utf-8", errors="replace",
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        payload = json.loads(result.stdout)
        adapters = payload if isinstance(payload, list) else [payload]
        for adapter in adapters:
            if not isinstance(adapter, dict):
                continue
            name = str(adapter.get("Name") or "").strip()
            if not name:
                continue
            vendor = classify_display_adapter_vendor(name, adapter.get("PNPDeviceID"))
            if vendor == "unknown":
                continue
            return {
                "status": "detected",
                "vendor": vendor,
                "name": name,
                "vram_bytes": None,
                "acceleration_status": "not_benchmarked",
                "note": "显示适配器身份来自本机 WMI；不使用可能截断的 AdapterRAM，也未声称 GPU 推理已验证。",
            }
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        return None
    return None


def _detect_gpu_profile():
    """Prefer local WMI identity for all vendors, then enrich NVIDIA with nvidia-smi."""
    display = _detect_windows_display_adapter()
    if display and display.get("vendor") == "NVIDIA":
        nvidia = _detect_nvidia_gpu()
        if nvidia.get("status") == "detected":
            return {**display, **nvidia, "acceleration_status": "detected_not_benchmarked"}
        return display
    if display:
        return display
    return _detect_nvidia_gpu()


def _detect_nvidia_gpu():
    """Read NVIDIA GPU metadata from nvidia-smi; never contacts a network service."""
    executable = shutil.which("nvidia-smi")
    if not executable:
        return {
            "status": "not_detected",
            "vendor": None,
            "name": None,
            "vram_bytes": None,
            "note": "未检测到 nvidia-smi。可能没有 NVIDIA 显卡，也可能是驱动或检测工具未安装；不能据此判断没有 GPU。",
        }
    try:
        result = subprocess.run(
            [executable, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3, check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return {
                "status": "unavailable", "vendor": "NVIDIA", "name": None,
                "vram_bytes": None, "note": "找到 nvidia-smi，但无法读取显卡信息；请检查显卡驱动。",
            }
        first = result.stdout.strip().splitlines()[0]
        parts = [part.strip() for part in first.split(",")]
        name = parts[0] if parts else "NVIDIA GPU"
        vram_bytes = int(float(parts[1]) * 1024 * 1024) if len(parts) > 1 else None
        return {
            "status": "detected", "vendor": "NVIDIA", "name": name,
            "vram_bytes": vram_bytes,
            "note": "显卡信息来自本机 nvidia-smi；未访问网络。",
        }
    except (OSError, subprocess.SubprocessError, ValueError):
        return {
            "status": "unavailable", "vendor": "NVIDIA", "name": None,
            "vram_bytes": None, "note": "读取 NVIDIA 显卡信息失败；请检查驱动。",
        }


def _recommend_local_model_tier(ram_bytes, vram_bytes):
    gib = 1024 ** 3
    ram_gib = ram_bytes / gib if ram_bytes else None
    vram_gib = vram_bytes / gib if vram_bytes else None
    if vram_gib is not None and vram_gib >= 12:
        tier = "gpu_7b_14b_quantized"
        label = "优先考虑 7B–14B 量化模型（仍需核对显存占用）"
    elif vram_gib is not None and vram_gib >= 6:
        tier = "gpu_3b_7b_quantized"
        label = "优先考虑 3B–7B 量化模型"
    elif ram_gib is not None and ram_gib >= 16:
        tier = "cpu_3b_7b_quantized"
        label = "可尝试 3B–7B 量化模型，CPU 推理可能较慢"
    elif ram_gib is not None and ram_gib >= 8:
        tier = "cpu_1b_3b_quantized"
        label = "建议从 1B–3B 小型量化模型开始"
    elif ram_gib is not None:
        tier = "very_small_or_not_recommended"
        label = "内存较紧张，建议先使用轻量工具，不建议直接运行大型模型"
    else:
        tier = "unknown"
        label = "无法读取内存信息，暂不推荐模型规格"
    return {
        "tier": tier,
        "label": label,
        "ram_gib": round(ram_gib, 2) if ram_gib is not None else None,
        "vram_gib": round(vram_gib, 2) if vram_gib is not None else None,
        "caution": "这是粗略筛选建议，不是性能保证；实际还取决于模型量化、上下文长度、系统占用和显存共享情况。",
    }


def _get_hardware_profile():
    ram_bytes = _physical_memory_bytes()
    gpu = _detect_gpu_profile()
    try:
        cpu_count = os.cpu_count()
    except Exception:
        cpu_count = None
    return {
        "cpu": {
            "logical_cores": cpu_count,
            "architecture": platform.machine() or None,
            "processor": platform.processor() or None,
        },
        "memory": {
            "total_bytes": ram_bytes,
            "total_gib": round(ram_bytes / (1024 ** 3), 2) if ram_bytes else None,
        },
        "gpu": gpu,
        "model_sizing": _recommend_local_model_tier(ram_bytes, gpu.get("vram_bytes")),
        "network_used": False,
        "note": "仅读取本机 CPU、物理内存和可选 NVIDIA 驱动信息；不进行压力测试、不改变系统设置、不下载模型。",
    }


def _scan_local_model_files():
    """List model-like files directly inside the app-managed models folder; never recurse."""
    model_dir = Path(STORAGE_DIR) / "models"
    supported = {".gguf", ".ggml", ".safetensors", ".onnx", ".pt", ".pth", ".bin", ".model"}
    files = []
    total_bytes = 0
    if model_dir.is_dir():
        try:
            for item in sorted(model_dir.iterdir(), key=lambda path: path.name.lower()):
                if not item.is_file() or item.is_symlink() or item.suffix.lower() not in supported:
                    continue
                try:
                    size_bytes = item.stat().st_size
                except OSError:
                    continue
                total_bytes += size_bytes
                if len(files) < 100:
                    files.append({
                        "name": item.name,
                        "format": item.suffix.lower().lstrip("."),
                        "size_bytes": size_bytes,
                    })
        except OSError:
            return {
                "directory": str(model_dir), "directory_exists": True,
                "status": "unavailable", "count": 0, "total_bytes": 0,
                "files": [], "note": "无法读取模型目录，请检查文件夹权限。",
            }
    return {
        "directory": str(model_dir),
        "directory_exists": model_dir.is_dir(),
        "status": "found" if total_bytes else "empty",
        "count": len(files),
        "total_bytes": total_bytes,
        "truncated": total_bytes > 0 and len(files) >= 100,
        "files": files,
        "note": "仅列出应用模型目录第一层的常见模型文件；文件存在不代表模型可运行、来源可信或许可证允许商用。",
    }

def _health_status(checks):
    values = [item.get("status") for item in checks]
    if "error" in values:
        return "error"
    if "warning" in values:
        return "warning"
    return "ok"


@router.get("/diagnostics")
def system_diagnostics(db: Session = Depends(get_db)):
    database = _check_database()
    ffmpeg = _check_binary("ffmpeg.exe")
    ffprobe = _check_binary("ffprobe.exe")
    try:
        usage = shutil.disk_usage(STORAGE_DIR)
        disk = {
            "status": "warning" if usage.free < 5 * 1024 ** 3 else "ok",
            "free_bytes": usage.free,
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "message": "剩余空间低于 5 GB，请清理磁盘或更换素材存储位置"
            if usage.free < 5 * 1024 ** 3 else "磁盘空间充足",
        }
    except Exception as exc:
        disk = {"status": "error", "message": str(exc)[:500]}

    runtime = db.query(AIRuntimeSetting).first()
    active_leases = db.query(TaskExecutionLease).filter(
        TaskExecutionLease.status == "active"
    ).count()
    queued_tasks = db.query(TaskQueueEntry).filter(
        TaskQueueEntry.status.in_(["waiting", "ready_to_run"])
    ).count()

    checks = [database, ffmpeg, ffprobe, disk]
    warnings = [
        item["message"] for item in checks
        if item.get("status") in ("warning", "error")
    ]
    return {
        "status": _health_status(checks),
        "application": "AI漫剧制作大师",
        "platform": platform.platform(),
        "python": platform.python_version(),
        "database": {**database, "path": str(DATABASE_PATH)},
        "storage": {"path": str(STORAGE_DIR), "disk": disk},
        "media_tools": {"ffmpeg": ffmpeg, "ffprobe": ffprobe},
        "local_ai": _check_local_ai_tools(),
        "hardware": _get_hardware_profile(),
        "local_models": _scan_local_model_files(),
        "ai_permissions": {
            "network_enabled": bool(runtime and runtime.network_enabled),
            "paid_api_allowed": bool(runtime and runtime.paid_api_allowed),
            "note": "这里只显示开关状态，不读取或返回 API Key。",
        },
        "tasks": {"active_leases": active_leases, "queued_tasks": queued_tasks},
        "warnings": warnings,
        "checked_components": len(checks),
    }
