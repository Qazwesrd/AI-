import unittest
from unittest.mock import patch

from fastapi import HTTPException
from app.routers.local_image_generation import (
    LocalImageGenerateInput, _checkpoint_names, build_txt2img_workflow,
    generate_local_image, get_local_image_task,
)


class LocalImageGenerationTests(unittest.TestCase):
    def test_checkpoint_allowlist_parses_installed_models(self):
        data = {"CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [["model.safetensors", "../bad", "中文模型"]]}}}}
        self.assertEqual(_checkpoint_names(data), ["model.safetensors"])

    def test_workflow_has_expected_nodes_and_connections(self):
        payload = LocalImageGenerateInput(checkpoint="model.safetensors", prompt="一位少年站在山巅", confirm_local_generation=True)
        workflow = build_txt2img_workflow(payload, 123)
        self.assertEqual(workflow["4"]["class_type"], "CheckpointLoaderSimple")
        self.assertEqual(workflow["3"]["inputs"]["seed"], 123)
        self.assertEqual(workflow["8"]["inputs"]["samples"], ["3", 0])
        self.assertEqual(workflow["9"]["class_type"], "SaveImage")

    def test_generation_requires_explicit_confirmation(self):
        payload = LocalImageGenerateInput(checkpoint="model.safetensors", prompt="A cinematic mountain", confirm_local_generation=False)
        with self.assertRaises(HTTPException) as caught:
            generate_local_image(payload)
        self.assertEqual(caught.exception.status_code, 400)

    def test_generation_refuses_uninstalled_checkpoint(self):
        payload = LocalImageGenerateInput(checkpoint="missing.safetensors", prompt="A cinematic mountain", confirm_local_generation=True)
        with patch("app.routers.local_image_generation._local_request", return_value={"CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [["other.safetensors"]]}}}}):
            with self.assertRaises(HTTPException) as caught:
                generate_local_image(payload)
        self.assertEqual(caught.exception.status_code, 404)

    def test_generation_queues_only_to_local_comfyui(self):
        payload = LocalImageGenerateInput(checkpoint="model.safetensors", prompt="A cinematic mountain", confirm_local_generation=True)
        with patch("app.routers.local_image_generation._local_request", side_effect=[
            {"CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [["model.safetensors"]]}}}},
            {"prompt_id": "12345678-1234-1234-1234-123456789abc"},
        ]) as request:
            result = generate_local_image(payload)
        self.assertTrue(result["local_only"])
        self.assertEqual(result["status"], "queued")
        self.assertEqual(request.call_args_list[1].args[0], "/prompt")

    def test_generation_rejects_dimensions_not_multiple_of_64_before_local_call(self):
        payload = LocalImageGenerateInput(checkpoint="model.safetensors", prompt="A cinematic mountain", width=700, confirm_local_generation=True)
        with patch("app.routers.local_image_generation._local_request") as request:
            with self.assertRaises(HTTPException) as caught:
                generate_local_image(payload)
        self.assertEqual(caught.exception.status_code, 400)
        request.assert_not_called()

    def test_checkpoint_path_traversal_is_rejected(self):
        with self.assertRaises(ValueError):
            LocalImageGenerateInput(checkpoint="../outside.safetensors", prompt="A cinematic mountain")

    def test_status_rejects_invalid_task_id(self):
        with self.assertRaises(HTTPException) as caught:
            get_local_image_task("../etc/passwd")
        self.assertEqual(caught.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
