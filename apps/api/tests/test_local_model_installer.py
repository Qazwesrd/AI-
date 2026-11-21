import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.routers.local_model_installer import (
    StartModelInstallInput,
    model_install_info,
    start_model_install,
)


class LocalModelInstallerTests(unittest.TestCase):
    def test_info_reports_fixed_model_and_no_automatic_download(self):
        with patch("app.routers.local_model_installer._ollama_models", return_value=[]), patch(
            "app.routers.local_model_installer.shutil.disk_usage",
            return_value=type("Usage", (), {"free": 10 * 1024 ** 3})(),
        ):
            result = model_install_info()
        self.assertEqual(result["model"], "qwen3:4b")
        self.assertTrue(result["enough_space"])
        self.assertFalse(result["automatic_download"])
        self.assertFalse(result["cloud_fallback"])

    def test_download_requires_explicit_consent(self):
        with self.assertRaises(HTTPException) as caught:
            start_model_install(StartModelInstallInput(confirm_download=False))
        self.assertEqual(caught.exception.status_code, 400)

    def test_existing_model_is_not_downloaded_again(self):
        with patch(
            "app.routers.local_model_installer._ollama_models",
            return_value=[{"name": "qwen3:4b"}],
        ):
            result = start_model_install(StartModelInstallInput(confirm_download=True))
        self.assertEqual(result["status"], "already_installed")
        self.assertFalse(result["network_used"])
        self.assertFalse(result["cloud_fallback"])


if __name__ == "__main__":
    unittest.main()
