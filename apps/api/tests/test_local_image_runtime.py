import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

import app.routers.local_image_runtime as runtime
from app.routers.local_image_runtime import (
    GIB, MAX_LOG_BYTES, _build_runtime_command, _checkpoint_names, _local_json,
    assess_image_hardware, local_image_runtime_logs,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit):
        return self.payload


class FakeOpener:
    def __init__(self, payload):
        self.payload = payload
        self.url = None

    def open(self, request, timeout):
        self.url = request.full_url
        return FakeResponse(self.payload)


class FakeProcess:
    pid = 4321

    def __init__(self):
        self.terminated = False
        self.killed = False

    def poll(self):
        return None if not self.terminated else 0

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.killed = True
        self.terminated = True


class LocalImageRuntimeTests(unittest.TestCase):
    def test_only_fixed_local_paths_are_allowed(self):
        with self.assertRaises(ValueError):
            _local_json("http://example.com")

    def test_checkpoint_list_is_extracted(self):
        payload = {"CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [["sd15.safetensors", "sdxl.safetensors"]]}}}}
        self.assertEqual(_checkpoint_names(payload), ["sd15.safetensors", "sdxl.safetensors"])

    def test_checkpoint_response_shape_is_validated(self):
        self.assertEqual(_checkpoint_names({"_error": "HTTP 500"}), [])
        self.assertEqual(_checkpoint_names([]), [])

    def test_local_json_uses_only_loopback(self):
        opener = FakeOpener(b'{"system": {"devices": []}}')
        result = _local_json("/system_stats", opener=opener)
        self.assertEqual(result["system"]["devices"], [])
        self.assertEqual(opener.url, "http://127.0.0.1:8188/system_stats")

    def test_hardware_assessment_warns_for_low_vram(self):
        result = assess_image_hardware({
            "memory": {"total_bytes": 8 * GIB},
            "gpu": {"vram_bytes": 3 * GIB},
        })
        self.assertEqual(result["gpu_level"], "limited")
        self.assertTrue(any("低于 4 GiB" in item for item in result["reasons"]))

    def test_hardware_assessment_does_not_assume_no_gpu_when_unknown(self):
        result = assess_image_hardware({"memory": {}, "gpu": {}})
        self.assertEqual(result["gpu_level"], "unknown")
        self.assertTrue(any("不能据此判断没有 GPU" in item for item in result["reasons"]))

    def test_cpu_launch_command_uses_rocm_safe_compatibility_launcher(self):
        main_py = Path("ComfyUI") / "main.py"
        launcher = Path("run_comfy_cpu.py")
        command = _build_runtime_command(Path("python.exe"), main_py, "cpu", launcher)
        self.assertEqual(command, ["python.exe", "-s", str(launcher)])
        self.assertNotIn(str(main_py), command)

    def test_gpu_launch_command_keeps_native_gpu_entrypoint(self):
        main_py = Path("ComfyUI") / "main.py"
        command = _build_runtime_command(Path("python.exe"), main_py, "gpu")
        self.assertEqual(command, ["python.exe", "-s", str(main_py), "--windows-standalone-build"])
        self.assertNotIn("--cpu", command)

    def test_ryzen_4800u_integrated_radeon_uses_cpu_mode(self):
        with patch("app.routers.local_image_runtime.detect_windows_video_adapters", return_value={
            "status": "detected",
            "adapters": [{"name": "AMD Radeon(TM) Graphics", "vendor": "AMD"}],
            "network_used": False,
        }):
            self.assertEqual(runtime._runtime_mode(), ("cpu", "AMD Radeon(TM) Graphics"))

    def test_unknown_adapter_falls_back_to_cpu(self):
        with patch("app.routers.local_image_runtime.detect_windows_video_adapters", return_value={
            "status": "unavailable", "adapters": [], "network_used": False,
        }):
            self.assertEqual(runtime._runtime_mode(), ("cpu", "未能确认受支持的 GPU，安全使用 CPU 模式"))

    def test_stop_only_terminates_the_process_handle_owned_by_application(self):
        fake = FakeProcess()
        old_process = runtime._COMFYUI_PROCESS
        old_mode = runtime._COMFYUI_RUNTIME_MODE
        old_started = runtime._COMFYUI_STARTED_AT
        try:
            runtime._COMFYUI_PROCESS = fake
            runtime._COMFYUI_RUNTIME_MODE = "cpu"
            runtime._COMFYUI_STARTED_AT = 123
            with patch("app.routers.local_image_runtime._close_log_handle"):
                result = runtime.stop_local_image_runtime()
            self.assertTrue(fake.terminated)
            self.assertFalse(fake.killed)
            self.assertEqual(result["status"], "stopped")
            self.assertIsNone(runtime._COMFYUI_PROCESS)
        finally:
            runtime._COMFYUI_PROCESS = old_process
            runtime._COMFYUI_RUNTIME_MODE = old_mode
            runtime._COMFYUI_STARTED_AT = old_started

    def test_reap_exited_process_preserves_exit_code_and_closes_log(self):
        class ExitedProcess:
            returncode = 17

            def poll(self):
                return 17

        old_process = runtime._COMFYUI_PROCESS
        old_mode = runtime._COMFYUI_RUNTIME_MODE
        old_started = runtime._COMFYUI_STARTED_AT
        old_exit = runtime._COMFYUI_LAST_EXIT_CODE
        try:
            runtime._COMFYUI_PROCESS = ExitedProcess()
            runtime._COMFYUI_RUNTIME_MODE = "cpu"
            runtime._COMFYUI_STARTED_AT = 123
            with patch("app.routers.local_image_runtime._close_log_handle") as close_log:
                self.assertTrue(runtime._reap_exited_managed_process())
                close_log.assert_called_once()
            self.assertIsNone(runtime._COMFYUI_PROCESS)
            self.assertIsNone(runtime._COMFYUI_RUNTIME_MODE)
            self.assertIsNone(runtime._COMFYUI_STARTED_AT)
            self.assertEqual(runtime._COMFYUI_LAST_EXIT_CODE, 17)
        finally:
            runtime._COMFYUI_PROCESS = old_process
            runtime._COMFYUI_RUNTIME_MODE = old_mode
            runtime._COMFYUI_STARTED_AT = old_started
            runtime._COMFYUI_LAST_EXIT_CODE = old_exit

    def test_stop_refuses_to_touch_unmanaged_processes(self):
        old_process = runtime._COMFYUI_PROCESS
        try:
            runtime._COMFYUI_PROCESS = None
            with self.assertRaises(HTTPException) as caught:
                runtime.stop_local_image_runtime()
            self.assertEqual(caught.exception.status_code, 409)
        finally:
            runtime._COMFYUI_PROCESS = old_process

    def test_api_shutdown_cleans_up_only_the_owned_process(self):
        fake = FakeProcess()
        old_process = runtime._COMFYUI_PROCESS
        try:
            runtime._COMFYUI_PROCESS = fake
            with patch("app.routers.local_image_runtime._close_log_handle"):
                runtime.shutdown_managed_comfyui()
            self.assertTrue(fake.terminated)
            self.assertFalse(fake.killed)
            self.assertIsNone(runtime._COMFYUI_PROCESS)
        finally:
            runtime._COMFYUI_PROCESS = old_process

    def test_runtime_logs_report_missing_file_without_error(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(runtime, "_log_path", return_value=Path(directory) / "missing.log"):
                result = local_image_runtime_logs()
        self.assertFalse(result["available"])
        self.assertEqual(result["content"], "")

    def test_runtime_logs_read_only_the_bounded_tail(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "comfyui.log"
            path.write_bytes(b"A" * (MAX_LOG_BYTES + 37))
            with patch.object(runtime, "_log_path", return_value=path):
                result = local_image_runtime_logs()
        self.assertTrue(result["available"])
        self.assertTrue(result["truncated"])
        self.assertEqual(len(result["content"]), MAX_LOG_BYTES)
        self.assertEqual(result["content"], "A" * MAX_LOG_BYTES)

    def test_start_stop_and_log_endpoints_are_registered(self):
        paths = {route.path for route in runtime.router.routes}
        self.assertIn("/api/local-ai/image-runtime/start", paths)
        self.assertIn("/api/local-ai/image-runtime/stop", paths)
        self.assertIn("/api/local-ai/image-runtime/logs", paths)


if __name__ == "__main__":
    unittest.main()
