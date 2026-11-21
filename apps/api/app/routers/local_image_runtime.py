"""Local ComfyUI runtime discovery and process lifecycle management.

Only the fixed per-user installation is launchable. The API never downloads
models, invokes a shell, contacts a cloud provider, or terminates arbitrary
processes. Stop is restricted to the exact Popen handle created in this process.
"""
import json
import os
import subprocess
import threading
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from fastapi import APIRouter, HTTPException

from app.routers.system import _get_hardware_profile
from app.routers.comfyui_installer import (
    detect_windows_video_adapters,
    recommend_runtime_mode,
)

router = APIRouter(prefix="/api/local-ai", tags=["本地图片生成"])
COMFYUI_BASE_URL = "http://127.0.0.1:8188"
_OPENER = build_opener(ProxyHandler({}))
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_LOG_BYTES = 128 * 1024
GIB = 1024 ** 3
_PROCESS_LOCK = threading.Lock()
_COMFYUI_PROCESS = None
_COMFYUI_LOG_HANDLE = None
_COMFYUI_RUNTIME_MODE = None
_COMFYUI_STARTED_AT = None


def _install_root() -> Path:
    # This is the only supported installation location. Never accept a path
    # from an HTTP request or construct a command from user-provided input.
    return Path.home() / "AIComicStudio" / "ComfyUI"


def _log_path() -> Path:
    data_root = Path(os.environ.get("APP_DATA_DIR") or (Path.home() / "AIComicStudio" / "data"))
    return data_root / "logs" / "comfyui-runtime.log"


def _local_json(path: str, opener=None, timeout: float = 2.5):
    if path not in {"/system_stats", "/object_info/CheckpointLoaderSimple"}:
        raise ValueError("Unsupported ComfyUI endpoint")
    request = Request(COMFYUI_BASE_URL + path, headers={"Accept": "application/json"})
    client = opener or _OPENER
    try:
        with client.open(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError("本地 ComfyUI 响应超过安全读取上限")
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("本地 ComfyUI 返回格式无效")
        return payload
    except HTTPError as exc:
        return {"_error": f"HTTP {exc.code}"}
    except (URLError, TimeoutError, OSError):
        return None
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"_error": "本地 ComfyUI 返回了无法识别的数据"}


def _checkpoint_names(object_info):
    if not isinstance(object_info, dict) or object_info.get("_error"):
        return []
    node = object_info.get("CheckpointLoaderSimple")
    if not isinstance(node, dict):
        return []
    inputs = node.get("input")
    required = inputs.get("required") if isinstance(inputs, dict) else None
    checkpoint = required.get("ckpt_name") if isinstance(required, dict) else None
    if isinstance(checkpoint, list) and checkpoint and isinstance(checkpoint[0], list):
        return [str(name)[:240] for name in checkpoint[0][:200] if isinstance(name, str)]
    return []


def assess_image_hardware(hardware: dict):
    memory = hardware.get("memory") or {}
    gpu = hardware.get("gpu") or {}
    ram = memory.get("total_bytes")
    vram = gpu.get("vram_bytes")
    reasons = []
    if ram is None:
        reasons.append("无法读取系统内存，建议先人工确认内存容量。")
    elif ram < 16 * GIB:
        reasons.append("系统内存低于 16 GiB，大型图片工作流可能出现内存不足。")
    else:
        reasons.append("系统内存达到 16 GiB 基础建议；复杂工作流仍可能需要更多内存。")

    if vram is None:
        level = "unknown"
        reasons.append("未读取到 NVIDIA 显存信息；可能使用其他品牌显卡或 CPU，不能据此判断没有 GPU。")
    elif vram < 4 * GIB:
        level = "limited"
        reasons.append("显存低于 4 GiB，现代扩散模型可能无法顺畅运行；建议先使用轻量模型并降低分辨率。")
    elif vram < 8 * GIB:
        level = "basic"
        reasons.append("显存为 4–8 GiB，建议从轻量/低显存工作流开始，SDXL 等大型工作流可能受限。")
    elif vram < 12 * GIB:
        level = "capable"
        reasons.append("显存为 8–12 GiB，可尝试更多常见工作流，但模型、分辨率和批量大小仍会影响结果。")
    else:
        level = "strong"
        reasons.append("显存达到 12 GiB 以上，具备尝试较复杂工作流的条件；仍需实际测试。")

    return {
        "ram_bytes": ram,
        "vram_bytes": vram,
        "gpu_level": level,
        "reasons": reasons,
        "note": "以上是保守的初步建议，不是硬件兼容保证。实际速度和可运行模型取决于显卡类型、驱动、量化方式、模型架构、分辨率和工作流。",
    }


