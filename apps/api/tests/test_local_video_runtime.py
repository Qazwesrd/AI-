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
            "WanVideoModelLoader": {
                "input": {"required": {"model": [["wan-vace.safetensors"]]}},
                "output": ["WANVIDEOMODEL"],
            },
            "WanVideoSampler": {
                "input": {"required": {"model": ["WANVIDEOMODEL"]}},
                "output": ["IMAGE"],
            },
            "VHS_VideoCombine": {"input": {"required": {"images": ["IMAGE"], "frame_rate": ["FLOAT"]}}},
        }
        workflow = {
            "1": {"class_type": "WanVideoModelLoader", "inputs": {"model": "wan-vace.safetensors"}},
            "3": {"class_type": "WanVideoSampler", "inputs": {"model": ["1", 0]}},
            "2": {"class_type": "VHS_VideoCombine", "inputs": {"images": ["3", 0], "frame_rate": 24}},
        }
        result = validate_local_video_workflow(workflow, info)
        self.assertTrue(result["valid"])
        self.assertTrue(result["has_wan_video_nodes"])
        self.assertTrue(result["has_video_output_node"])

    def test_api_workflow_preflight_rejects_disconnected_generation_chain(self):
        from app.routers.local_video_runtime import validate_local_video_workflow
        info = {
            "WanVideoModelLoader": {
                "input": {"required": {"model": [["installed.safetensors"]]}},
                "output": ["WANVIDEOMODEL"],
            },
            "WanVideoSampler": {
                "input": {"required": {"model": ["WANVIDEOMODEL"]}},
                "output": ["IMAGE"],
            },
            "VHS_VideoCombine": {"input": {"required": {"images": ["IMAGE"]}}},
        }
        workflow = {
            "1": {"class_type": "WanVideoModelLoader", "inputs": {"model": "installed.safetensors"}},
            "2": {"class_type": "WanVideoSampler", "inputs": {"model": ["1", 0]}},
            "3": {"class_type": "VHS_VideoCombine", "inputs": {"images": ["1", 0]}},
        }
        result = validate_local_video_workflow(workflow, info)
        self.assertFalse(result["valid"])
        self.assertFalse(result["has_connected_generation_chain"])
        self.assertTrue(any("未形成连续连通链路" in error for error in result["errors"]))

    def test_api_workflow_preflight_rejects_invalid_output_port(self):
        from app.routers.local_video_runtime import validate_local_video_workflow
        info = {
            "WanVideoModelLoader": {
                "input": {"required": {"model": [["installed.safetensors"]]}},
                "output": ["IMAGE"],
            },
            "VHS_VideoCombine": {"input": {"required": {"images": ["IMAGE"]}}},
        }
        result = validate_local_video_workflow({
            "1": {"class_type": "WanVideoModelLoader", "inputs": {"model": "installed.safetensors"}},
            "2": {"class_type": "VHS_VideoCombine", "inputs": {"images": ["1", 4]}},
        }, info)
        self.assertFalse(result["valid"])
        self.assertTrue(any("不存在的输出端口" in error for error in result["errors"]))

    def test_api_workflow_preflight_rejects_mismatched_socket_types(self):
        from app.routers.local_video_runtime import validate_local_video_workflow
        info = {
            "WanVideoModelLoader": {
                "input": {"required": {"model": [["installed.safetensors"]]}},
                "output": ["WANVIDEOMODEL"],
            },
            "VHS_VideoCombine": {"input": {"required": {"images": ["IMAGE"]}}},
        }
        result = validate_local_video_workflow({
            "1": {"class_type": "WanVideoModelLoader", "inputs": {"model": "installed.safetensors"}},
            "2": {"class_type": "VHS_VideoCombine", "inputs": {"images": ["1", 0]}},
        }, info)
        self.assertFalse(result["valid"])
        self.assertTrue(any("类型 IMAGE" in error and "WANVIDEOMODEL" in error for error in result["errors"]))

    def test_api_workflow_preflight_rejects_missing_model_and_link(self):
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

    def test_api_workflow_preflight_requires_connected_video_output(self):
        from app.routers.local_video_runtime import validate_local_video_workflow
        info = {
            "WanVideoSampler": {"input": {"required": {"steps": ["INT"]}}, "output": ["IMAGE"]},
            "VHS_VideoCombine": {"input": {"required": {"images": ["IMAGE"]}}},
        }
        result = validate_local_video_workflow({
            "1": {"class_type": "WanVideoSampler", "inputs": {"steps": 20}},
            "2": {"class_type": "VHS_VideoCombine", "inputs": {"images": "unconnected"}},
        }, info)
        self.assertFalse(result["valid"])
        self.assertTrue(any("没有连接图像帧/视频输入" in error for error in result["errors"]))

    def test_api_workflow_preflight_requires_wan_sampler_or_vace_processing(self):
        from app.routers.local_video_runtime import validate_local_video_workflow
        info = {
            "WanVideoModelLoader": {"input": {"required": {"model": [["installed.safetensors"]]}}, "output": ["IMAGE"]},
            "VHS_VideoCombine": {"input": {"required": {"images": ["IMAGE"]}}},
        }
        result = validate_local_video_workflow({
            "1": {"class_type": "WanVideoModelLoader", "inputs": {"model": "installed.safetensors"}},
            "2": {"class_type": "VHS_VideoCombine", "inputs": {"images": ["1", 0]}},
        }, info)
        self.assertFalse(result["valid"])
        self.assertTrue(any("Wan 视频采样或 VACE 处理节点" in error for error in result["errors"]))

    def test_api_workflow_preflight_requires_video_output(self):
        from app.routers.local_video_runtime import validate_local_video_workflow
        info = {"WanVideoSampler": {"input": {"required": {"steps": ["INT"]}}}}
        result = validate_local_video_workflow(
            {"1": {"class_type": "WanVideoSampler", "inputs": {"steps": 20}}}, info
        )
        self.assertFalse(result["valid"])
        self.assertTrue(any("视频输出节点" in error for error in result["errors"]))


    def test_template_capabilities_discovers_installed_roles_and_input_choices(self):
        from app.routers.local_video_runtime import build_video_template_capabilities
        info = {
            "WanVideoModelLoader": {"input": {"required": {"model": [["wan-t2v.safetensors", "wan-vace.safetensors"]]}}},
            "WanVideoSampler": {"input": {"required": {"steps": ["INT", {"default": 12}]}}},
            "VHS_VideoCombine": {"input": {"required": {"frame_rate": ["FLOAT"]}}},
            "LoadImage": {"input": {"required": {"image": ["STRING"]}}},
        }
        result = build_video_template_capabilities(info)
        self.assertTrue(result["available"])
        self.assertIn("WanVideoModelLoader", result["roles"]["model_loader"])
        self.assertIn("WanVideoSampler", result["roles"]["sampler"])
        self.assertIn("VHS_VideoCombine", result["roles"]["video_output"])
        loader = next(node for node in result["nodes"] if node["class_type"] == "WanVideoModelLoader")
        self.assertEqual(loader["input_schema"]["required"]["model"]["choices"], ["wan-t2v.safetensors", "wan-vace.safetensors"])
        self.assertFalse(result["template_generated"])
        self.assertFalse(result["generation_ready"])

    def test_template_capabilities_exposes_typed_socket_candidates_without_auto_linking(self):
        from app.routers.local_video_runtime import build_video_template_capabilities
        info = {
            "WanVideoModelLoader": {
                "input": {"required": {"model": [["wan-t2v.safetensors"]]}},
                "output": ["WANVIDEOMODEL"],
                "output_name": ["model"],
            },
            "WanVideoSampler": {
                "input": {"required": {"model": ["WANVIDEOMODEL"]}},
                "output": ["LATENT"],
                "output_name": ["samples"],
            },
            "VHS_VideoCombine": {
                "input": {"required": {"images": ["IMAGE"]}},
                "output": [],
            },
        }
        result = build_video_template_capabilities(info)
        self.assertTrue(result["available"])
        self.assertEqual(
            result["connection_candidates"],
            [{
                "target_class": "WanVideoSampler",
                "input": "model",
                "input_type": "WANVIDEOMODEL",
                "sources": [{
                    "class_type": "WanVideoModelLoader",
                    "output_index": 0,
                    "output_name": "model",
                }],
            }],
        )
        self.assertFalse(result["template_generated"])
        self.assertFalse(result["generation_ready"])

    def test_template_assessment_reports_missing_wan_and_vace_roles(self):
        from app.routers.local_video_runtime import build_video_template_capabilities
        info = {
            "WanVideoModelLoader": {"input": {"required": {"model": [["wan-t2v.safetensors"]]}}},
            "WanVideoSampler": {"input": {"required": {"steps": ["INT"]}}},
            "VHS_VideoCombine": {"input": {"required": {"images": ["IMAGE"]}}},
        }
        result = build_video_template_capabilities(info)
        self.assertTrue(result["available"])
        self.assertIn("text_conditioning", result["template_assessment"]["wan_t2v_role_coverage"]["missing_roles"])
        self.assertIn("vace_processing", result["template_assessment"]["vace_role_coverage"]["missing_roles"])
        self.assertFalse(result["template_assessment"]["wan_t2v_role_coverage"]["all_role_groups_found"])
        self.assertFalse(result["template_generated"])
        self.assertFalse(result["generation_ready"])

    def test_template_capabilities_does_not_guess_when_nodes_are_missing(self):
        from app.routers.local_video_runtime import build_video_template_capabilities
        result = build_video_template_capabilities({"KSampler": {}, "SaveImage": {}})
        self.assertFalse(result["available"])
        self.assertEqual(result["roles"], {})
        self.assertFalse(result["template_generated"])


    def test_video_preview_rejects_path_traversal_metadata(self):
        from fastapi import HTTPException
        from app.routers import local_video_runtime as runtime
        prompt_id = "a" * 32
        history = {
            prompt_id: {
                "outputs": {
                    "1": {"videos": [{"filename": "../outside.mp4", "subfolder": "", "type": "output"}]}
                }
            }
        }
        with patch.object(runtime, "_comfy_request", return_value=history):
            with self.assertRaises(HTTPException) as caught:
                runtime.preview_video_workflow_output(prompt_id)
        self.assertEqual(caught.exception.status_code, 422)

    def test_video_preview_rejects_parent_directory_subfolder(self):
        from fastapi import HTTPException
        from app.routers import local_video_runtime as runtime
        prompt_id = "b" * 32
        history = {
            prompt_id: {
                "outputs": {
                    "1": {"videos": [{"filename": "render.mp4", "subfolder": "safe/../../outside", "type": "output"}]}
                }
            }
        }
        with patch.object(runtime, "_comfy_request", return_value=history):
            with self.assertRaises(HTTPException) as caught:
                runtime.preview_video_workflow_output(prompt_id)
        self.assertEqual(caught.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
