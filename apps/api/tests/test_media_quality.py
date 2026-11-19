import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.media_service import validate_rendered_video


class RenderedVideoValidationTests(unittest.TestCase):
    def _file(self, directory):
        path = Path(directory) / "rendered.mp4"
        path.write_bytes(b"x" * 2048)
        return path

    @patch("app.services.media_service.probe_media")
    def test_valid_video_returns_stream_and_duration_report(self, probe):
        with tempfile.TemporaryDirectory() as directory:
            path = self._file(directory)
            probe.return_value = {
                "format": {"duration": "120.25"},
                "streams": [
                    {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080},
                    {"codec_type": "audio", "codec_name": "aac"},
                ],
            }
            report = validate_rendered_video(path, expected_duration=120, require_audio=True)
            self.assertTrue(report["valid"])
            self.assertEqual(report["duration_seconds"], 120.25)
            self.assertTrue(report["has_audio"])
            self.assertEqual(report["width"], 1920)

    @patch("app.services.media_service.probe_media")
    def test_rejects_video_without_video_stream(self, probe):
        with tempfile.TemporaryDirectory() as directory:
            path = self._file(directory)
            probe.return_value = {"format": {"duration": "10"}, "streams": [
                {"codec_type": "audio", "codec_name": "aac"}
            ]}
            with self.assertRaisesRegex(ValueError, "没有有效的视频流"):
                validate_rendered_video(path)

    @patch("app.services.media_service.probe_media")
    def test_rejects_missing_audio_when_required(self, probe):
        with tempfile.TemporaryDirectory() as directory:
            path = self._file(directory)
            probe.return_value = {"format": {"duration": "10"}, "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080}
            ]}
            with self.assertRaisesRegex(ValueError, "缺少音频流"):
                validate_rendered_video(path, require_audio=True)

    @patch("app.services.media_service.probe_media")
    def test_rejects_duration_outside_tolerance(self, probe):
        with tempfile.TemporaryDirectory() as directory:
            path = self._file(directory)
            probe.return_value = {"format": {"duration": "80"}, "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080}
            ]}
            with self.assertRaisesRegex(ValueError, "时长异常"):
                validate_rendered_video(path, expected_duration=120)

    def test_rejects_tiny_output(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tiny.mp4"
            path.write_bytes(b"x")
            with self.assertRaisesRegex(ValueError, "过小"):
                validate_rendered_video(path)


if __name__ == "__main__":
    unittest.main()
