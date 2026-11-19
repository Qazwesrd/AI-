import unittest

from app.routers.video_editor import WebHandoffInput, _map_web_plan


class VideoEditorWebHandoffTests(unittest.TestCase):
    def test_handoff_requires_explicit_video_confirmation(self):
        payload = WebHandoffInput(
            instruction="保留主要动作，删除等待镜头",
            source_paths=["/storage/videos/source.mp4"],
        )
        self.assertFalse(payload.confirm_selected_videos)

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

    def test_web_plan_requires_non_empty_segments(self):
        with self.assertRaisesRegex(ValueError, "segments"):
            _map_web_plan({"segments": []}, ["/local/storage/source.mp4"])


if __name__ == "__main__":
    unittest.main()
