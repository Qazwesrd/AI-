import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import editing_service


class VideoEditorAudioModeTests(unittest.TestCase):
    def _run_plan(self, mode, original_has_audio=True):
        with tempfile.TemporaryDirectory(prefix="video-audio-mode-") as temp:
            root = Path(temp)
            source = root / "source.mp4"
            audio = root / "music.mp3"
            source.write_bytes(b"source")
            audio.write_bytes(b"audio")
            calls = []

            def fake_ffmpeg(args, **kwargs):
                calls.append(list(args))
                destination = Path(args[-1])
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(b"x" * 2048)
                if kwargs.get("progress_callback"):
                    kwargs["progress_callback"]({"percent": 99.9, "stage": "validating"})

            with (
                patch.object(editing_service, "EDIT_DIR", root / "edit"),
                patch.object(editing_service, "VIDEO_DIR", root / "video"),
                patch.object(editing_service, "safe_storage_file", side_effect=lambda p: Path(p)),
                patch.object(editing_service, "run_ffmpeg_with_progress", side_effect=fake_ffmpeg),
                patch.object(editing_service, "validate_rendered_video", return_value={"valid": True}),
                patch.object(
                    editing_service,
                    "probe_media",
                    return_value={"streams": [{"codec_type": "audio"}] if original_has_audio else []},
                ),
            ):
                output, size = editing_service.execute_edit_plan(
                    {
                        "segments": [{
                            "file_path": str(source),
                            "start": 0,
                            "end": 2,
                        }],
                        "width": 1280,
                        "height": 720,
                    },
                    audio_path=str(audio),
                    audio_mode=mode,
                )

            self.assertTrue(Path(output).is_file())
            self.assertGreater(size, 0)
            return calls[-1]

    def test_replace_mode_uses_only_external_audio_track(self):
        args = self._run_plan("replace")
        self.assertIn("1:a:0", args)
        self.assertNotIn("-filter_complex", args)

    def test_mix_mode_mixes_original_and_external_audio(self):
        args = self._run_plan("mix", original_has_audio=True)
        self.assertIn("-filter_complex", args)
        filter_graph = args[args.index("-filter_complex") + 1]
        self.assertIn("amix=inputs=2", filter_graph)
        self.assertIn("[original]", filter_graph)
        self.assertIn("[music]", filter_graph)

    def test_mix_mode_works_when_source_has_no_audio(self):
        args = self._run_plan("mix", original_has_audio=False)
        self.assertIn("1:a:0", args)
        self.assertNotIn("-filter_complex", args)

    def test_reports_weighted_progress_through_validation(self):
        with tempfile.TemporaryDirectory(prefix="video-progress-") as temp:
            root = Path(temp)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            updates = []

            def fake_ffmpeg(args, **kwargs):
                Path(args[-1]).parent.mkdir(parents=True, exist_ok=True)
                Path(args[-1]).write_bytes(b"x" * 2048)
                if kwargs.get("progress_callback"):
                    kwargs["progress_callback"]({"percent": 99.9, "out_time_seconds": 2})

            with (
                patch.object(editing_service, "EDIT_DIR", root / "edit"),
                patch.object(editing_service, "VIDEO_DIR", root / "video"),
                patch.object(editing_service, "safe_storage_file", side_effect=lambda p: Path(p)),
                patch.object(editing_service, "run_ffmpeg_with_progress", side_effect=fake_ffmpeg),
                patch.object(editing_service, "validate_rendered_video", return_value={"valid": True}),
            ):
                editing_service.execute_edit_plan(
                    {"segments": [{"file_path": str(source), "start": 0, "end": 2}],
                     "width": 1280, "height": 720},
                    progress_callback=updates.append,
                )
            self.assertTrue(any(item["stage"] == "rendering_segments" for item in updates))
            self.assertTrue(any(item["stage"] == "concatenating" for item in updates))
            self.assertEqual(updates[-1]["stage"], "completed")
            self.assertEqual(updates[-1]["percent"], 100.0)

    def test_cancel_event_stops_before_next_segment(self):
        import threading
        from app.services.media_service import FFmpegCancelledError

        with tempfile.TemporaryDirectory(prefix="video-cancel-") as temp:
            root = Path(temp)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            cancel = threading.Event()
            cancel.set()
            with (
                patch.object(editing_service, "EDIT_DIR", root / "edit"),
                patch.object(editing_service, "VIDEO_DIR", root / "video"),
            ):
                with self.assertRaises(FFmpegCancelledError) as raised:
                    editing_service.execute_edit_plan(
                        {"segments": [{"file_path": str(source), "start": 0, "end": 2}],
                         "width": 1280, "height": 720},
                        cancel_event=cancel,
                    )
                self.assertIsNone(raised.exception.partial_output)
                self.assertIsNone(raised.exception.work_directory)
                work_root = root / "edit"
                self.assertFalse(work_root.exists() and any(work_root.iterdir()))



if __name__ == "__main__":
    unittest.main()
