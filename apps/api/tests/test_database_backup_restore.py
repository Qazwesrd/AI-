import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.routers.backups import _validate_sqlite_backup, _backup_sqlite


class DatabaseBackupTests(unittest.TestCase):
    def test_backup_is_valid_and_contains_existing_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.db"
            backup = root / "backup.db"
            connection = sqlite3.connect(source)
            connection.execute("CREATE TABLE novels (id INTEGER PRIMARY KEY, title TEXT)")
            connection.execute("INSERT INTO novels(title) VALUES ('保留小说')")
            connection.commit()
            connection.close()

            _backup_sqlite(source, backup)
            info = _validate_sqlite_backup(backup)
            self.assertTrue(info["has_novels"])
            self.assertGreaterEqual(info["tables"], 1)
            restored = sqlite3.connect(backup)
            try:
                self.assertEqual(restored.execute("SELECT title FROM novels").fetchone()[0], "保留小说")
            finally:
                restored.close()

    def test_rejects_non_sqlite_and_empty_database(self):
        with tempfile.TemporaryDirectory() as directory:
            invalid = Path(directory) / "invalid.db"
            invalid.write_text("not a database", encoding="utf-8")
            with self.assertRaises(ValueError):
                _validate_sqlite_backup(invalid)

            empty = Path(directory) / "empty.db"
            connection = sqlite3.connect(empty)
            connection.execute("CREATE TABLE unrelated (id INTEGER)")
            connection.commit()
            connection.close()
            with self.assertRaises(ValueError):
                _validate_sqlite_backup(empty)

    def test_rejects_corrupted_database(self):
        with tempfile.TemporaryDirectory() as directory:
            corrupt = Path(directory) / "corrupt.db"
            corrupt.write_bytes(b"SQLite format 3\x00" + b"x" * 200)
            with self.assertRaises(ValueError):
                _validate_sqlite_backup(corrupt)


if __name__ == "__main__":
    unittest.main()
