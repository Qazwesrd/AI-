import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from app.routers import prompts
from app.services.public_source_collector import FeedCollectionError


class PublicResearchSourceValidationTests(unittest.TestCase):
    def setUp(self):
        self.db = MagicMock()
        self.db.query.return_value.filter.return_value.first.return_value = None

    def test_create_source_fetches_and_verifies_feed_before_persisting(self):
        feed_url = "https://www.youtube.com/feeds/videos.xml?channel_id=UC1234567890123456789012"
        with patch.object(prompts, "validate_public_feed_url", return_value=feed_url), \
             patch.object(prompts, "fetch_public_feed", return_value=[{"title": "episode", "url": "https://youtube.com/watch?v=1"}]):
            result = prompts.create_public_research_source(
                prompts.PublicSourceInput(platform="YouTube", name="", feed_url=feed_url),
                self.db,
            )

        self.assertTrue(result["verified"])
        self.assertEqual(result["verified_item_count"], 1)
        self.assertEqual(result["item"]["name"], "YouTube · www.youtube.com")
        self.db.add.assert_called_once()
        self.db.commit.assert_called_once()

    def test_invalid_or_unreachable_feed_is_not_saved(self):
        feed_url = "https://www.youtube.com/feeds/videos.xml?channel_id=UC1234567890123456789012"
        with patch.object(prompts, "validate_public_feed_url", return_value=feed_url), \
             patch.object(prompts, "fetch_public_feed", side_effect=FeedCollectionError("订阅源连接失败或超时")):
            with self.assertRaises(HTTPException) as raised:
                prompts.create_public_research_source(
                    prompts.PublicSourceInput(platform="YouTube", name="", feed_url=feed_url),
                    self.db,
                )

        self.assertEqual(raised.exception.status_code, 400)
        self.db.add.assert_not_called()
        self.db.commit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
