import unittest

from app.routers.local_video_runtime import assess_wan_vace_nodes


class LocalVideoRuntimeTests(unittest.TestCase):
    def test_missing_runtime_does_not_claim_generation_ready(self):
        result = assess_wan_vace_nodes(None)
        self.assertEqual(result["status"], "unavailable")
        self.assertFalse(result["generation_ready"])

    def test_empty_nodes_report_missing_without_cloud_fallback(self):
        result = assess_wan_vace_nodes({})
        self.assertEqual(result["status"], "missing")
        self.assertFalse(result["vace_node_found"])
        self.assertFalse(result["generation_ready"])

    def test_detects_wan_and_vace_nodes_but_not_complete_workflow(self):
        info = {
            "WanVideoModelLoader": {
                "input": {"required": {"model": [["wan2.1_i2v.safetensors", "wan2.1_t2v.safetensors"]]}}
            },
            "WanVideoVACEEncode": {"input": {"required": {"image": ["IMAGE"]}}},
            "WanVideoSampler": {"input": {"required": {"steps": ["INT", {"default": 20}]}}},
        }
        result = assess_wan_vace_nodes(info)
        self.assertEqual(result["status"], "detected")
        self.assertTrue(result["vace_node_found"])
        self.assertTrue(result["wan_video_nodes_found"])
        self.assertIn("wan2.1_i2v.safetensors", result["models"])
        self.assertFalse(result["generation_ready"])
        self.assertFalse(result["workflow_execution_verified"])

    def test_unrelated_nodes_are_not_misclassified(self):
        result = assess_wan_vace_nodes({"KSampler": {}, "SaveImage": {}})
        self.assertEqual(result["node_count"], 0)
        self.assertFalse(result["generation_ready"])


if __name__ == "__main__":
    unittest.main()
