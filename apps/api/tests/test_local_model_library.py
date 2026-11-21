import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from app.routers.local_models import (
    ModelReviewInput,
    _compatibility,
    _read_catalog,
    _write_catalog,
    list_local_models,
    save_model_review,
)


class LocalModelLibraryTests(unittest.TestCase):
    def test_compatibility_prefers_gpu_when_file_fits_vram(self):
        result = _compatibility(
            4 * 1024 ** 3,
            {"memory": {"total_bytes": 16 * 1024 ** 3},
             "gpu": {"vram_bytes": 8 * 1024 ** 3}},
        )
        self.assertEqual(result["status"], "gpu_candidate")

    def test_compatibility_warns_when_ram_is_too_small(self):
        result = _compatibility(
            8 * 1024 ** 3,
            {"memory": {"total_bytes": 8 * 1024 ** 3},
             "gpu": {"vram_bytes": None}},
        )
        self.assertEqual(result["status"], "insufficient_memory")
        self.assertIn("粗略初筛", result["note"])

    def test_catalog_round_trip_uses_atomic_local_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir) / "models"
            path = directory / ".model-library.json"
            with patch("app.routers.local_models.MODEL_DIR", directory), patch(
                "app.routers.local_models.CATALOG_PATH", path
            ):
                _write_catalog({"sample.gguf": {"license_review": "checked_by_user"}})
                self.assertEqual(
                    _read_catalog()["sample.gguf"]["license_review"],
                    "checked_by_user",
                )

    def test_review_rejects_path_traversal(self):
        with self.assertRaises(HTTPException) as caught:
            save_model_review(ModelReviewInput(filename="../outside.gguf"))
        self.assertEqual(caught.exception.status_code, 400)

    def test_review_only_accepts_files_in_model_directory(self):
        payload = ModelReviewInput(
            filename="story-model.gguf",
            license_review="checked_by_user",
            license_name="用户填写的许可证名称",
            notes="已人工阅读相关授权条款",
        )
        with patch(
            "app.routers.local_models._scan_local_model_files",
            return_value={"files": [{"name": "story-model.gguf"}]},
        ), patch(
            "app.routers.local_models._read_catalog", return_value={}
        ), patch("app.routers.local_models._write_catalog") as write, patch(
            "app.routers.local_models._catalog_items",
            return_value={"items": [{"name": "story-model.gguf", "license_review": "checked_by_user"}]},
        ):
            result = save_model_review(payload)
        self.assertEqual(result["license_review"], "checked_by_user")
        write.assert_called_once()
        self.assertEqual(write.call_args.args[0]["story-model.gguf"]["license_name"], "用户填写的许可证名称")

    def test_list_marks_license_as_unverified_by_default(self):
        with patch(
            "app.routers.local_models._scan_local_model_files",
            return_value={"files": [{"name": "story-model.gguf", "size_bytes": 100}]},
        ), patch(
            "app.routers.local_models._read_catalog", return_value={}
        ), patch(
            "app.routers.local_models._get_hardware_profile",
            return_value={"memory": {"total_bytes": 1024}, "gpu": {"vram_bytes": None}},
        ):
            result = list_local_models()
        self.assertEqual(result["items"][0]["license_review"], "unverified")
        self.assertIn("未核验", result["items"][0]["license_notice"])


if __name__ == "__main__":
    unittest.main()
