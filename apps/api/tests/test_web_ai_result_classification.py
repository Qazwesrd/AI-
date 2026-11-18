import unittest

from app.routers.web_ai_tools import infer_result_classification


class WebAIResultClassificationTests(unittest.TestCase):
    def test_video_uses_extension(self):
        self.assertEqual(infer_result_classification("episode.mp4", "application/octet-stream", "video")[0], "video")

    def test_subtitle_uses_extension(self):
        self.assertEqual(infer_result_classification("episode.srt", "text/plain", "text")[0], "subtitle")

    def test_image_filename_only_produces_candidate(self):
        kind, reason = infer_result_classification("character_hero.png", "image/png", "image")
        self.assertEqual(kind, "character")
        self.assertIn("需人工确认", reason)

    def test_image_content_is_not_guessed(self):
        kind, reason = infer_result_classification("download.png", "image/png", "image")
        self.assertEqual(kind, "image")
        self.assertIn("无法仅凭元数据", reason)

    def test_storyboard_json_depends_on_task_category(self):
        self.assertEqual(infer_result_classification("shots.json", "application/json", "storyboard")[0], "storyboard")


if __name__ == "__main__":
    unittest.main()
