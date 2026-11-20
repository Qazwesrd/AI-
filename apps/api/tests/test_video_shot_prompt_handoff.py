import unittest

from app.routers.ai_collaboration import HandoffPackageRequest


class VideoShotPromptHandoffTests(unittest.TestCase):
    def test_video_shot_prompt_is_a_supported_handoff_task(self):
        payload = HandoffPackageRequest(
            confirm_selected_chapter=True,
            task_type="视频分镜提示词",
        )
        self.assertEqual(payload.task_type, "视频分镜提示词")

    def test_unknown_handoff_task_is_rejected(self):
        with self.assertRaises(ValueError):
            HandoffPackageRequest(
                confirm_selected_chapter=True,
                task_type="自动扣费视频生成",
            )


if __name__ == "__main__":
    unittest.main()
