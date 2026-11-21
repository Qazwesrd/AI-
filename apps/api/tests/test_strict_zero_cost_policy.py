import unittest

from fastapi import HTTPException

from app.services.cost_safety import STRICT_ZERO_COST_MODE, require_cloud_ai_api_disabled


class StrictZeroCostPolicyTests(unittest.TestCase):
    def test_strict_zero_cost_mode_is_enabled(self):
        self.assertTrue(STRICT_ZERO_COST_MODE)

    def test_cloud_ai_api_submission_is_blocked_before_network_access(self):
        with self.assertRaises(HTTPException) as raised:
            require_cloud_ai_api_disabled()

        self.assertEqual(raised.exception.status_code, 403)
        self.assertIn("严格零费用模式", raised.exception.detail)
        self.assertIn("不会提交任务", raised.exception.detail)


if __name__ == "__main__":
    unittest.main()
