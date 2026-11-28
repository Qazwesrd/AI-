import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.routers.video_editor import (
    WebHandoffInput, LocalVideoAnalyzeInput, _map_web_plan, _build_web_handoff_details,
    _build_local_video_analysis_prompt,
)


class VideoEditorWebHandoffTests(unittest.TestCase):
    def test_handoff_requires_explicit_video_confirmation(self):
        payload = WebHandoffInput(
            instruction="保留主要动作，删除等待镜头",
            source_paths=["/storage/videos/source.mp4"],
        )
        self.assertFalse(payload.confirm_selected_videos)

    def test_pending_handoff_can_be_reconstructed_after_reload(self):
        job = SimpleNamespace(
            id=42,
            status="awaiting_web_ai",
            title="视频剪辑任务",
            instruction="保留关键动作，删除空镜头",
            source_files=["/storage/videos/source.mp4"],
        )
        manifest = [{
            "file_key": "video_1",
            "file_name": "source.mp4",
            "duration_seconds": 60.0,
            "width": 1920,
            "height": 1080,
            "size_bytes": 123456,
        }]
        with patch("app.routers.video_editor.safe_storage_file", side_effect=lambda value: Path(value)), patch(
            "app.routers.video_editor._web_file_manifest", return_value=manifest
        ):
            details = _build_web_handoff_details(job)
        self.assertEqual(details["id"], 42)
        self.assertEqual(details["manifest"], manifest)
        self.assertIn("保留关键动作", details["prompt"])
        self.assertFalse(details["video_bytes_transferred"])
        self.assertFalse(details["third_party_transfer_started"])

    def test_web_plan_maps_only_opaque_file_keys_to_local_sources(self):
        plan = {
            "title": "测试剪辑",
            "segments": [{
                "file_key": "video_1",
                "start": 1.5,
                "end": 4.0,
                "description": "用户确认的片段",
            }],
        }
        mapped = _map_web_plan(plan, ["/local/storage/source.mp4"])
        self.assertEqual(mapped["segments"][0]["file_path"], "/local/storage/source.mp4")
        self.assertNotIn("file_key", mapped["segments"][0])

    def test_web_plan_cannot_reference_an_unselected_video(self):
        plan = {"segments": [{
            "file_key": "video_99", "start": 0, "end": 2, "description": "不允许"
        }]}
        with self.assertRaisesRegex(ValueError, "未授权"):
            _map_web_plan(plan, ["/local/storage/source.mp4"])


    def test_local_video_analysis_prompt_uses_real_newlines_and_source_durations(self):
        data = LocalVideoAnalyzeInput(
            model="vision-local",
            instruction="保留人物说话镜头",
            source_paths=["/storage/videos/source.mp4"],
        )
        with patch("app.routers.video_editor.probe_media", return_value={
            "format": {"duration": "12.5"}, "streams": [{"codec_type": "video"}]
        }):
            prompt = _build_local_video_analysis_prompt(
                data,
                [{"file_index": 1, "timestamp_seconds": 2.5}],
                [Path("/storage/videos/source.mp4")],
            )
        self.assertIn("\n用户剪辑要求：\n保留人物说话镜头", prompt)
        self.assertIn('"duration_seconds": 12.5', prompt)
        self.assertNotIn("\\n用户剪辑要求", prompt)

    def test_web_plan_requires_non_empty_segments(self):
        with self.assertRaisesRegex(ValueError, "segments"):
            _map_web_plan({"segments": []}, ["/local/storage/source.mp4"])


if __name__ == "__main__":
    unittest.main()
