import unittest
from unittest.mock import patch

from fastapi import HTTPException
from app.routers.local_image_generation import (
    LocalImageGenerateInput, _checkpoint_names, build_txt2img_workflow,
    generate_local_image, get_local_image_task, LocalImageArchiveInput,
    _safe_output_image, _download_comfyui_output, archive_local_image_task_output,
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


    def test_archive_rejects_path_traversal_and_non_output_files(self):
        for item in (
            {"filename": "../secret.png", "subfolder": "", "type": "output"},
            {"filename": "safe.png", "subfolder": "../secret", "type": "output"},
            {"filename": "safe.png", "subfolder": "folder\\escape", "type": "output"},
            {"filename": "safe.png", "subfolder": "", "type": "temp"},
        ):
            with self.subTest(item=item), self.assertRaises(HTTPException):
                _safe_output_image(item)

    def test_archive_download_checks_image_magic_bytes(self):
        png = b"\x89PNG\r\n\x1a\n" + b"fake-image-payload"
        with patch("app.routers.local_image_generation.OPENER.open") as opened:
            class Response:
                def __enter__(self): return self
                def __exit__(self, *args): return False
                def read(self, limit): return png
            opened.return_value = Response()
            raw, mime, suffix = _download_comfyui_output("frame.png", "")
        self.assertEqual(raw, png)
        self.assertEqual(mime, "image/png")
        self.assertEqual(suffix, ".png")

    def test_archive_rejects_output_not_owned_by_task(self):
        prompt_id = "12345678-1234-1234-1234-123456789abc"
        payload = LocalImageArchiveInput(filename="other.png")
        history = {prompt_id: {
            "status": {"completed": True},
            "outputs": {"9": {"images": [{"filename": "actual.png", "subfolder": "", "type": "output"}]}},
        }}
        with (
            patch("app.routers.local_image_generation._local_request", return_value=history),
            patch("app.routers.local_image_generation._download_comfyui_output") as download,
        ):
            with self.assertRaises(HTTPException) as caught:
                archive_local_image_task_output(prompt_id, payload, db=None)
        self.assertEqual(caught.exception.status_code, 404)
        download.assert_not_called()


if __name__ == "__main__":
    unittest.main()
