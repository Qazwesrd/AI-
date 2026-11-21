import unittest
from unittest.mock import patch

from fastapi import HTTPException
from app.routers.local_image_generation import (
    inspect_local_reference_capabilities,
    _local_request,
)


class ComfyUIReferenceCapabilityTests(unittest.TestCase):
    def test_reference_capability_detects_nodes_and_model_files(self):
        objects = {
            "LoadImage": {"LoadImage": {"input": {"required": {"image": [["ref.png"]]}}}},
            "CLIPVisionLoader": {"CLIPVisionLoader": {"input": {"required": {"clip_name": [["clip-vit-h.safetensors"]]}}}},
            "IPAdapterUnifiedLoader": {"IPAdapterUnifiedLoader": {"input": {"required": {"ipadapter_file": [["ip-adapter-plus.safetensors"]]}}}},
            "IPAdapter": {"IPAdapter": {"input": {"required": {"model": ["MODEL"], "ipadapter": ["IPADAPTER"]}}}},
            "IPAdapterAdvanced": {},
            "IPAdapterModelLoader": {},
            "PrepImageForClipVision": {"PrepImageForClipVision": {"input": {"required": {"image": ["IMAGE"]}}}},
        }
        with patch("app.routers.local_image_generation._local_request", side_effect=lambda path, **kwargs: objects[path.rsplit("/", 1)[-1]]):
            result = inspect_local_reference_capabilities()
        self.assertTrue(result["reference_conditioning_nodes_ready"])
        self.assertTrue(result["reference_conditioning_models_ready"])
        self.assertTrue(result["reference_conditioning_ready"])
        self.assertEqual(result["missing_node_types"], [])
        self.assertEqual(result["reference_nodes"]["IPAdapterUnifiedLoader"]["adapter_models"], ["ip-adapter-plus.safetensors"])

    def test_reference_capability_reports_missing_components(self):
        with patch("app.routers.local_image_generation._local_request", return_value={}):
            result = inspect_local_reference_capabilities()
        self.assertFalse(result["reference_conditioning_ready"])
        self.assertIn("LoadImage", result["missing_node_types"])
        self.assertIn("IP-Adapter model", result["missing_model_files"])

    def test_local_request_blocks_arbitrary_comfyui_endpoints(self):
        with self.assertRaises(ValueError):
            _local_request("/view?filename=secret.png")
        with self.assertRaises(ValueError):
            _local_request("/object_info/UnknownCustomNode")


if __name__ == "__main__":
    unittest.main()
