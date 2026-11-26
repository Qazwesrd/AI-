import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app.services.local_vace import _runtime, request_cancel_local_vace


class LocalVaceRuntimeTests(unittest.TestCase):
    def _make_runtime(self, root):
        engines = Path(root) / "engines"
        cli = engines / "StableDiffusionCpp" / "bin" / "sd-cli.exe"
        cli.parent.mkdir(parents=True)
        cli.write_bytes(b"test-executable")
        models = engines / "VACE" / "models"
        models.mkdir(parents=True)
        for filename in (
            "wan2.1-v3-vace-1.3b-q4_0.gguf",
            "umt5-xxl-encoder-q4_k_m.gguf",
            "pig_wan_vae_fp32-f16.gguf",
        ):
            (models / filename).write_bytes(b"model")
        return cli, models

    def test_runtime_resolves_only_the_bundled_engine_and_weights(self):
        with tempfile.TemporaryDirectory() as root:
            expected_cli, expected_models = self._make_runtime(root)
            with patch.dict(os.environ, {"APP_DATA_DIR": root}):
                cli, models = _runtime()
            self.assertEqual(cli, expected_cli.resolve())
            self.assertEqual(models["diffusion"].parent, expected_models.resolve())

    def test_runtime_fails_closed_when_a_required_model_is_missing(self):
        with tempfile.TemporaryDirectory() as root:
            engines = Path(root) / "engines"
            cli = engines / "StableDiffusionCpp" / "sd-cli.exe"
            cli.parent.mkdir(parents=True)
            cli.write_bytes(b"test")
            models = engines / "VACE" / "models"
            models.mkdir(parents=True)
            with patch.dict(os.environ, {"APP_DATA_DIR": root}):
                with self.assertRaises(FileNotFoundError):
                    _runtime()

    def test_cancel_queued_task_marks_it_cancelled_without_spawning(self):
        task = Mock(task_type="local_vace_video", status="queued", request_data={"stage": "queued"})
        db = Mock()
        result = request_cancel_local_vace(db, task)
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(task.status, "cancelled")
        self.assertEqual(task.request_data["stage"], "cancelled")
        db.commit.assert_called_once()

    def test_cancel_running_task_sets_cancel_request_for_worker(self):
        task = Mock(task_type="local_vace_video", status="running", request_data={"stage": "generating"})
        db = Mock()
        result = request_cancel_local_vace(db, task)
        self.assertEqual(result["status"], "cancel_requested")
        self.assertEqual(task.status, "cancel_requested")
        db.commit.assert_called_once()

    def test_cancel_completed_task_is_idempotent(self):
        task = Mock(task_type="local_vace_video", status="completed", request_data={})
        db = Mock()
        result = request_cancel_local_vace(db, task)
        self.assertEqual(result["status"], "completed")
        db.commit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
