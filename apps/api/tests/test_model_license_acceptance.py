import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from app.services.model_license_acceptance import (
    OPENRAIL_MODEL_FILENAME,
    OPENRAIL_TERMS_VERSION,
    get_openrail_acceptance,
    require_openrail_acceptance,
    set_openrail_acceptance,
)


class OpenRailAcceptanceTests(unittest.TestCase):
    def test_sd15_generation_is_blocked_until_acceptance(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "acceptance.json"
            with patch("app.services.model_license_acceptance.acceptance_path", return_value=path):
                with self.assertRaises(HTTPException) as caught:
                    require_openrail_acceptance(OPENRAIL_MODEL_FILENAME)
                self.assertEqual(caught.exception.status_code, 403)

    def test_acceptance_is_persisted_and_allows_sd15(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "legal" / "acceptance.json"
            with patch("app.services.model_license_acceptance.acceptance_path", return_value=path):
                result = set_openrail_acceptance(True, OPENRAIL_TERMS_VERSION)
                self.assertTrue(result["accepted"])
                self.assertIsNotNone(result["accepted_at"])
                require_openrail_acceptance(OPENRAIL_MODEL_FILENAME)
                stored = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(stored["terms_version"], OPENRAIL_TERMS_VERSION)

    def test_stale_terms_version_cannot_be_accepted(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "acceptance.json"
            with patch("app.services.model_license_acceptance.acceptance_path", return_value=path):
                with self.assertRaises(HTTPException) as caught:
                    set_openrail_acceptance(True, "old-version")
                self.assertEqual(caught.exception.status_code, 409)
                self.assertFalse(path.exists())

    def test_acceptance_can_be_revoked(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "acceptance.json"
            with patch("app.services.model_license_acceptance.acceptance_path", return_value=path):
                set_openrail_acceptance(True, OPENRAIL_TERMS_VERSION)
                result = set_openrail_acceptance(False, OPENRAIL_TERMS_VERSION)
                self.assertFalse(result["accepted"])
                self.assertFalse(path.exists())

    def test_other_checkpoints_are_not_gated_by_sd15_license(self):
        require_openrail_acceptance("my-own-model.safetensors")


if __name__ == "__main__":
    unittest.main()
