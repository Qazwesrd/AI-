import unittest

from app.services.task_diagnostics import classify_task_error


class TaskDiagnosticsTests(unittest.TestCase):
    def test_quota_errors_are_classified_without_retry_instruction(self):
        result = classify_task_error("insufficient_quota: balance too low")
        self.assertEqual(result["category"], "quota_or_billing")
        self.assertIn("不要自动重试", result["guidance"])

    def test_authentication_errors_are_classified(self):
        self.assertEqual(classify_task_error("401 invalid api key")["category"], "authentication")

    def test_timeout_errors_warn_about_submission_state(self):
        result = classify_task_error("Read timed out")
        self.assertEqual(result["category"], "timeout")
        self.assertIn("不要", result["guidance"])

    def test_unknown_errors_remain_unknown(self):
        result = classify_task_error("unexpected provider response")
        self.assertEqual(result["category"], "unknown")
        self.assertEqual(result["confidence"], "low")

    def test_empty_error_has_no_category(self):
        self.assertIsNone(classify_task_error("")["category"])


if __name__ == "__main__":
    unittest.main()
