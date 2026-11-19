import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

from app.routers.workspace_backups import _validate_workspace_archive


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


if __name__ == "__main__":
    unittest.main()