def _build_runtime_command(python_exe: Path, main_py: Path, mode: str):
    command = [str(python_exe), "-s", str(main_py)]
    if mode == "cpu":
        command.append("--cpu")
    command.append("--windows-standalone-build")
    return command


def _runtime_mode():
    detected = detect_windows_video_adapters()
    adapters = detected.get("adapters") if isinstance(detected, dict) else []
    if not isinstance(adapters, list):
        adapters = []
    # Match the installer policy: only explicitly recognized device families
    # use acceleration. Unknown and older integrated GPUs stay on CPU.
    for adapter in adapters:
        if not isinstance(adapter, dict):
            continue
        vendor = str(adapter.get("vendor") or "")
        name = str(adapter.get("name") or "")
        if vendor in {"NVIDIA", "AMD", "Intel"}:
            return recommend_runtime_mode(vendor, name), name
    return "cpu", "未能确认受支持的 GPU，安全使用 CPU 模式"


def _managed_process_state():
    process = _COMFYUI_PROCESS
    if process is None:
        return {"managed": False, "process_id": None, "process_alive": False}
    alive = process.poll() is None
    return {
        "managed": alive,
        "process_id": process.pid if alive else None,
        "process_alive": alive,
    }


def _close_log_handle():
    global _COMFYUI_LOG_HANDLE
    handle = _COMFYUI_LOG_HANDLE
    _COMFYUI_LOG_HANDLE = None
    if handle:
        try:
            handle.flush()
            handle.close()
        except OSError:
            pass


def _status_payload():
    stats = _local_json("/system_stats")
    managed = _managed_process_state()
    root = _install_root()
    python_exe = root / "python_embeded" / "python.exe"
    main_py = root / "ComfyUI" / "main.py"
    installed = python_exe.is_file() and main_py.is_file()
    common = {
        "engine": "comfyui",
        "base_url": COMFYUI_BASE_URL,
        "installed": installed,
        "install_directory": str(root),
        "log_path": str(_log_path()),
        "managed_by_application": managed["managed"],
        "process_id": managed["process_id"],
        "runtime_mode": _COMFYUI_RUNTIME_MODE if managed["managed"] else None,
        "started_at": _COMFYUI_STARTED_AT if managed["managed"] else None,
        "network_used": False,
        "downloaded": False,
        "cloud_fallback": False,
        "hardware": assess_image_hardware(_get_hardware_profile()),
    }
    if stats and not stats.get("_error"):
        object_info = _local_json("/object_info/CheckpointLoaderSimple")
        checkpoints = _checkpoint_names(object_info)
        system = stats.get("system")
        devices = system.get("devices") if isinstance(system, dict) and isinstance(system.get("devices"), list) else []
        return {
            **common,
            "status": "ready",
            "checkpoint_count": len(checkpoints),
            "checkpoints": checkpoints,
            "device_count": len(devices),
            "message": "已连接本机 ComfyUI。这里只检查服务和已安装模型，不会提交生成任务或下载模型。",
            "limitations": "检测成功不代表已安装可用模型或已完成图片生成。",
        }
    if stats and stats.get("_error"):
        status = "error"
        message = "本机 ComfyUI 返回异常：" + str(stats["_error"])
    elif managed["process_alive"]:
        status = "starting"
        message = "ComfyUI 进程已启动，正在等待本机健康检查通过。"
    elif installed:
        status = "stopped"
        message = "已找到本地 ComfyUI 引擎，但当前没有运行。"
    else:
        status = "not_installed"
        message = "尚未找到完整的本地 ComfyUI 引擎。请先安装完整离线引擎套件。"
    return {
        **common,
        "status": status,
        "checkpoint_count": 0,
        "checkpoints": [],
        "device_count": 0,
        "message": message,
        "generation_submitted": False,
    }


@router.get("/image-runtime")
def local_image_runtime():
    """Discover local ComfyUI status and already-installed checkpoints."""
    global _COMFYUI_PROCESS
    if _COMFYUI_PROCESS is not None and _COMFYUI_PROCESS.poll() is not None:
        _COMFYUI_PROCESS = None
        _close_log_handle()
    return _status_payload()


