import io
import threading
import unittest
from unittest.mock import patch

from app.services.media_service import (
    FFmpegCancelledError,
    run_ffmpeg_with_progress,
)


class FakeProcess:
    def __init__(self, output="", returncode=0):
        self.stdout = io.StringIO(output)
        self.returncode = returncode
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode if self.stdout.tell() == len(self.stdout.getvalue()) else None

    def wait(self, timeout=None):
        if self.returncode is None:
            self.returncode = -15 if self.terminated else 0
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.killed = True
        self.returncode = -9


class FFmpegProgressRunnerTests(unittest.TestCase):
    @patch("app.services.media_service._bundled_media_path", return_value="ffmpeg")
    @patch("app.services.media_service.subprocess.Popen")
    def test_reports_progress_and_completion(self, popen, _path):
        process = FakeProcess(
            "out_time_ms=2500000\nframe=25\nspeed=1.2x\nprogress=continue\n"
            "out_time=00:00:10.000000\nprogress=end\n"
        )
        popen.return_value = process
        updates = []
        result = run_ffmpeg_with_progress(
            ["-i", "input.mp4", "temporary.mp4"],
            duration_seconds=10,
            progress_callback=updates.append,
        )
        self.assertEqual(result["percent"], 100.0)
        self.assertEqual(result["stage"], "validating")
        self.assertEqual(updates[0]["percent"], 25.0)
        self.assertEqual(updates[-1]["percent"], 100.0)
        self.assertEqual(popen.call_args.args[0][-1], "temporary.mp4")

    @patch("app.services.media_service._bundled_media_path", return_value="ffmpeg")
    @patch("app.services.media_service.subprocess.Popen")
    def test_cancel_event_terminates_ffmpeg(self, popen, _path):
        process = FakeProcess("")
        process.returncode = None
        popen.return_value = process
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(FFmpegCancelledError):
            run_ffmpeg_with_progress(
                ["-i", "input.mp4", "temporary.mp4"],
                cancel_event=cancel,
            )
        self.assertTrue(process.terminated)

    @patch("app.services.media_service._bundled_media_path", return_value="ffmpeg")
    @patch("app.services.media_service.subprocess.Popen")
    def test_nonzero_exit_includes_ffmpeg_diagnostic(self, popen, _path):
        process = FakeProcess("", returncode=1)
        popen.return_value = process
        with patch("app.services.media_service.tempfile.TemporaryFile") as temp:
            output = temp.return_value.__enter__.return_value
            output.read.return_value = "invalid input"
            with self.assertRaisesRegex(RuntimeError, "FFmpeg 执行失败"):
                run_ffmpeg_with_progress(["-i", "broken.mp4", "out.mp4"])


if __name__ == "__main__":
    unittest.main()
