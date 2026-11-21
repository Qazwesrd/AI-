import unittest

from unittest.mock import patch

from app.routers.system import _check_local_ai_tools, _health_status, _recommend_local_model_tier


class SystemDiagnosticsTests(unittest.TestCase):
    def test_model_tier_recommends_small_model_for_low_ram(self):
        result = _recommend_local_model_tier(6 * 1024 ** 3, None)
        self.assertEqual(result["tier"], "very_small_or_not_recommended")
        self.assertIsNone(result["vram_gib"])

    def test_model_tier_uses_vram_when_available(self):
        result = _recommend_local_model_tier(16 * 1024 ** 3, 8 * 1024 ** 3)
        self.assertEqual(result["tier"], "gpu_3b_7b_quantized")
        self.assertEqual(result["vram_gib"], 8.0)
        self.assertIn("性能保证", result["caution"])

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
