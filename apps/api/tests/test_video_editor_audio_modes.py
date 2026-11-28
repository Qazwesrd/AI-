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

            def fake_ffmpeg(args):
                calls.append(list(args))
                destination = Path(args[-1])
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(b"rendered")

            with (
                patch.object(editing_service, "EDIT_DIR", root / "edit"),
                patch.object(editing_service, "VIDEO_DIR", root / "video"),
                patch.object(editing_service, "safe_storage_file", side_effect=lambda p: Path(p)),
                patch.object(editing_service, "run_ffmpeg", side_effect=fake_ffmpeg),
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
            return calls

    def test_replace_mode_uses_only_external_audio_track(self):
        args = self._run_plan("replace")[-1]
        self.assertIn("1:a:0", args)
        self.assertNotIn("-filter_complex", args)

    def test_mix_mode_mixes_original_and_external_audio(self):
        args = self._run_plan("mix", original_has_audio=True)[-1]
        self.assertIn("-filter_complex", args)
        filter_graph = args[args.index("-filter_complex") + 1]
        self.assertIn("amix=inputs=2", filter_graph)
        self.assertIn("[original]", filter_graph)
        self.assertIn("[music]", filter_graph)


    def test_silent_source_segment_gets_synthetic_audio_for_concat_compatibility(self):
        calls = self._run_plan("mix", original_has_audio=False)
        segment_args = calls[0]
        self.assertIn("anullsrc=channel_layout=stereo:sample_rate=48000", segment_args)
        self.assertIn("1:a:0", segment_args)
        self.assertIn("-map", segment_args)

    def test_mix_mode_works_when_source_has_no_audio(self):
        args = self._run_plan("mix", original_has_audio=False)[-1]
        self.assertIn("1:a:0", args)
        self.assertNotIn("-filter_complex", args)


if __name__ == "__main__":
    unittest.main()
