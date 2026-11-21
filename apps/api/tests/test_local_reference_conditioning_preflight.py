import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.routers import local_image_generation as local_images


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


if __name__ == "__main__":
    unittest.main()
