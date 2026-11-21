import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.routers.production_audit import _probe_final_video


class EpisodeVideoQualityAuditTests(unittest.TestCase):
    def test_missing_ffprobe_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            video = Path(temporary) / "episode.mp4"
            video.write_bytes(b"not-a-real-video")
            with patch("app.routers.production_audit.shutil.which", return_value=None):
                result = _probe_final_video(video)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["issues"][0]["code"], "ffprobe_unavailable")
        self.assertEqual(result["decode_check"], "not_run")

    def test_probe_and_full_decode_report_streams_and_duration(self):
        with tempfile.TemporaryDirectory() as temporary:
            video = Path(temporary) / "episode.mp4"
            video.write_bytes(b"test-video-bytes")
            payload = {
                "format": {"duration": "121.5", "size": str(video.stat().st_size), "format_name": "mov,mp4"},
                "streams": [
                    {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080},
                    {"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000", "channels": 2},
                ],
            }
            responses = [
                SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr=""),
                SimpleNamespace(returncode=0, stdout="", stderr=""),
            ]
            with (
                patch("app.routers.production_audit.shutil.which", side_effect=lambda name: f"/tools/{name}"),
                patch("app.routers.production_audit.subprocess.run", side_effect=responses) as run,
            ):
                result = _probe_final_video(video)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["probe"]["duration_seconds"], 121.5)
        self.assertEqual(result["probe"]["video_streams"][0]["codec_name"], "h264")
        self.assertEqual(result["probe"]["audio_streams"][0]["codec_name"], "aac")
        self.assertEqual(result["decode_check"], "passed")
        self.assertEqual(run.call_count, 2)

    def test_missing_audio_is_warning_not_blocker(self):
        with tempfile.TemporaryDirectory() as temporary:
            video = Path(temporary) / "silent.mp4"
            video.write_bytes(b"test-video-bytes")
            payload = {
                "format": {"duration": "30", "format_name": "mp4"},
                "streams": [{"codec_type": "video", "codec_name": "h264"}],
            }
            responses = [
                SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr=""),
                SimpleNamespace(returncode=0, stdout="", stderr=""),
            ]
            with (
                patch("app.routers.production_audit.shutil.which", side_effect=lambda name: f"/tools/{name}"),
                patch("app.routers.production_audit.subprocess.run", side_effect=responses),
            ):
                result = _probe_final_video(video)
        self.assertEqual(result["status"], "ready_with_warnings")
        self.assertIn("audio_stream_missing", {item["code"] for item in result["issues"]})


if __name__ == "__main__":
    unittest.main()
