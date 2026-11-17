import unittest

from app.routers.tasks import _status_group


class UnifiedTaskStatusTests(unittest.TestCase):
    def test_active_provider_statuses_are_normalized(self):
        for value in ("pending", "queued", "submitted", "processing", "running"):
            with self.subTest(status=value):
                self.assertEqual(_status_group(value), "active")

    def test_completed_status_variants_are_normalized(self):
        for value in ("completed", "success", "succeeded", "finished", "done"):
            with self.subTest(status=value):
                self.assertEqual(_status_group(value), "completed")

    def test_uncertain_submission_needs_attention(self):
        for value in ("submission_unknown", "awaiting_video_url", "submitting"):
            with self.subTest(status=value):
                self.assertEqual(_status_group(value), "attention")

    def test_failure_pause_cancel_and_partial_are_distinct(self):
        self.assertEqual(_status_group("failed"), "failed")
        self.assertEqual(_status_group("paused"), "paused")
        self.assertEqual(_status_group("cancelled"), "cancelled")
        self.assertEqual(_status_group("partial"), "partial")

    def test_unknown_status_is_preserved_as_other(self):
        self.assertEqual(_status_group("provider_custom_state"), "other")
        self.assertEqual(_status_group(None), "other")


if __name__ == "__main__":
    unittest.main()
