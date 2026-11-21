import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import editing_service


class EditingAudioDurationTests(unittest.TestCase):
    def _run_case(self, audio_mode, original_audio):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            video_dir = root / "videos"
            edit_dir = root / "edit-jobs"
            video_dir.mkdir()
            edit_dir.mkdir()
            final_calls = []

            def fake_ffmpeg(args, **kwargs):
                target = Path(args[-1])
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"x" * 2048)
                if target.name.endswith(".partial.mp4"):
                    final_calls.append(list(args))

            def fake_probe(path):
                return {"streams": (
                    [{"codec_type": "video"}, {"codec_type": "audio"}]
                    if original_audio else [{"codec_type": "video"}]
                )}

            plan = {
                "segments": [{
                    "file_path": str(root / "source.mp4"),
                    "start": 0,
                    "end": 8,
                }],
                "width": 1280,
                "height": 720,
            }
            with patch.object(editing_service, "VIDEO_DIR", video_dir), \
                 patch.object(editing_service, "EDIT_DIR", edit_dir), \
                 patch.object(editing_service, "safe_storage_file", side_effect=lambda value: Path(value)), \
                 patch.object(editing_service, "run_ffmpeg_with_progress", side_effect=fake_ffmpeg), \
                 patch.object(editing_service, "probe_media", side_effect=fake_probe), \
                 patch.object(editing_service, "validate_rendered_video") as validate:
                output, size = editing_service.execute_edit_plan(
                    plan,
                    audio_path=str(root / "short-audio.mp3"),
                    audio_mode=audio_mode,
                )

            self.assertTrue(Path(output).is_file())
            self.assertEqual(size, 2048)
            self.assertEqual(len(final_calls), 1)
            args = final_calls[0]
            self.assertNotIn("-shortest", args)
            self.assertIn("-t", args)
            self.assertEqual(args[args.index("-t") + 1], "8.0")
            if audio_mode == "replace" or (audio_mode == "mix" and not original_audio):
                self.assertIn("apad", args)
            else:
                self.assertIn("amix=inputs=2:duration=first", " ".join(args))
            validate.assert_called_once()
            self.assertEqual(validate.call_args.kwargs["expected_duration"], 8.0)
            self.assertTrue(validate.call_args.kwargs["require_audio"])

    def test_short_replacement_audio_does_not_truncate_video(self):
        self._run_case("replace", original_audio=True)

    def test_short_mix_audio_does_not_truncate_video_without_source_audio(self):
        self._run_case("mix", original_audio=False)

    def test_mix_preserves_full_video_duration_with_source_audio(self):
        self._run_case("mix", original_audio=True)


if __name__ == "__main__":
    unittest.main()