@router.post("/image-runtime/start")
def start_local_image_runtime():
    """Start only the application's fixed local ComfyUI installation."""
    global _COMFYUI_PROCESS, _COMFYUI_LOG_HANDLE, _COMFYUI_RUNTIME_MODE, _COMFYUI_STARTED_AT
    if os.name != "nt":
        raise HTTPException(status_code=400, detail="当前启动管理仅支持 Windows 桌面版。")
    with _PROCESS_LOCK:
        if _COMFYUI_PROCESS is not None and _COMFYUI_PROCESS.poll() is None:
            return _status_payload()
        # If another process already owns the port, do not adopt or terminate it.
        current = _local_json("/system_stats")
        if current and not current.get("_error"):
            raise HTTPException(status_code=409, detail="检测到已有 ComfyUI 服务正在运行，但不是本软件启动的进程；为避免误操作，本软件不会接管或停止它。")
        root = _install_root()
        python_exe = root / "python_embeded" / "python.exe"
        main_py = root / "ComfyUI" / "main.py"
        if not root.is_dir() or not python_exe.is_file() or not main_py.is_file():
            raise HTTPException(status_code=404, detail=f"没有找到完整的 ComfyUI 引擎。预期位置：{root}")
        if python_exe.is_symlink() or main_py.is_symlink():
            raise HTTPException(status_code=400, detail="ComfyUI 启动文件不能是符号链接。")
        mode, adapter_name = _runtime_mode()
        command = _build_runtime_command(python_exe, main_py, mode)
        log_path = _log_path()
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            if log_path.exists() and log_path.stat().st_size > 5 * 1024 * 1024:
                rotated = log_path.with_suffix(".previous.log")
                if rotated.exists():
                    rotated.unlink()
                log_path.replace(rotated)
            handle = log_path.open("ab", buffering=0)
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
            process = subprocess.Popen(
                command,
                cwd=str(root),
                stdin=subprocess.DEVNULL,
                stdout=handle,
                stderr=subprocess.STDOUT,
                shell=False,
                close_fds=True,
                creationflags=creationflags,
                env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1"},
            )
        except OSError as exc:
            try:
                handle.close()
            except (UnboundLocalError, OSError):
                pass
            raise HTTPException(status_code=500, detail=f"启动 ComfyUI 失败：{exc}") from exc
        _COMFYUI_PROCESS = process
        _COMFYUI_LOG_HANDLE = handle
        _COMFYUI_RUNTIME_MODE = mode
        _COMFYUI_STARTED_AT = int(time.time())
        return {
            "status": "starting",
            "process_id": process.pid,
            "runtime_mode": mode,
            "adapter": adapter_name,
            "base_url": COMFYUI_BASE_URL,
            "log_path": str(log_path),
            "network_used": False,
            "downloaded": False,
            "message": "已启动本软件管理的 ComfyUI 进程，正在进行健康检查。CPU 模式下图片生成会比较慢。" if mode == "cpu" else "已启动本软件管理的 ComfyUI 进程，正在进行健康检查。",
        }


@router.post("/image-runtime/stop")
def stop_local_image_runtime():
    """Stop only the exact process handle launched by this API process."""
    global _COMFYUI_PROCESS, _COMFYUI_RUNTIME_MODE, _COMFYUI_STARTED_AT
    with _PROCESS_LOCK:
        process = _COMFYUI_PROCESS
        if process is None:
            raise HTTPException(status_code=409, detail="当前没有由本软件启动且可安全管理的 ComfyUI 进程；不会结束其他程序。")
        if process.poll() is not None:
            _COMFYUI_PROCESS = None
            _COMFYUI_RUNTIME_MODE = None
            _COMFYUI_STARTED_AT = None
            _close_log_handle()
            return {"status": "stopped", "message": "ComfyUI 进程已经退出。"}
        try:
            process.terminate()
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            # This still targets the same Popen process handle, never taskkill
            # by image name or a user-supplied PID.
            process.kill()
            process.wait(timeout=5)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"停止 ComfyUI 失败：{exc}") from exc
        _COMFYUI_PROCESS = None
        _COMFYUI_RUNTIME_MODE = None
        _COMFYUI_STARTED_AT = None
        _close_log_handle()
        return {"status": "stopped", "message": "已安全停止本软件启动的 ComfyUI 进程；没有结束其他 Python 程序。"}


@router.get("/image-runtime/logs")
def local_image_runtime_logs():
    """Read a bounded tail of the fixed local ComfyUI log file."""
    path = _log_path()
    if not path.is_file():
        return {"log_path": str(path), "content": "", "available": False}
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - MAX_LOG_BYTES), os.SEEK_SET)
            raw = handle.read(MAX_LOG_BYTES)
        return {
            "log_path": str(path),
            "content": raw.decode("utf-8", errors="replace"),
            "available": True,
            "truncated": size > MAX_LOG_BYTES,
        }
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"读取 ComfyUI 日志失败：{exc}") from exc
