import unittest
from app.routers.local_model_hardware import assess_qwen3_4b, GIB


class LocalModelHardwareReadinessTests(unittest.TestCase):
    def setUp(self):
        self.hardware = {
            "cpu": {"logical_cores": 8},
            "memory": {"total_bytes": 16 * GIB},
            "gpu": {"status": "not_detected", "vram_bytes": None},
        }

    def test_supported_windows_11_is_recommended_without_gpu_claim(self):
        result = assess_qwen3_4b(self.hardware, 20 * GIB, "Windows", "10.0.22631")
        self.assertEqual(result["status"], "recommended")
        self.assertTrue(result["operating_system"]["supported"])
        self.assertIn("不代表电脑没有其他品牌显卡", " ".join(result["reasons"]))

    def test_amd_integrated_graphics_is_detected_but_not_promised_gpu_acceleration(self):
        hardware = {
            "cpu": {"logical_cores": 8},
            "memory": {"total_bytes": 16 * GIB},
            "gpu": {
                "status": "detected", "vendor": "AMD",
                "name": "AMD Radeon(TM) Graphics", "vram_bytes": None,
            },
        }
        result = assess_qwen3_4b(hardware, 20 * GIB, "Windows", "10.0.22631")
        self.assertEqual(result["recommended_inference_mode"], "cpu")
        self.assertFalse(result["gpu_acceleration_verified"])
        self.assertIn("AMD", " ".join(result["reasons"]))
        self.assertIn("CPU 推理方案", " ".join(result["reasons"]))

    def test_intel_adapter_is_not_misreported_as_nvidia_or_gpu_verified(self):
        hardware = {
            "cpu": {"logical_cores": 8},
            "memory": {"total_bytes": 16 * GIB},
            "gpu": {
                "status": "detected", "vendor": "Intel",
                "name": "Intel UHD Graphics", "vram_bytes": None,
            },
        }
        result = assess_qwen3_4b(hardware, 20 * GIB, "Windows", "10.0.22631")
        self.assertEqual(result["recommended_inference_mode"], "cpu")
        self.assertFalse(result["gpu_acceleration_verified"])
        self.assertIn("Intel", " ".join(result["reasons"]))

    def test_windows_10_before_22h2_is_blocked(self):
        result = assess_qwen3_4b(self.hardware, 20 * GIB, "Windows", "10.0.19044")
        self.assertEqual(result["status"], "not_recommended")
        self.assertFalse(result["operating_system"]["supported"])

    def test_low_ram_blocks_qwen4b(self):
        hardware = {**self.hardware, "memory": {"total_bytes": 6 * GIB}}
        result = assess_qwen3_4b(hardware, 20 * GIB, "Windows", "10.0.22631")
        self.assertEqual(result["status"], "not_recommended")
        self.assertTrue(any("低于 8 GiB" in item for item in result["blockers"]))

    def test_insufficient_disk_blocks_install(self):
        result = assess_qwen3_4b(self.hardware, 2 * GIB, "Windows", "10.0.22631")
        self.assertEqual(result["status"], "not_recommended")
        self.assertTrue(any("不足 6 GiB" in item for item in result["blockers"]))

    def test_unknown_ram_requires_manual_check(self):
        hardware = {**self.hardware, "memory": {"total_bytes": None}}
        result = assess_qwen3_4b(hardware, 20 * GIB, "Windows", "10.0.22631")
        self.assertEqual(result["status"], "needs_manual_check")

    def test_non_windows_is_not_misreported_as_windows_supported(self):
        result = assess_qwen3_4b(self.hardware, 20 * GIB, "Linux", "6.8.0")
        self.assertEqual(result["operating_system"]["status"], "not_windows")
        self.assertEqual(result["status"], "recommended")


if __name__ == "__main__":
    unittest.main()
