import unittest
from unittest.mock import patch

from app.routers.local_image_runtime import (
    GIB, _checkpoint_names, _local_json, _selector_choices, assess_image_hardware, local_image_runtime,
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


class LocalImageRuntimeTests(unittest.TestCase):
    def test_only_fixed_local_paths_are_allowed(self):
        with self.assertRaises(ValueError):
            _local_json("http://example.com")

    def test_checkpoint_list_is_extracted(self):
        payload = {"CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [["sd15.safetensors", "sdxl.safetensors"]]}}}}
        self.assertEqual(_checkpoint_names(payload), ["sd15.safetensors", "sdxl.safetensors"])

    def test_model_selector_choices_are_enumerated(self):
        payload = {"VAELoader": {"input": {"required": {"vae_name": [["vae-ft.safetensors", "sdxl_vae.safetensors"]]}}}}
        self.assertEqual(
            _selector_choices(payload, "vae_name"),
            ["sdxl_vae.safetensors", "vae-ft.safetensors"],
        )

    def test_checkpoint_response_shape_is_validated(self):
        self.assertEqual(_checkpoint_names({"_error": "HTTP 500"}), [])
        self.assertEqual(_checkpoint_names([]), [])

    def test_local_json_uses_only_loopback(self):
        opener = FakeOpener(b'{"system": {"devices": []}}')
        result = _local_json("/system_stats", opener=opener)
        self.assertEqual(result["system"]["devices"], [])
        self.assertEqual(opener.url, "http://127.0.0.1:8188/system_stats")

    def test_image_runtime_reads_comfyui_devices_from_top_level(self):
        stats = {
            "system": {"os": "Windows", "comfyui_version": "test"},
            "devices": [{"name": "AMD Radeon Graphics", "type": "cpu", "vram_total": 0, "vram_free": 0}],
        }
        with patch("app.routers.local_image_runtime._local_json", return_value=stats), patch(
            "app.routers.local_image_runtime._scan_comfy_models",
            return_value={"items": [], "total": 0, "counts_by_category": {}, "selectors": {}},
        ), patch("app.routers.local_image_runtime._get_hardware_profile", return_value={"memory": {}, "gpu": {}}):
            result = local_image_runtime()
        self.assertEqual(result["device_count"], 1)

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


if __name__ == "__main__":
    unittest.main()
