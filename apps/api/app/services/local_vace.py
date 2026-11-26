"""Local-only Wan 2.1 VACE worker; no network, download, or paid-provider fallback."""
import logging
import os
import subprocess
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from app.database import SessionLocal
from app.models.media import MediaFile, MediaTask
from app.services.media_service import VIDEO_DIR, probe_media
from app.services.task_runtime import acquire_task_lease, heartbeat_task_lease, release_task_lease

logger = logging.getLogger("ai_comic_studio.local_vace")
_STOP = threading.Event()
_THREAD = None
_ACTIVE_LOCK = threading.Lock()
_ACTIVE_PROCESS = None
_ACTIVE_TASK_ID = None
_MODEL_FILES = {
    "diffusion": "wan2.1-v3-vace-1.3b-q4_0.gguf",
    "text_encoder": "umt5-xxl-encoder-q4_k_m.gguf",
    "vae": "pig_wan_vae_fp32-f16.gguf",
}


def _runtime():
    data_dir = Path(os.environ.get("APP_DATA_DIR", "")).expanduser().resolve()
    if not os.environ.get("APP_DATA_DIR"):
        raise RuntimeError("未设置本地 AI 数据目录 APP_DATA_DIR。")
    engines = (data_dir / "engines").resolve()
    cli_root = (engines / "StableDiffusionCpp").resolve()
    model_root = (engines / "VACE" / "models").resolve()
    if engines not in cli_root.parents or engines not in model_root.parents:
        raise RuntimeError("VACE 引擎目录路径校验失败。")
    cli = next((item for item in cli_root.rglob("sd-cli.exe") if item.is_file()), None)
    if cli is None:
        raise FileNotFoundError("没有找到内置 StableDiffusionCpp sd-cli.exe。")
    cli = cli.resolve()
    if cli_root not in cli.parents:
        raise RuntimeError("sd-cli.exe 路径超出受管引擎目录。")
    models = {}
    for key, filename in _MODEL_FILES.items():
        model = (model_root / filename).resolve()
        if model_root not in model.parents or not model.is_file():
            raise FileNotFoundError("缺少本地 VACE 模型文件：" + filename)
        models[key] = model
    return cli, models


def _write_state(db, task, status=None, stage=None, error=None):
    request = dict(task.request_data or {})
    if stage is not None:
        request["stage"] = stage
    request["stage_updated_at"] = datetime.utcnow().isoformat()
    task.request_data = request
    if status is not None:
        task.status = status
    if error is not None:
        task.error = str(error)[:3000]
    task.updated_at = datetime.utcnow()
    db.commit()


def _tail(path, limit=2200):
    try:
        with open(path, "rb") as stream:
            stream.seek(max(0, os.path.getsize(path) - limit))
            return stream.read().decode("utf-8", errors="replace")[-limit:]
    except OSError:
        return ""


def _terminate_tree(process):
    if process is None or process.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=12, check=False,
            )
        else:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
    except Exception:
        logger.exception("Could not stop owned VACE process tree")
        try:
            process.kill()
        except Exception:
            pass


