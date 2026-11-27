import unittest
from unittest.mock import patch

from app.routers.local_video_runtime import assess_wan_vace_nodes


class LocalVideoRuntimeTests(unittest.TestCase):
    def test_missing_runtime_does_not_claim_generation_ready(self):
        result = assess_wan_vace_nodes(None)
        self.assertEqual(result["status"], "unavailable")
        self.assertFalse(result["generation_ready"])
        self.assertFalse(result["workflow_node_set_complete"])
        self.assertGreater(len(result["missing_required_nodes"]), 0)

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
        self.assertTrue(any(item["name"] == "wan2.1_i2v.safetensors" and item["category"] == "video_model" for item in result["model_inventory"]))
        self.assertTrue(all(item["load_tested"] is False for item in result["model_inventory"]))
        self.assertFalse(result["generation_ready"])
        self.assertFalse(result["workflow_execution_verified"])
        self.assertFalse(result["workflow_node_set_complete"])


    def test_full_required_node_set_is_reported_without_claiming_runtime_ready(self):
        from app.routers.local_video_runtime import REQUIRED_VACE_WORKFLOW_NODES
        info = {node_name: {} for node_name in REQUIRED_VACE_WORKFLOW_NODES.values()}
        result = assess_wan_vace_nodes(info)
        self.assertTrue(result["workflow_node_set_complete"])
        self.assertEqual(result["missing_required_nodes"], [])
        self.assertTrue(all(result["required_nodes"].values()))
        self.assertFalse(result["generation_ready"])
        self.assertFalse(result["workflow_execution_verified"])

    def test_reports_actual_required_node_input_schema_and_dropdown_choices(self):
        from app.routers.local_video_runtime import REQUIRED_VACE_WORKFLOW_NODES
        info = {
            node_name: {"input": {"required": {"model_name": [["wan-vae.safetensors", "other.safetensors"]]}}}
            for node_name in REQUIRED_VACE_WORKFLOW_NODES.values()
        }
        result = assess_wan_vace_nodes(info)
        loader = next(node for node in result["nodes"] if node["node_type"] == "WanVideoModelLoader")
        self.assertEqual(
            loader["input_schema"]["required"]["model_name"]["choices"],
            ["wan-vae.safetensors", "other.safetensors"],
        )
        self.assertEqual(loader["input_schema"]["required"]["model_name"]["type"], "COMBO")
        self.assertFalse(result["generation_ready"])

    def test_scans_actual_local_video_model_directories(self):
        from app.routers import local_video_runtime as runtime
        def fake_local_json(path, opener=None, timeout=4.0):
            return {
                "/models": ["diffusion_models", "vae", "text_encoders"],
                "/models/diffusion_models": ["wan2.1-vace.safetensors"],
                "/models/vae": ["wan_vae.safetensors"],
                "/models/text_encoders": ["umt5_xxl_fp8.safetensors"],
            }.get(path)
        with patch.object(runtime, "_local_json", side_effect=fake_local_json):
            result = runtime._scan_video_model_folders()
        self.assertTrue(result["available"])
        self.assertEqual(result["folders"]["diffusion_models"]["count"], 1)
        self.assertEqual(
            {(item["name"], item["category"]) for item in result["items"]},
            {
                ("wan2.1-vace.safetensors", "video_model"),
                ("wan_vae.safetensors", "video_vae"),
                ("umt5_xxl_fp8.safetensors", "video_text_encoder"),
            },
        )
        self.assertTrue(all(item["load_tested"] is False for item in result["items"]))

    def test_unrelated_nodes_are_not_misclassified(self):
        result = assess_wan_vace_nodes({"KSampler": {}, "SaveImage": {}})
        self.assertEqual(result["node_count"], 0)
        self.assertFalse(result["generation_ready"])


    def test_vace_input_preflight_checks_live_inputs_and_model_choices(self):
        from app.routers.local_video_runtime import validate_vace_node_input_requirements
        info = {
            "WanVideoModelLoader": {
                "input": {"required": {
                    "model": [["wan2.1-vace.safetensors", "other.safetensors"]],
                    "precision": ["STRING", {"default": "fp16"}],
                }}
            }
        }
        result = validate_vace_node_input_requirements(info, {
            "WanVideoModelLoader": {
                "model": {"choice": "wan2.1-vace.safetensors"},
                "precision": {},
                "missing_field": {},
            },
            "MissingNode": {"model": {}},
        })
        self.assertFalse(result["valid"])
        self.assertEqual(result["missing_nodes"], ["MissingNode"])
        self.assertEqual(result["missing_inputs"], [
            {"node": "WanVideoModelLoader", "input": "missing_field"}
        ])
        self.assertEqual(result["unavailable_choices"], [])

    def test_vace_input_preflight_rejects_uninstalled_model_choice(self):
        from app.routers.local_video_runtime import validate_vace_node_input_requirements
        info = {
            "WanVideoModelLoader": {
                "input": {"required": {"model": [["installed.safetensors"]]}}
            }
        }
        result = validate_vace_node_input_requirements(info, {
            "WanVideoModelLoader": {"model": {"choice": "not-installed.safetensors"}}
        })
        self.assertFalse(result["valid"])
        self.assertEqual(result["unavailable_choices"][0]["choice"], "not-installed.safetensors")


    def test_api_workflow_preflight_accepts_installed_wan_video_graph(self):
        from app.routers.local_video_runtime import validate_local_video_workflow
        info = {
            "WanVideoModelLoader": {"input": {"required": {"model": [["wan-vace.safetensors"]]}}},
            "VHS_VideoCombine": {"input": {"required": {"images": ["IMAGE"], "frame_rate": ["FLOAT"]}}},
        }
        workflow = {
            "1": {"class_type": "WanVideoModelLoader", "inputs": {"model": "wan-vace.safetensors"}},
            "2": {"class_type": "VHS_VideoCombine", "inputs": {"images": ["1", 0], "frame_rate": 24}},
        }
        result = validate_local_video_workflow(workflow, info)
        self.assertTrue(result["valid"])
        self.assertTrue(result["has_wan_video_nodes"])
        self.assertTrue(result["has_video_output_node"])
        self.assertEqual(result["node_count"], 2)

    def test_api_workflow_preflight_rejects_missing_node_and_bad_model_choice(self):
        from app.routers.local_video_runtime import validate_local_video_workflow
        info = {
            "WanVideoModelLoader": {"input": {"required": {"model": [["installed.safetensors"]]}}},
            "VHS_VideoCombine": {"input": {"required": {"images": ["IMAGE"]}}},
        }
        workflow = {
            "1": {"class_type": "WanVideoModelLoader", "inputs": {"model": "missing.safetensors"}},
            "2": {"class_type": "VHS_VideoCombine", "inputs": {"images": ["99", 0]}},
        }
        result = validate_local_video_workflow(workflow, info)
        self.assertFalse(result["valid"])
        self.assertTrue(any("不在本机可用选项" in error for error in result["errors"]))
        self.assertTrue(any("引用了不存在的节点" in error for error in result["errors"]))

    def test_api_workflow_preflight_rejects_graph_without_video_output(self):
        from app.routers.local_video_runtime import validate_local_video_workflow
        info = {"WanVideoSampler": {"input": {"required": {"steps": ["INT"]}}}}
        result = validate_local_video_workflow(
            {"1": {"class_type": "WanVideoSampler", "inputs": {"steps": 20}}}, info
        )
        self.assertFalse(result["valid"])
        self.assertTrue(any("视频输出节点" in error for error in result["errors"]))

    def test_api_workflow_preflight_rejects_unbounded_graph(self):
        from app.routers.local_video_runtime import MAX_WORKFLOW_NODES, validate_local_video_workflow
        workflow = {str(i): {"class_type": "WanVideoSampler", "inputs": {}} for i in range(MAX_WORKFLOW_NODES + 1)}
        result = validate_local_video_workflow(workflow, {})
        self.assertFalse(result["valid"])
        self.assertIn("安全上限", result["errors"][0])


if __name__ == "__main__":
    unittest.main()
