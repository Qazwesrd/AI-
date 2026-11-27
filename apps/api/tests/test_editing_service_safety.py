import unittest

from fastapi import HTTPException

from app.services.editing_service import call_ai_for_edit_plan


class EditingServiceZeroCostTests(unittest.TestCase):
    def test_cloud_ai_editing_api_is_blocked_before_provider_or_key_lookup(self):
        with self.assertRaises(HTTPException) as caught:
            call_ai_for_edit_plan(
                db=None,
                provider="any-provider",
                model="any-model",
                instruction="剪掉无效片段",
                source_files=["/not/read"],
                confirm_paid=True,
            )
        self.assertEqual(caught.exception.status_code, 403)
        self.assertIn("严格零费用模式", caught.exception.detail)


if __name__ == "__main__":
    unittest.main()
