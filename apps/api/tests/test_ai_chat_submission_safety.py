import unittest

from app.routers.studio import record_ai_submission_unknown


class FakeDatabase:
    def __init__(self):
        self.items = []
        self.committed = False

    def add(self, item):
        self.items.append(item)

    def commit(self):
        self.committed = True


class AIChatSubmissionSafetyTests(unittest.TestCase):
    def test_ambiguous_provider_submission_is_not_logged_as_safe_failure(self):
        db = FakeDatabase()

        detail = record_ai_submission_unknown(
            db, "xai", "grok-model", "story_analysis", TimeoutError("read timed out")
        )

        self.assertTrue(db.committed)
        self.assertEqual(len(db.items), 1)
        self.assertEqual(db.items[0].status, "submission_unknown")
        self.assertIn("不要直接重复提交", detail)
        self.assertIn("read timed out", db.items[0].detail)

    def test_ambiguous_submission_log_is_bounded(self):
        db = FakeDatabase()

        record_ai_submission_unknown(db, "openai", "model", "chat", RuntimeError("x" * 5000))

        self.assertLessEqual(len(db.items[0].detail), 2000)


if __name__ == "__main__":
    unittest.main()
