import unittest
from pydantic import ValidationError

from app.routers.ai_collaboration import HandoffPackageRequest


class HandoffPackageRequestTests(unittest.TestCase):
    def test_explicit_confirmation_is_required_by_default(self):
        payload = HandoffPackageRequest()
        self.assertFalse(payload.confirm_selected_chapter)

    def test_confirmation_can_be_explicitly_enabled(self):
        payload = HandoffPackageRequest(confirm_selected_chapter=True)
        self.assertTrue(payload.confirm_selected_chapter)

    def test_context_limit_is_bounded(self):
        with self.assertRaises(ValidationError):
            HandoffPackageRequest(confirm_selected_chapter=True, max_context_chars=999)
        with self.assertRaises(ValidationError):
            HandoffPackageRequest(confirm_selected_chapter=True, max_context_chars=150001)

    def test_context_limit_accepts_supported_range(self):
        payload = HandoffPackageRequest(
            confirm_selected_chapter=True,
            max_context_chars=80000,
        )
        self.assertEqual(payload.max_context_chars, 80000)


if __name__ == "__main__":
    unittest.main()
