import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from app.routers.patch_center import (
    PatchPackageInput,
    _diff_paths,
    _validate_target_path,
    create_patch_package,
    list_patch_packages,
    _build_patch_preview,
)


class PatchCenterTests(unittest.TestCase):
    def test_allowed_source_path(self):
        self.assertEqual(
            _validate_target_path("apps/web/src/app/example/page.tsx"),
            "apps/web/src/app/example/page.tsx",
        )

    def test_rejects_path_traversal_and_absolute_paths(self):
        for path in (
            "../secret.py",
            "apps/web/src/../../secret.py",
            "/etc/passwd",
            "C:\\Windows\\win.ini",
            "apps/web/public/payload.js",
        ):
            with self.subTest(path=path):
                with self.assertRaises(HTTPException):
                    _validate_target_path(path)

    def test_diff_paths_extracts_files(self):
        diff = (
            "--- a/apps/web/src/app/page.tsx\n"
            "+++ b/apps/web/src/app/page.tsx\n"
            "@@ -1 +1 @@\n-old\n+new\n"
        )
        self.assertEqual(_diff_paths(diff), ["apps/web/src/app/page.tsx"])

    def test_diff_paths_accepts_new_file(self):
        diff = (
            "--- /dev/null\n"
            "+++ b/apps/api/app/new_router.py\n"
            "@@ -0,0 +1 @@\n+value = 1\n"
        )
        self.assertEqual(_diff_paths(diff), ["apps/api/app/new_router.py"])


    def test_preview_returns_hunks_without_applying(self):
        diff = (
            "--- a/apps/web/src/app/page.tsx\\n"
            "+++ b/apps/web/src/app/page.tsx\\n"
            "@@ -1 +1 @@\\n-old\\n+new\\n"
        )
        preview = _build_patch_preview({
            "id": "a" * 32,
            "title": "preview test",
            "target_version": "current",
            "sha256": "b" * 64,
            "size_bytes": len(diff.encode("utf-8")),
            "changed_files": ["apps/web/src/app/page.tsx"],
            "patch_format": "unified-diff",
            "code": diff,
        })
        self.assertFalse(preview["apply_supported"])
        self.assertEqual(preview["files"][0]["new_path"], "b/apps/web/src/app/page.tsx")
        self.assertIn("+new", preview["files"][0]["hunks"][0]["changes"])
        self.assertIn("只读", preview["message"])

    def test_package_is_staged_and_hash_is_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict("os.environ", {"APP_DATA_DIR": directory}):
                result = create_patch_package(PatchPackageInput(
                    title="test patch",
                    patch_format="unified-diff",
                    code=(
                        "--- a/apps/web/src/app/page.tsx\n"
                        "+++ b/apps/web/src/app/page.tsx\n"
                        "@@ -1 +1 @@\n-old\n+new\n"
                    ),
                ))
                self.assertEqual(result["status"], "staged")
                self.assertEqual(len(result["sha256"]), 64)
                self.assertEqual(
                    list_patch_packages()["packages"][0]["id"],
                    result["id"],
                )


if __name__ == "__main__":
    unittest.main()
