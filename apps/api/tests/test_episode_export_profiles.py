import unittest

from app.routers.media import build_episode_export_video_filter


class EpisodeExportProfileTests(unittest.TestCase):
    def test_default_full_hd_24fps_profile(self):
        result = build_episode_export_video_filter(1920, 1080, 24, 5)
        self.assertIn("scale=1920:1080", result)
        self.assertIn("fps=24", result)
        self.assertIn("format=yuv420p", result)
        self.assertIn("trim=duration=5.0", result)

    def test_full_hd_30fps_profile(self):
        result = build_episode_export_video_filter(1920, 1080, 30, 3.5)
        self.assertIn("fps=30", result)
        self.assertIn("trim=duration=3.5", result)

    def test_supported_720p_profile(self):
        result = build_episode_export_video_filter(1280, 720, 24, 4)
        self.assertIn("scale=1280:720", result)

    def test_rejects_unsupported_resolution_or_frame_rate(self):
        with self.assertRaisesRegex(ValueError, "分辨率"):
            build_episode_export_video_filter(1600, 900, 24, 4)
        with self.assertRaisesRegex(ValueError, "帧率"):
            build_episode_export_video_filter(1920, 1080, 60, 4)

    def test_rejects_invalid_duration(self):
        with self.assertRaisesRegex(ValueError, "时长"):
            build_episode_export_video_filter(1920, 1080, 24, 0)


if __name__ == "__main__":
    unittest.main()
