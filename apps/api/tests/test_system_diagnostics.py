import unittest
import tempfile
from pathlib import Path

from unittest.mock import patch

from app.routers.system import _check_local_ai_tools, _health_status, _recommend_local_model_tier, _scan_local_model_files, _parse_windows_video_adapters, _detect_gpu_hardware


class SystemDiagnosticsTests(unittest.TestCase):

    def test_windows_detects_amd_integrated_graphics_without_faking_dedicated_vram(self):
        adapters = _parse_windows_video_adapters([{
            "Name": "AMD Radeon(TM) Graphics",
            "AdapterRAM": 536870912,
            "DriverVersion": "31.0.12027.9001",
        }])
        self.assertEqual(len(adapters), 1)
        gpu = adapters[0]
        self.assertEqual(gpu["vendor"], "AMD")
        self.assertTrue(gpu["shared_memory"])
        self.assertIsNone(gpu["vram_bytes"])
        self.assertEqual(gpu["reported_adapter_memory_bytes"], 536870912)
        self.assertFalse(gpu["dedicated_vram_confirmed"])
        self.assertIn("共享", gpu["note"])

    def test_windows_detects_intel_integrated_graphics(self):
        adapters = _parse_windows_video_adapters({
            "Name": "Intel(R) Iris(R) Xe Graphics",
            "AdapterRAM": 1073741824,
        })
        self.assertEqual(adapters[0]["vendor"], "Intel")
        self.assertTrue(adapters[0]["shared_memory"])
        self.assertIsNone(adapters[0]["vram_bytes"])

    def test_gpu_detection_prefers_nvidia_telemetry_when_available(self):
        nvidia = {
            "status": "detected", "vendor": "NVIDIA",
            "name": "GeForce RTX", "vram_bytes": 8 * 1024 ** 3,
        }
        with patch("app.routers.system._detect_nvidia_gpu", return_value=nvidia), patch(
            "app.routers.system._detect_windows_video_adapters"
        ) as windows:
            result = _detect_gpu_hardware()
        self.assertEqual(result, nvidia)
        windows.assert_not_called()

    def test_gpu_detection_uses_windows_adapter_when_nvidia_not_present(self):
        amd = {
            "status": "detected", "vendor": "AMD", "name": "AMD Radeon(TM) Graphics",
            "vram_bytes": None, "shared_memory": True,
        }
        with patch("app.routers.system._detect_nvidia_gpu", return_value={
            "status": "not_detected", "vendor": None, "name": None, "vram_bytes": None,
        }), patch("app.routers.system._detect_windows_video_adapters", return_value=amd):
            result = _detect_gpu_hardware()
        self.assertEqual(result["vendor"], "AMD")
        self.assertIsNone(result["vram_bytes"])

    def test_model_tier_recommends_small_model_for_low_ram(self):
        result = _recommend_local_model_tier(6 * 1024 ** 3, None)
        self.assertEqual(result["tier"], "very_small_or_not_recommended")
        self.assertIsNone(result["vram_gib"])

    def test_model_tier_uses_vram_when_available(self):
        result = _recommend_local_model_tier(16 * 1024 ** 3, 8 * 1024 ** 3)
        self.assertEqual(result["tier"], "gpu_3b_7b_quantized")
        self.assertEqual(result["vram_gib"], 8.0)
        self.assertIn("性能保证", result["caution"])

    def test_model_inventory_lists_only_supported_direct_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            model_dir = Path(temp_dir) / "models"
            model_dir.mkdir()
            (model_dir / "story-model.gguf").write_bytes(b"12345")
            (model_dir / "vision.safetensors").write_bytes(b"123")
            (model_dir / "notes.txt").write_text("not a model", encoding="utf-8")
            nested = model_dir / "nested"
            nested.mkdir()
            (nested / "hidden.gguf").write_bytes(b"1234567")
            with patch("app.routers.system.STORAGE_DIR", temp_dir):
                result = _scan_local_model_files()
            self.assertEqual(result["status"], "found")
            self.assertEqual(result["count"], 2)
            self.assertEqual(result["total_bytes"], 8)
            self.assertEqual({item["name"] for item in result["files"]}, {"story-model.gguf", "vision.safetensors"})
            self.assertIn("许可证", result["note"])

    def test_model_inventory_handles_missing_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("app.routers.system.STORAGE_DIR", temp_dir):
                result = _scan_local_model_files()
            self.assertFalse(result["directory_exists"])
            self.assertEqual(result["status"], "empty")
            self.assertEqual(result["count"], 0)

    def test_all_checks_ok(self):
        self.assertEqual(_health_status([
            {"status": "ok"}, {"status": "ok"}
        ]), "ok")

    def test_warning_is_reported_without_error(self):
        self.assertEqual(_health_status([
            {"status": "ok"}, {"status": "warning"}
        ]), "warning")

    def test_error_has_highest_priority(self):
        self.assertEqual(_health_status([
            {"status": "warning"}, {"status": "error"}
        ]), "error")

    @patch("app.routers.system.shutil.which", return_value=None)
    def test_local_ai_detection_is_offline_and_does_not_auto_download(self, _which):
        result = _check_local_ai_tools()
        self.assertEqual(result["mode"], "local_only_detection")
        self.assertFalse(result["network_used"])
        self.assertFalse(result["auto_downloaded"])
        self.assertEqual(len(result["tools"]), 3)
        self.assertTrue(all(item["status"] == "not_detected" for item in result["tools"]))
        _which.assert_called()

    @patch("app.routers.system.shutil.which", side_effect=lambda name: "/usr/bin/ollama" if name == "ollama" else None)
    def test_local_ai_detection_reports_available_executable(self, _which):
        result = _check_local_ai_tools()
        ollama = next(item for item in result["tools"] if item["id"] == "ollama")
        self.assertEqual(ollama["status"], "installed")
        self.assertEqual(ollama["executable"], "/usr/bin/ollama")
        self.assertFalse(ollama["network_used"])


if __name__ == "__main__":
    unittest.main()
