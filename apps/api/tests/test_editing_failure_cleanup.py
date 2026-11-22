import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import editing_service
from app.services.media_service import FFmpegCancelledError


class EditingFailureCleanupTests(unittest.TestCase):
    def _run_failure(self, error):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            video_dir = root / "videos"
            edit_dir = root / "edit-jobs"
            video_dir.mkdir()
            edit_dir.mkdir()
            final_output = []

            def fake_ffmpeg(args, **kwargs):
                target = Path(args[-1])
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"x" * 2048)
                if target.name.endswith(".partial.mp4"):
                    final_output.append(target)
                    raise error

            plan = {
                "segments": [{
                    "file_path": str(root / "source.mp4"),
                    "start": 0,
                    "end": 5,
                }],
                "width": 1280,
                "height": 720,
            }
            with patch.object(editing_service, "VIDEO_DIR", video_dir), \
                 patch.object(editing_service, "EDIT_DIR", edit_dir), \
                 patch.object(editing_service, "safe_storage_file", side_effect=lambda value: Path(value)), \
                 patch.object(editing_service, "run_ffmpeg_with_progress", side_effect=fake_ffmpeg), \
                 patch.object(editing_service, "probe_media", return_value={"streams": [{"codec_type": "video"}]}):
                with self.assertRaises(type(error)) as raised:
                    editing_service.execute_edit_plan(plan)

            self.assertEqual(len(final_output), 1)
            self.assertFalse(final_output[0].exists())
            self.assertEqual(list(video_dir.iterdir()), [])
            self.assertEqual(list(edit_dir.iterdir()), [])
            if isinstance(error, FFmpegCancelledError):
                self.assertIsNone(raised.exception.partial_output)
                self.assertIsNone(raised.exception.work_directory)

    def test_failed_render_removes_partial_output_and_work_files(self):
        self._run_failure(RuntimeError("simulated FFmpeg failure"))

    def test_cancelled_render_removes_partial_output_and_work_files(self):
        self._run_failure(FFmpegCancelledError("simulated cancellation"))


if __name__ == "__main__":
    unittest.main()
