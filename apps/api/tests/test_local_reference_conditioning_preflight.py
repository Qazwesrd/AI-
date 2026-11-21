import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.routers import local_image_generation as local_images
from app.routers.local_image_generation import LocalImageGenerateInput


class ReferenceMediaFileValidationTests(unittest.TestCase):
    def test_accepts_png_inside_managed_storage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "reference.png"
            image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"test")
            media = SimpleNamespace(file_path=str(image), mime_type="image/png")
            with patch.object(local_images, "STORAGE", root):
                result = local_images._inspect_reference_media_file(media)
        self.assertTrue(result["ready"])
        self.assertEqual(result["mime_type"], "image/png")

    def test_rejects_path_outside_managed_storage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "storage"
            root.mkdir()
            outside = Path(directory) / "outside.png"
            outside.write_bytes(b"\x89PNG\r\n\x1a\n" + b"test")
            media = SimpleNamespace(file_path=str(outside), mime_type="image/png")
            with patch.object(local_images, "STORAGE", root):
                result = local_images._inspect_reference_media_file(media)
        self.assertFalse(result["ready"])
        self.assertIn("目录", result["reason"])

    def test_rejects_mime_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "reference.png"
            image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"test")
            media = SimpleNamespace(file_path=str(image), mime_type="image/jpeg")
            with patch.object(local_images, "STORAGE", root):
                result = local_images._inspect_reference_media_file(media)
        self.assertFalse(result["ready"])
        self.assertIn("MIME", result["reason"])

    def test_rejects_oversized_image(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "reference.png"
            image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"test")
            media = SimpleNamespace(file_path=str(image), mime_type="image/png")
            with patch.object(local_images, "STORAGE", root), patch.object(local_images, "MAX_IMAGE_BYTES", 4):
                result = local_images._inspect_reference_media_file(media)
        self.assertFalse(result["ready"])
        self.assertIn("25 MiB", result["reason"])


    def test_upload_uses_fixed_local_endpoint_and_multipart_body(self):
        class Response:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self, size=-1):
                return b'{"name":"reference.png","subfolder":"ai_comic_references/p1","type":"input"}'

        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "reference.png"
            image.write_bytes(b"png-payload")
            media = SimpleNamespace(file_path=str(image))
            captured = {}
            def capture(request, timeout):
                captured["url"] = request.full_url
                captured["body"] = request.data
                captured["content_type"] = request.get_header("Content-type")
                return Response()
            with patch.object(local_images, "_inspect_reference_media_file", return_value={
                "ready": True, "mime_type": "image/png", "size": 11,
            }), patch.object(local_images, "OPENER") as opener:
                opener.open.side_effect = capture
                result = local_images._upload_reference_to_local_comfyui(media, 1, "characters", 2)
        self.assertEqual(captured["url"], "http://127.0.0.1:8188/upload/image")
        self.assertIn(b'Content-Disposition: form-data; name="image"', captured["body"])
        self.assertIn(b"png-payload", captured["body"])
        self.assertIn(b"\r\n", captured["body"])
        self.assertEqual(result["name"], "reference.png")


class ReferenceConditionedWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.payload = LocalImageGenerateInput(
            checkpoint="comic-model.safetensors",
            prompt="A character standing in a city street",
            negative_prompt="blurry",
            style="国漫",
            confirm_local_generation=True,
        )
        self.object_infos = {
            "LoadImage": {"input": {"required": {"image": ["STRING", {"image_upload": True}]}}},
            "IPAdapterUnifiedLoader": {
                "input": {"required": {
                    "model": ["MODEL"],
                    "preset": [["STANDARD (medium strength)", "VIT-G"]],
                }}
            },
            "IPAdapterAdvanced": {
                "input": {"required": {
                    "model": ["MODEL"],
                    "ipadapter": ["IPADAPTER"],
                    "image": ["IMAGE"],
                    "weight": ["FLOAT", {"default": 0.65}],
                    "weight_type": [["linear", "ease in-out"]],
                    "combine_embeds": [["concat", "add"]],
                    "start_at": ["FLOAT", {"default": 0.0}],
                    "end_at": ["FLOAT", {"default": 1.0}],
                    "embeds_scaling": [["V only", "K+V"]],
                }}
            },
        }

    def test_builds_graph_from_unified_loader_schema(self):
        workflow = local_images.build_reference_conditioned_workflow(
            self.payload, 123, self.object_infos,
            [{"name": "ref.png", "subfolder": "ai_comic_references/p1", "type": "input"}],
        )
        self.assertEqual(workflow["10"]["class_type"], "IPAdapterUnifiedLoader")
        self.assertEqual(workflow["10"]["inputs"]["preset"], "STANDARD (medium strength)")
        self.assertEqual(workflow["12"]["class_type"], "LoadImage")
        self.assertEqual(workflow["13"]["class_type"], "IPAdapterAdvanced")
        self.assertEqual(workflow["13"]["inputs"]["model"], ["10", 0])
        self.assertEqual(workflow["13"]["inputs"]["ipadapter"], ["10", 1])
        self.assertEqual(workflow["3"]["inputs"]["model"], ["13", 0])

    def test_rejects_reference_path_traversal(self):
        with self.assertRaises(ValueError):
            local_images.build_reference_conditioned_workflow(
                self.payload, 123, self.object_infos,
                [{"name": "ref.png", "subfolder": "../outside", "type": "input"}],
            )

    def test_rejects_more_than_ten_references(self):
        refs = [{"name": f"ref-{i}.png", "subfolder": "refs", "type": "input"} for i in range(11)]
        with self.assertRaises(ValueError):
            local_images.build_reference_conditioned_workflow(self.payload, 123, self.object_infos, refs)


if __name__ == "__main__":
    unittest.main()
