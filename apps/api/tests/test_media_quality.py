import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.media_service import validate_rendered_video, _parse_frame_rate


class RenderedVideoValidationTests(unittest.TestCase):
    def test_frame_rate_parser_handles_common_fraction_formats(self):
        self.assertEqual(_parse_frame_rate("24/1"), 24.0)
        self.assertEqual(_parse_frame_rate("30000/1001"), 29.97)
        self.assertIsNone(_parse_frame_rate("0/0"))
        self.assertIsNone(_parse_frame_rate("N/A"))

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
                    {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080, "avg_frame_rate": "24/1"},
                    {"codec_type": "audio", "codec_name": "aac"},
                ],
            }
            report = validate_rendered_video(path, expected_duration=120, require_audio=True)
            self.assertTrue(report["valid"])
            self.assertEqual(report["duration_seconds"], 120.25)
            self.assertTrue(report["has_audio"])
            self.assertEqual(report["width"], 1920)
            self.assertEqual(report["frame_rate"], 24.0)

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




class AdvancedMediaQualityScanTests(unittest.TestCase):
    @patch("app.services.media_service._bundled_media_path", return_value="ffmpeg")
    @patch("app.services.media_service.subprocess.run")
    @patch("app.services.media_service.probe_media")
    def test_detects_black_frames_and_silence_as_advisories(self, probe, run, _ffmpeg):
        from types import SimpleNamespace
        from app.services.media_service import scan_media_quality

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "episode.mp4"
            path.write_bytes(b"x" * 2048)
            probe.return_value = {"streams": [
                {"codec_type": "video", "codec_name": "h264"},
                {"codec_type": "audio", "codec_name": "aac"},
            ]}
            run.side_effect = [
                SimpleNamespace(returncode=0, stderr="black_start:1.000 black_end:2.250 black_duration:1.250", stdout=""),
                SimpleNamespace(returncode=0, stderr="mean_volume: -24.0 dB\nmax_volume: -1.0 dB", stdout=""),
                SimpleNamespace(returncode=0, stderr="silence_start:3.000\nsilence_end:5.500 | silence_duration:2.500", stdout=""),
            ]
            report = scan_media_quality(path)
            self.assertTrue(report["scan_complete"])
            self.assertEqual(report["audio_levels"]["mean_volume_db"], -24.0)
            self.assertEqual(report["audio_levels"]["max_volume_db"], -1.0)
            self.assertEqual(len(report["black_segments"]), 1)
            self.assertEqual(report["black_segments"][0]["duration_seconds"], 1.25)
            self.assertEqual(len(report["silence_segments"]), 1)
            self.assertEqual(report["silence_segments"][0]["duration_seconds"], 2.5)
            self.assertEqual(len(report["warnings"]), 2)

    @patch("app.services.media_service._bundled_media_path", return_value="ffmpeg")
    @patch("app.services.media_service.subprocess.run")
    @patch("app.services.media_service.probe_media")
    def test_warns_for_quiet_or_clipping_audio(self, probe, run, _ffmpeg):
        from types import SimpleNamespace
        from app.services.media_service import scan_media_quality

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "levels.mp4"
            path.write_bytes(b"x" * 2048)
            probe.return_value = {"streams": [
                {"codec_type": "video", "codec_name": "h264"},
                {"codec_type": "audio", "codec_name": "aac"},
            ]}
            run.side_effect = [
                SimpleNamespace(returncode=0, stderr="", stdout=""),
                SimpleNamespace(returncode=0, stderr="mean_volume: -42.0 dB\nmax_volume: 0.2 dB", stdout=""),
                SimpleNamespace(returncode=0, stderr="", stdout=""),
            ]
            report = scan_media_quality(path)
            self.assertEqual(report["audio_levels"]["mean_volume_db"], -42.0)
            self.assertEqual(report["audio_levels"]["max_volume_db"], 0.2)
            self.assertTrue(any("平均音量偏低" in item for item in report["warnings"]))
            self.assertTrue(any("削波风险" in item for item in report["warnings"]))

    @patch("app.services.media_service._bundled_media_path", return_value="ffmpeg")
    @patch("app.services.media_service.subprocess.run")
    @patch("app.services.media_service.probe_media")
    def test_skips_silence_detection_when_video_has_no_audio(self, probe, run, _ffmpeg):
        from types import SimpleNamespace
        from app.services.media_service import scan_media_quality

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "silent.mp4"
            path.write_bytes(b"x" * 2048)
            probe.return_value = {"streams": [
                {"codec_type": "video", "codec_name": "h264"}
            ]}
            run.return_value = SimpleNamespace(returncode=0, stderr="", stdout="")
            report = scan_media_quality(path)
            self.assertFalse(report["has_audio"])
            self.assertEqual(report["silence_segments"], [])
            self.assertEqual(run.call_count, 1)
            self.assertTrue(any("没有音轨" in item for item in report["warnings"]))

    @patch("app.services.media_service._bundled_media_path", return_value="ffmpeg")
    @patch("app.services.media_service.subprocess.run")
    @patch("app.services.media_service.probe_media")
    def test_rejects_media_without_video_stream(self, probe, _run, _ffmpeg):
        from app.services.media_service import scan_media_quality

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audio.mp4"
            path.write_bytes(b"x" * 2048)
            probe.return_value = {"streams": [{"codec_type": "audio"}]}
            with self.assertRaisesRegex(ValueError, "没有可检测的视频流"):
                scan_media_quality(path)


if __name__ == "__main__":
    unittest.main()
