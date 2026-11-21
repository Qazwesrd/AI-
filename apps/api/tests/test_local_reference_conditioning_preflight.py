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


if __name__ == "__main__":
    unittest.main()