def _run_task(task_id):
    global _ACTIVE_PROCESS, _ACTIVE_TASK_ID
    db = SessionLocal()
    process = None
    output = None
    log_path = None
    lease = False
    succeeded = False
    failure = ""
    try:
        task = db.query(MediaTask).filter(
            MediaTask.id == task_id,
            MediaTask.task_type == "local_vace_video",
        ).first()
        if task is None or task.status != "queued":
            return
        try:
            acquire_task_lease(db, "media", task_id, priority=3)
            lease = True
        except Exception as exc:
            logger.info("VACE task %s waits for a task-runtime slot: %s", task_id, exc)
            return

        db.expire_all()
        task = db.query(MediaTask).filter(MediaTask.id == task_id).first()
        if task is None or task.status != "queued":
            release_task_lease(db, "media", task_id, False, "任务在启动前已取消或状态已变化")
            lease = False
            return
        _write_state(db, task, status="starting", stage="preparing_runtime")

        options = dict(task.request_data or {})
        prompt = (task.prompt or "").strip()
        width = int(options.get("width", 256))
        height = int(options.get("height", 256))
        frames = int(options.get("frames", 9))
        steps = int(options.get("steps", 4))
        if not prompt:
            raise ValueError("视频提示词不能为空。")
        if width not in (256, 384, 512) or height not in (256, 384, 512):
            raise ValueError("分辨率仅支持 256、384 或 512。")
        if not 5 <= frames <= 33 or not 4 <= steps <= 20:
            raise ValueError("帧数或采样步数超出安全范围。")

        cli, models = _runtime()
        db.expire_all()
        task = db.query(MediaTask).filter(MediaTask.id == task_id).first()
        if task is None or task.status == "cancel_requested":
            if task:
                _write_state(db, task, status="cancelled", stage="cancelled")
            return
        if _STOP.is_set():
            _write_state(db, task, status="interrupted", stage="interrupted",
                         error="应用关闭时任务中断；不会自动重新生成。")
            return
        VIDEO_DIR.mkdir(parents=True, exist_ok=True)
        stamp = uuid.uuid4().hex[:10]
        output = VIDEO_DIR / f"local_vace_{task_id}_{stamp}.avi"
        log_path = VIDEO_DIR / f"local_vace_{task_id}_{stamp}.log"
        command = [
            str(cli), "-M", "vid_gen",
            "--diffusion-model", str(models["diffusion"]),
            "--vae", str(models["vae"]),
            "--t5xxl", str(models["text_encoder"]),
            "-p", prompt,
            "-n", str(options.get("negative_prompt") or "blurry, low quality, watermark, text"),
            "--cfg-scale", "4.0", "--sampling-method", "euler",
            "--steps", str(steps), "-W", str(width), "-H", str(height),
            "--video-frames", str(frames), "--offload-to-cpu", "-o", str(output),
        ]
        db.expire_all()
        task = db.query(MediaTask).filter(MediaTask.id == task_id).first()
        if task is None or task.status == "cancel_requested":
            if task:
                _write_state(db, task, status="cancelled", stage="cancelled")
            return
        _write_state(db, task, status="running", stage="generating")
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        with open(log_path, "w", encoding="utf-8", errors="replace") as log:
            process = subprocess.Popen(
                command, cwd=str(cli.parent), stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT, shell=False,
                creationflags=flags,
            )
            with _ACTIVE_LOCK:
                _ACTIVE_PROCESS, _ACTIVE_TASK_ID = process, task_id

            started = time.monotonic()
            last_heartbeat = started
            while process.poll() is None:
                if _STOP.is_set():
                    _terminate_tree(process)
                    failure = "应用关闭时本地 VACE 任务被中断；下次启动不会自动重跑。"
                    break
                db.expire_all()
                current = db.query(MediaTask).filter(MediaTask.id == task_id).first()
                if current is None:
                    _terminate_tree(process)
                    failure = "任务记录已不存在，已停止对应生成进程。"
                    break
                if current.status == "cancel_requested":
                    _terminate_tree(process)
                    _write_state(db, current, status="cancelled", stage="cancelled")
                    break
                if time.monotonic() - started > 6 * 60 * 60:
                    _terminate_tree(process)
                    failure = "本地 VACE 生成超过 6 小时上限，已停止进程。"
                    break
                if time.monotonic() - last_heartbeat >= 10:
                    heartbeat_task_lease(db, "media", task_id)
                    last_heartbeat = time.monotonic()
                time.sleep(1)

        current = db.query(MediaTask).filter(MediaTask.id == task_id).first()
        if current and current.status == "cancelled":
            if output:
                output.unlink(missing_ok=True)
            return
        if failure:
            if current:
                _write_state(
                    db, current,
                    status="interrupted" if _STOP.is_set() else "failed",
                    stage="interrupted" if _STOP.is_set() else "failed",
                    error=failure,
                )
            if output:
                output.unlink(missing_ok=True)
            return
        if process.returncode != 0:
            raise RuntimeError(
                f"VACE 推理进程退出码 {process.returncode}。日志：{_tail(log_path)}"
            )
        if not output or not output.is_file() or output.stat().st_size < 4096:
            raise RuntimeError("VACE 未生成有效视频文件；请查看本地任务日志。")

        current = db.query(MediaTask).filter(MediaTask.id == task_id).first()
        if current is None:
            raise RuntimeError("视频任务记录不存在。")
        _write_state(db, current, status="validating", stage="validating_output")
        info = probe_media(output)
        streams = info.get("streams") or []
        video = next((item for item in streams if item.get("codec_type") == "video"), None)
        if not video:
            raise RuntimeError("FFprobe 校验失败：输出文件没有视频流。")
        duration = float((info.get("format") or {}).get("duration") or video.get("duration") or 0)
        if duration <= 0:
            raise RuntimeError("FFprobe 无法确认输出视频时长。")

        media = MediaFile(
            name=output.name, category="video", file_path=str(output),
            mime_type="video/x-msvideo", file_size=output.stat().st_size,
            duration=duration,
            metadata_json={
                "generator": "Wan 2.1 VACE 1.3B (local)",
                "local_only": True, "paid_api_used": False,
                "prompt": prompt, "width": width, "height": height,
                "frames": frames, "steps": steps,
                "ffprobe": {
                    "duration": duration, "width": video.get("width"),
                    "height": video.get("height"), "codec": video.get("codec_name"),
                },
            },
        )
        current.result_path = str(output)
        current.result_data = {
            "media_file_name": output.name, "media_file_size": output.stat().st_size,
            "duration": duration, "ffprobe": media.metadata_json["ffprobe"],
            "local_only": True, "paid_api_used": False,
        }
        request = dict(current.request_data or {})
        request["stage"] = "completed"
        request["progress"] = 100
        current.request_data = request
        current.status = "completed"
        current.error = ""
        current.updated_at = datetime.utcnow()
        db.add(media)
        db.flush()
        current.result_data = dict(current.result_data or {})
        current.result_data["media_file_id"] = media.id
        db.commit()
        succeeded = True
    except Exception as exc:
        logger.exception("Local VACE task %s failed", task_id)
        failure = str(exc)[:2800]
        try:
            current = db.query(MediaTask).filter(MediaTask.id == task_id).first()
            if current and current.status not in ("cancelled", "interrupted"):
                _write_state(db, current, status="failed", stage="failed", error=failure)
        except Exception:
            db.rollback()
        if output:
            output.unlink(missing_ok=True)
    finally:
        with _ACTIVE_LOCK:
            if _ACTIVE_TASK_ID == task_id:
                _ACTIVE_PROCESS = None
                _ACTIVE_TASK_ID = None
        if lease:
            try:
                release_task_lease(db, "media", task_id, succeeded, failure)
            except Exception:
                logger.exception("Could not release VACE task lease %s", task_id)
        db.close()


