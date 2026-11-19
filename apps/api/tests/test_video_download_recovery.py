import tempfile
import unittest
from pathlib import Path
from fastapi import HTTPException
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.routers.media import refresh_video_task


class VideoDownloadRecoveryTests(unittest.TestCase):
    def _task(self, result_path=None):
        return SimpleNamespace(
            id=81, task_type="storyboard_video", status="submitted",
            remote_task_id="remote-81", provider="provider-x",
            request_data={"status_path_template": "/videos/{task_id}",
                          "status_field": "status", "video_url_field": "video_url",
                          "confirm_paid": False},
            result_data=None, result_path=result_path, error=None,
        )

    @patch("app.routers.media.get_provider", return_value=(SimpleNamespace(), "key", "https://provider.invalid"))
    @patch("app.routers.media.http_json", return_value={"status": "completed", "video_url": "https://cdn.invalid/video.mp4"})
    @patch("app.routers.media.http_download")
    @patch("app.routers.media.validate_rendered_video")
    @patch("app.routers.media.VIDEO_DIR")
    def test_invalid_download_is_not_registered_and_can_be_retried(
        self, video_dir, validate, download, _http_json, _provider
    ):
        with tempfile.TemporaryDirectory() as folder:
            video_dir.__truediv__ = lambda _self, name: Path(folder) / name
            def write_partial(_url, destination):
                Path(destination).write_bytes(b"partial")
            download.side_effect = write_partial
            validate.side_effect = ValueError("FFprobe 无法确认视频时长")
            task = self._task()
            db = Mock()
            db.query.return_value.filter.return_value.first.return_value = task

            result = refresh_video_task(81, db)

            self.assertEqual(result["status"], "download_failed")
            self.assertTrue(result["retry_download_only"])
            self.assertIsNone(task.result_path)
            self.assertFalse(any(Path(folder).iterdir()))
            db.add.assert_not_called()
            self.assertIn("不会重新提交视频生成请求", task.error)

    @patch("app.routers.media.get_provider", return_value=(SimpleNamespace(), "key", "https://provider.invalid"))
    @patch("app.routers.media.http_json", return_value={"status": "completed", "video_url": "https://cdn.invalid/video.mp4"})
    @patch("app.routers.media.http_download")
    @patch("app.routers.media.validate_rendered_video")
    @patch("app.routers.media.VIDEO_DIR")
    def test_validated_download_is_atomically_registered(
        self, video_dir, validate, download, _http_json, _provider
    ):
        with tempfile.TemporaryDirectory() as folder:
            video_dir.__truediv__ = lambda _self, name: Path(folder) / name
            def write_video(_url, destination):
                Path(destination).write_bytes(b"valid-video" * 200)
            download.side_effect = write_video
            validate.return_value = {"valid": True, "has_audio": True}
            task = self._task()
            db = Mock()
            db.query.return_value.filter.return_value.first.return_value = task

            result = refresh_video_task(81, db)

            self.assertEqual(result["status"], "completed")
            self.assertTrue(Path(task.result_path).is_file())
            validate.assert_called_once()
            db.add.assert_called_once()
            self.assertIsNone(task.error)

    @patch("app.routers.media.validate_rendered_video", side_effect=ValueError("损坏文件"))
    def test_existing_corrupt_result_is_not_reported_as_completed(self, _validate):
        with tempfile.TemporaryDirectory() as folder:
            corrupt = Path(folder) / "old.mp4"
            corrupt.write_bytes(b"x" * 2048)
            task = self._task(str(corrupt))
            db = Mock()
            db.query.return_value.filter.return_value.first.return_value = task
            # Stop after the existing-file integrity branch by making provider lookup fail.
            with patch("app.routers.media.get_provider", side_effect=HTTPException(503, "provider unavailable")):
                with self.assertRaises(HTTPException):
                    refresh_video_task(81, db)
            self.assertEqual(task.status, "download_failed")
            self.assertIsNone(task.result_path)
            self.assertEqual(task.request_data["invalid_result_path"], str(corrupt))
            self.assertTrue(corrupt.exists())


if __name__ == "__main__":
    unittest.main()
