import unittest

from app.routers.comfyui_installer import (
    GIB, ENGINE_RESERVE_BYTES, MODEL_RESERVE_BYTES, assess_comfyui_install,
)


class ComfyUIInstallReadinessTests(unittest.TestCase):
    def test_low_disk_blocks_engine_install(self):
        result = assess_comfyui_install(
            {"memory": {"total_bytes": 32 * GIB}, "gpu": {"vram_bytes": 8 * GIB}},
            7 * GIB,
        )
        self.assertFalse(result["can_install_engine"])
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["download_started"])

    def test_engine_can_fit_but_model_reserve_warns(self):
        result = assess_comfyui_install(
            {"memory": {"total_bytes": 16 * GIB}, "gpu": {"vram_bytes": 6 * GIB}},
            ENGINE_RESERVE_BYTES + 1,
        )
        self.assertTrue(result["can_install_engine"])
        self.assertTrue(any("不足以同时预留" in item for item in result["warnings"]))

    def test_unknown_hardware_requires_manual_review(self):
        result = assess_comfyui_install({"memory": {}, "gpu": {}}, None)
        self.assertEqual(result["status"], "needs_manual_check")
        self.assertEqual(result["hardware_profile"], "unknown")

    def test_large_storage_separates_engine_and_optional_model(self):
        result = assess_comfyui_install(
            {"memory": {"total_bytes": 32 * GIB}, "gpu": {"vram_bytes": 16 * GIB}},
            ENGINE_RESERVE_BYTES + MODEL_RESERVE_BYTES,
        )
        self.assertTrue(result["can_install_engine"])
        self.assertEqual(result["recommended_total_reserve_bytes"], 23 * GIB)
        self.assertFalse(result["network_used"])


if __name__ == "__main__":
    unittest.main()
