import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from pathlib import Path

from app.routers.workspace_backups import _validate_workspace_archive, _verify_archive_stream_limits


class WorkspaceArchiveValidationTests(unittest.TestCase):
    def test_rejects_zip_without_database_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("manifest.json", json.dumps({"format_version": 1}))
                archive.writestr("storage/example.txt", "data")
            with self.assertRaisesRegex(ValueError, "缺少数据库快照"):
                _validate_workspace_archive(path)

    def test_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "unsafe.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("manifest.json", "{}")
                archive.writestr("database/current.db", "not really a db")
                archive.writestr("storage/../../outside.txt", "bad")
            with self.assertRaisesRegex(ValueError, "不安全的文件路径"):
                _validate_workspace_archive(path)

    def test_rejects_corrupt_zip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "corrupt.zip"
            path.write_bytes(b"not a zip")
            with self.assertRaises(ValueError):
                _validate_workspace_archive(path)

    def test_stream_verification_enforces_actual_expanded_byte_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "large.zip"
            with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("payload.txt", "x" * 128)
            with zipfile.ZipFile(path, "r") as archive:
                with patch("app.routers.workspace_backups.MAX_ARCHIVE_UNPACKED_BYTES", 64):
                    with self.assertRaisesRegex(ValueError, "实际解压"):
                        _verify_archive_stream_limits(archive)

    def test_stream_verification_checks_member_integrity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "valid.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("payload.txt", "safe content")
            with zipfile.ZipFile(path, "r") as archive:
                self.assertEqual(_verify_archive_stream_limits(archive), len("safe content"))


if __name__ == "__main__":
    unittest.main()
