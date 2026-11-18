import unittest
from types import SimpleNamespace

from app.routers.visual import mark_image_submission_unknown


class ImageSubmissionSafetyTests(unittest.TestCase):
    def test_ambiguous_http_failure_is_not_marked_as_retryable_failure(self):
        task = SimpleNamespace(status="running", error="")

        mark_image_submission_unknown(task, "图片服务 HTTP 502: upstream timeout")

        self.assertEqual(task.status, "interrupted_unknown")
        self.assertIn("核对图像服务商控制台", task.error)
        self.assertIn("不会重复扣费", task.error)
        self.assertIn("HTTP 502", task.error)

    def test_manual_review_error_is_bounded_to_database_field_limit(self):
        task = SimpleNamespace(status="running", error="")

        mark_image_submission_unknown(task, "x" * 5000)

        self.assertLessEqual(len(task.error), 2000)


if __name__ == "__main__":
    unittest.main()
