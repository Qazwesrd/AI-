import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from sqlalchemy import create_engine, inspect
from sqlalchemy.pool import NullPool

from app import database


class DatabasePreMigrationBackupTests(unittest.TestCase):
    def test_existing_database_is_backed_up_before_create_all(self):
        with tempfile.TemporaryDirectory(prefix="comic-db-backup-test-") as temp:
            root = Path(temp)
            db_path = root / "existing.db"
            storage_path = root / "storage"

            with sqlite3.connect(db_path) as connection:
                connection.execute(
                    "CREATE TABLE legacy_marker (id INTEGER PRIMARY KEY, value TEXT NOT NULL)"
                )
                connection.execute(
                    "INSERT INTO legacy_marker (value) VALUES (?)",
                    ("keep-this-row",),
                )

            previous_engine = database.engine
            previous_path = database.DATABASE_PATH
            previous_storage = database.STORAGE_DIR
            test_engine = create_engine(
                f"sqlite:///{db_path.as_posix()}",
                connect_args={"check_same_thread": False, "timeout": 30},
                poolclass=NullPool,
                future=True,
            )

            try:
                database.engine = test_engine
                database.DATABASE_PATH = db_path
                database.STORAGE_DIR = storage_path

                self.assertTrue(database.init_database())

                backup_dir = storage_path / "backups" / "schema"
                backups = list(backup_dir.glob("*.db"))
                self.assertEqual(len(backups), 1)

                with sqlite3.connect(backups[0]) as backup:
                    backup_tables = {
                        row[0]
                        for row in backup.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'"
                        )
                    }
                    self.assertEqual(backup_tables, {"legacy_marker"})
                    self.assertEqual(
                        backup.execute(
                            "SELECT value FROM legacy_marker WHERE id=1"
                        ).fetchone(),
                        ("keep-this-row",),
                    )
                    self.assertEqual(
                        backup.execute("PRAGMA integrity_check").fetchone(),
                        ("ok",),
                    )

                live_tables = set(inspect(test_engine).get_table_names())
                self.assertIn("legacy_marker", live_tables)
                self.assertGreater(len(live_tables), 1)

                with sqlite3.connect(db_path) as live:
                    self.assertEqual(
                        live.execute(
                            "SELECT value FROM legacy_marker WHERE id=1"
                        ).fetchone(),
                        ("keep-this-row",),
                    )
            finally:
                test_engine.dispose()
                database.engine = previous_engine
                database.DATABASE_PATH = previous_path
                database.STORAGE_DIR = previous_storage

    def test_failed_migration_restores_original_database_snapshot(self):
        with tempfile.TemporaryDirectory(prefix="comic-db-rollback-test-") as temp:
            root = Path(temp)
            db_path = root / "existing.db"
            storage_path = root / "storage"
            with sqlite3.connect(db_path) as connection:
                connection.execute(
                    "CREATE TABLE legacy_marker (id INTEGER PRIMARY KEY, value TEXT NOT NULL)"
                )
                connection.execute(
                    "INSERT INTO legacy_marker (value) VALUES (?)",
                    ("must-survive-failed-migration",),
                )

            previous_engine = database.engine
            previous_path = database.DATABASE_PATH
            previous_storage = database.STORAGE_DIR
            test_engine = create_engine(
                f"sqlite:///{db_path.as_posix()}",
                connect_args={"check_same_thread": False, "timeout": 30},
                poolclass=NullPool,
                future=True,
            )
            try:
                database.engine = test_engine
                database.DATABASE_PATH = db_path
                database.STORAGE_DIR = storage_path
                with patch.object(database, "_migrate_missing_columns", side_effect=RuntimeError("simulated migration failure")):
                    with self.assertRaisesRegex(RuntimeError, "已自动恢复迁移前数据库备份"):
                        database.init_database()

                with sqlite3.connect(db_path) as restored:
                    tables = {
                        row[0] for row in restored.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'"
                        )
                    }
                    self.assertEqual(tables, {"legacy_marker"})
                    self.assertEqual(
                        restored.execute("SELECT value FROM legacy_marker WHERE id=1").fetchone(),
                        ("must-survive-failed-migration",),
                    )
                    self.assertEqual(restored.execute("PRAGMA integrity_check").fetchone(), ("ok",))
                backups = list((storage_path / "backups" / "schema").glob("*.db"))
                self.assertEqual(len(backups), 1)
            finally:
                test_engine.dispose()
                database.engine = previous_engine
                database.DATABASE_PATH = previous_path
                database.STORAGE_DIR = previous_storage

    def test_failed_first_initialization_removes_partial_database(self):
        with tempfile.TemporaryDirectory(prefix="comic-db-first-run-") as temp:
            root = Path(temp)
            db_path = root / "new.db"
            previous_engine = database.engine
            previous_path = database.DATABASE_PATH
            previous_storage = database.STORAGE_DIR
            test_engine = create_engine(
                f"sqlite:///{db_path.as_posix()}",
                connect_args={"check_same_thread": False, "timeout": 30},
                poolclass=NullPool,
                future=True,
            )
            try:
                database.engine = test_engine
                database.DATABASE_PATH = db_path
                database.STORAGE_DIR = root / "storage"
                with patch.object(database, "_migrate_missing_columns", side_effect=RuntimeError("simulated first-run failure")):
                    with self.assertRaisesRegex(RuntimeError, "新建的空数据库已清理"):
                        database.init_database()
                self.assertFalse(db_path.exists())
            finally:
                test_engine.dispose()
                database.engine = previous_engine
                database.DATABASE_PATH = previous_path
                database.STORAGE_DIR = previous_storage

    def test_schema_migration_ledger_is_recorded_once(self):
        with tempfile.TemporaryDirectory(prefix="comic-schema-version-") as temp:
            root = Path(temp)
            db_path = root / "versioned.db"
            previous_engine = database.engine
            previous_path = database.DATABASE_PATH
            previous_storage = database.STORAGE_DIR
            test_engine = create_engine(
                f"sqlite:///{db_path.as_posix()}",
                connect_args={"check_same_thread": False, "timeout": 30},
                poolclass=NullPool,
                future=True,
            )
            try:
                database.engine = test_engine
                database.DATABASE_PATH = db_path
                database.STORAGE_DIR = root / "storage"
                database.init_database()
                database.init_database()
                self.assertEqual(database.get_schema_version(), 1)
                with sqlite3.connect(db_path) as connection:
                    rows = connection.execute(
                        "SELECT version, name FROM schema_migrations ORDER BY version"
                    ).fetchall()
                self.assertEqual(rows, [(1, "baseline_schema")])
            finally:
                test_engine.dispose()
                database.engine = previous_engine
                database.DATABASE_PATH = previous_path
                database.STORAGE_DIR = previous_storage


if __name__ == "__main__":
    unittest.main()
