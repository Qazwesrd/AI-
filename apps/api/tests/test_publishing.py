import unittest
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi import HTTPException

from app.routers.publishing import PublishInput, create_jobs
from app.services.media_service import STORAGE


class PublishingInputTests(unittest.TestCase):
    def test_cover_is_optional_when_frontend_sends_null(self):
        payload = PublishInput(
            platform="youtube",
            video_path="/storage/publishing/video/example.mp4",
            cover_path=None,
        )
        self.assertIsNone(payload.cover_path)

    def test_existing_video_outside_app_storage_is_rejected(self):
        with tempfile.NamedTemporaryFile(suffix=".mp4") as outside:
            with self.assertRaises(HTTPException) as raised:
                create_jobs(
                    [PublishInput(platform="youtube", video_path=outside.name)],
                    db=Mock(),
                )
        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("素材存储目录", raised.exception.detail)

    def test_unsupported_video_extension_is_rejected_inside_storage(self):
        with tempfile.TemporaryDirectory(dir=str(STORAGE)) as folder:
            video = Path(folder) / "not-a-video.txt"
            video.write_text("not a video", encoding="utf-8")
            with self.assertRaises(HTTPException) as raised:
                create_jobs(
                    [PublishInput(platform="youtube", video_path=str(video))],
                    db=Mock(),
                )
        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("视频仅支持", raised.exception.detail)

    def test_cover_outside_app_storage_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=str(STORAGE)) as folder:
            video = Path(folder) / "video.mp4"
            video.write_bytes(b"test video placeholder")
            with tempfile.NamedTemporaryFile(suffix=".png") as outside_cover:
                with self.assertRaises(HTTPException) as raised:
                    create_jobs(
                        [PublishInput(
                            platform="youtube",
                            video_path=str(video),
                            cover_path=outside_cover.name,
                        )],
                        db=Mock(),
                    )
        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("封面必须位于", raised.exception.detail)

    def test_cover_defaults_to_none_when_omitted(self):
        payload = PublishInput(
            platform="youtube",
            video_path="/storage/publishing/video/example.mp4",
        )
        self.assertIsNone(payload.cover_path)

    @patch("app.routers.publishing.validate_rendered_video")
    def test_corrupt_video_is_rejected_before_publish_job_creation(self, validate):
        validate.side_effect = ValueError("FFprobe 无法确认导出视频时长")
        with tempfile.TemporaryDirectory(dir=str(STORAGE)) as folder:
            video = Path(folder) / "corrupt.mp4"
            video.write_bytes(b"x" * 2048)
            with self.assertRaises(HTTPException) as raised:
                create_jobs(
                    [PublishInput(platform="youtube", video_path=str(video))],
                    db=Mock(),
                )
        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("完整性检查", raised.exception.detail)
        self.assertIn("无法确认", raised.exception.detail)

    @patch("app.routers.publishing.validate_rendered_video")
    def test_valid_silent_video_is_allowed_for_manual_publishing(self, validate):
        validate.return_value = {
            "valid": True, "has_audio": False, "warnings": ["没有音频流"]
        }
        with tempfile.TemporaryDirectory(dir=str(STORAGE)) as folder:
            video = Path(folder) / "silent.mp4"
            video.write_bytes(b"x" * 2048)
            db = Mock()
            result = create_jobs(
                [PublishInput(platform="youtube", video_path=str(video))],
                db=db,
            )
        self.assertTrue(result["success"])
        validate.assert_called_once_with(Path(video))


if __name__ == "__main__":
    unittest.main()