def _worker_loop():
    while not _STOP.is_set():
        db = SessionLocal()
        task_id = None
        try:
            task = db.query(MediaTask).filter(
                MediaTask.task_type == "local_vace_video",
                MediaTask.status == "queued",
            ).order_by(MediaTask.created_at.asc(), MediaTask.id.asc()).first()
            task_id = task.id if task else None
        except Exception:
            logger.exception("Could not read local VACE queue")
        finally:
            db.close()
        if task_id is None:
            _STOP.wait(1)
        else:
            _run_task(task_id)


def start_local_vace_worker():
    global _THREAD
    if _THREAD and _THREAD.is_alive():
        return
    _STOP.clear()
    db = SessionLocal()
    try:
        stale = db.query(MediaTask).filter(
            MediaTask.task_type == "local_vace_video",
            MediaTask.status.in_(("starting", "running", "validating", "cancel_requested")),
        ).all()
        for task in stale:
            request = dict(task.request_data or {})
            request["stage"] = "interrupted"
            task.request_data = request
            task.status = "interrupted"
            task.error = "上次应用关闭时任务中断；本次启动不会自动重新生成。"
            task.updated_at = datetime.utcnow()
        if stale:
            db.commit()
    finally:
        db.close()
    _THREAD = threading.Thread(target=_worker_loop, name="local-vace-worker", daemon=True)
    _THREAD.start()
    logger.info("Local VACE worker started (local-only; no cloud fallback).")


def stop_local_vace_worker():
    _STOP.set()
    with _ACTIVE_LOCK:
        process = _ACTIVE_PROCESS
    _terminate_tree(process)
    if _THREAD and _THREAD.is_alive():
        _THREAD.join(timeout=12)
    logger.info("Local VACE worker stopped.")


def request_cancel_local_vace(db, task):
    if task.task_type != "local_vace_video":
        raise ValueError("不是本地 VACE 视频任务。")
    if task.status in ("completed", "failed", "cancelled", "interrupted"):
        return {"task_id": task.id, "status": task.status, "message": "任务已结束，无需取消。"}
    request = dict(task.request_data or {})
    if task.status == "queued":
        task.status = "cancelled"
        request["stage"] = "cancelled"
        task.request_data = request
        task.updated_at = datetime.utcnow()
        db.commit()
        return {"task_id": task.id, "status": task.status, "message": "已取消排队中的本地 VACE 任务。"}
    task.status = "cancel_requested"
    task.updated_at = datetime.utcnow()
    db.commit()
    return {"task_id": task.id, "status": task.status, "message": "已请求取消；正在停止本任务创建的进程树。"}


def validate_local_vace_runtime():
    """Raise a clear error if the bundled local VACE runtime or weights are missing."""
    return _runtime()
