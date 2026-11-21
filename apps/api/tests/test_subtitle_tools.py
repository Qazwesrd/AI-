import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import HTTPException

from app.routers.subtitle_tools import (
    LocalTranslateInput,
    LocalVoiceInput,
    create_local_subtitle_voice,
    translate_subtitles_locally,
    list_local_subtitle_voices,
    parse_srt,
    render_srt,
    format_time,
    _subtitle_ownership,
)


class SubtitleToolsTests(unittest.TestCase):
    def test_parse_and_render_preserves_timing_and_order(self):
        source = """1
00:00:01,250 --> 00:00:02,500
你好

2
00:00:03,000 --> 00:00:04,250
世界
"""
        cues = parse_srt(source)
        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[0]["start"], 1.25)
        self.assertEqual(cues[0]["end"], 2.5)
        self.assertEqual(cues[1]["text"], "世界")
        rendered = render_srt([
            {"start": item["start"], "end": item["end"], "text": "translated"}
            for item in cues
        ])
        reparsed = parse_srt(rendered)
        self.assertEqual([(x["start"], x["end"]) for x in reparsed],
                         [(x["start"], x["end"]) for x in cues])

    def test_parse_accepts_dot_milliseconds(self):
        cues = parse_srt("1\n00:00:00.500 --> 00:00:01.250\nHello")
        self.assertEqual(cues[0]["start"], 0.5)
        self.assertEqual(cues[0]["end"], 1.25)

    def test_invalid_or_empty_srt_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_srt("not a subtitle")
        with self.assertRaises(ValueError):
            parse_srt("1\n00:00:02,000 --> 00:00:01,000\nWrong")

    def test_subtitle_ownership_is_inherited_from_registered_track(self):
        db = Mock()
        db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(
            project_id=17,
            episode_id=23,
        )
        self.assertEqual(
            _subtitle_ownership(db, Path("/managed/subtitles/episode.srt")),
            (17, 23),
        )

    def test_subtitle_ownership_falls_back_to_media_metadata(self):
        db = Mock()
        db.query.return_value.filter.return_value.first.side_effect = [
            None,
            SimpleNamespace(metadata_json={"project_id": "17", "episode_id": "23"}),
        ]
        self.assertEqual(
            _subtitle_ownership(db, Path("/managed/subtitles/imported.srt")),
            (17, 23),
        )

    def test_local_subtitle_translation_requires_explicit_confirmation(self):
        with self.assertRaises(HTTPException) as raised:
            translate_subtitles_locally(
                LocalTranslateInput(subtitle_path="captions.srt", model="qwen3:4b", target_language="zh", confirm_local_generation=False),
                db=Mock(),
            )
        self.assertEqual(raised.exception.status_code, 400)

    def test_local_subtitle_translation_preserves_timecodes_and_order(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "captions.srt"
            source.write_text("source", encoding="utf-8")
            db = Mock()
            model_response = {"response": '{"translations":[{"id":1,"text":"你好"},{"id":2,"text":"世界"}]}'}
            with (
                patch("app.routers.subtitle_tools.SUBTITLE_DIR", root),
                patch("app.routers.subtitle_tools._subtitle_ownership", return_value=(None, None)),
                patch("app.routers.subtitle_tools.safe_storage_file", return_value=source),
                patch("app.routers.subtitle_tools._read_text", return_value="srt"),
                patch("app.routers.subtitle_tools.parse_srt", return_value=[
                    {"index": 1, "start": 1.0, "end": 2.0, "text": "Hello"},
                    {"index": 2, "start": 3.0, "end": 4.0, "text": "World"},
                ]),
                patch("app.routers.subtitle_tools._installed_models", return_value=[{"name": "qwen3:4b"}]),
                patch("app.routers.subtitle_tools._ollama_request", return_value=model_response) as ollama,
            ):
                result = translate_subtitles_locally(
                    LocalTranslateInput(
                        subtitle_path=str(source), model="qwen3:4b",
                        target_language="zh", confirm_local_generation=True,
                    ),
                    db=db,
                )
                saved = Path(result["path"]).read_text(encoding="utf-8-sig")
            self.assertTrue(result["success"])
            self.assertTrue(result["timecodes_preserved"])
            self.assertFalse(result["network_used"])
            self.assertFalse(result["paid_api_used"])
            self.assertIn("00:00:01,000 --> 00:00:02,000", saved)
            self.assertIn("00:00:03,000 --> 00:00:04,000", saved)
            self.assertLess(saved.index("你好"), saved.index("世界"))
            ollama.assert_called_once()
            db.add.assert_called()
            db.commit.assert_called_once()

    def test_local_subtitle_translation_rejects_uninstalled_model(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "captions.srt"
            source.write_text("source", encoding="utf-8")
            with (
                patch("app.routers.subtitle_tools.safe_storage_file", return_value=source),
                patch("app.routers.subtitle_tools._subtitle_ownership", return_value=(None, None)),
                patch("app.routers.subtitle_tools._read_text", return_value="srt"),
                patch("app.routers.subtitle_tools.parse_srt", return_value=[
                    {"index": 1, "start": 0.0, "end": 1.0, "text": "Hello"}
                ]),
                patch("app.routers.subtitle_tools._installed_models", return_value=[]),
            ):
                with self.assertRaises(HTTPException) as raised:
                    translate_subtitles_locally(
                        LocalTranslateInput(
                            subtitle_path=str(source), model="qwen3:4b",
                            target_language="zh", confirm_local_generation=True,
                        ),
                        db=Mock(),
                    )
            self.assertEqual(raised.exception.status_code, 404)

    def test_local_voice_requires_explicit_confirmation(self):
        with self.assertRaises(HTTPException) as raised:
            create_local_subtitle_voice(
                LocalVoiceInput(subtitle_path="sample.srt", voice="Microsoft Huihui", confirm_local_generation=False),
                db=Mock(),
            )
        self.assertEqual(raised.exception.status_code, 400)

    def test_local_voice_lists_only_installed_windows_voices(self):
        with patch("app.routers.subtitle_tools.list_local_sapi_voices", return_value=[
            {"name": "Microsoft Huihui Desktop", "culture": "zh-CN", "gender": "Female", "age": "Adult"}
        ]):
            result = list_local_subtitle_voices()
        self.assertEqual(result["voice_count"], 1)
        self.assertEqual(result["voices"][0]["culture"], "zh-CN")
        self.assertFalse(result["network_used"])
        self.assertFalse(result["paid_api_used"])

    def test_local_subtitle_voice_generates_aligned_offline_audio(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "captions.srt"
            source.write_text("test", encoding="utf-8")
            job_dir = root / "job"
            job_dir.mkdir()
            segment = job_dir / "cue-1.wav"
            segment.write_bytes(b"RIFF" + b"0" * 20 + b"WAVE")
            db = Mock()
            def write_output(command):
                Path(command[-1]).write_bytes(b"mp3-output")
            with (
                patch("app.routers.subtitle_tools.AUDIO_DIR", root),
                patch("app.routers.subtitle_tools._subtitle_ownership", return_value=(None, None)),
                patch("app.routers.subtitle_tools.safe_storage_file", return_value=source),
                patch("app.routers.subtitle_tools._read_text", return_value="srt"),
                patch("app.routers.subtitle_tools.parse_srt", return_value=[
                    {"index": 1, "start": 0.0, "end": 2.0, "text": "你好"}
                ]),
                patch("app.routers.subtitle_tools.synthesize_local_sapi_cues", return_value=(job_dir, [segment])),
                patch("app.routers.subtitle_tools.probe_media", return_value={"format": {"duration": 1.0}}),
                patch("app.routers.subtitle_tools.run_ffmpeg", side_effect=write_output),
                patch("app.routers.subtitle_tools.shutil.rmtree") as cleanup,
            ):
                result = create_local_subtitle_voice(
                    LocalVoiceInput(
                        subtitle_path=str(source),
                        voice="Microsoft Huihui Desktop",
                        rate=0,
                        confirm_local_generation=True,
                    ),
                    db=db,
                )
            self.assertTrue(result["success"])
            self.assertTrue(result["timeline_aligned"])
            self.assertFalse(result["network_used"])
            self.assertFalse(result["paid_api_used"])
            db.add.assert_called()
            db.commit.assert_called_once()
            cleanup.assert_called_once_with(job_dir, ignore_errors=True)

    def test_time_formatter_keeps_milliseconds(self):
        self.assertEqual(format_time(65.125), "00:01:05,125")


if __name__ == "__main__":
    unittest.main()
