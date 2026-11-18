import unittest

from app.routers.publishing import PublishInput


class PublishingInputTests(unittest.TestCase):
    def test_cover_is_optional_when_frontend_sends_null(self):
        payload = PublishInput(
            platform="youtube",
            video_path="/storage/publishing/video/example.mp4",
            cover_path=None,
        )
        self.assertIsNone(payload.cover_path)

    def test_cover_defaults_to_none_when_omitted(self):
        payload = PublishInput(
            platform="youtube",
            video_path="/storage/publishing/video/example.mp4",
        )
        self.assertIsNone(payload.cover_path)


if __name__ == "__main__":
    unittest.main()
