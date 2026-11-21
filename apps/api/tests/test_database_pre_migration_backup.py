import sqlite3
import tempfile
from contextlib import contextmanager
import unittest
from unittest.mock import patch
from pathlib import Path

from sqlalchemy import create_engine, inspect
from sqlalchemy.pool import NullPool

from app import database



@contextmanager
def sqlite_connection(path):
    """Close SQLite handles explicitly; sqlite3's connection context manager does not close."""
    connection = sqlite3.connect(path)
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


class DatabasePreMigrationBackupTests(unittest.TestCase):
    def test_pre_migration_backup_includes_uncheckpointed_wal_rows(self):
        with tempfile.TemporaryDirectory(prefix="comic-db-wal-backup-") as temp:
            root = Path(temp)
            db_path = root / "wal-live.db"
            previous_path = database.DATABASE_PATH
            previous_storage = database.STORAGE_DIR
            connection = sqlite3.connect(db_path)
            try:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA wal_autocheckpoint=0")
                connection.execute(
                    "CREATE TABLE wal_marker (id INTEGER PRIMARY KEY, value TEXT NOT NULL)"
                )
                connection.commit()
                connection.execute(
                    "INSERT INTO wal_marker (value) VALUES (?)",
                    ("committed-but-not-checkpointed",),
                )
                connection.commit()
                self.assertTrue(Path(str(db_path) + "-wal").exists())

                database.DATABASE_PATH = db_path
                database.STORAGE_DIR = root / "storage"
                backup_path = database._backup_database_before_migration()
                self.assertIsNotNone(backup_path)
                with sqlite_connection(backup_path) as backup:
                    self.assertEqual(
                        backup.execute("SELECT value FROM wal_marker WHERE id=1").fetchone(),
                        ("committed-but-not-checkpointed",),
                    )
                    self.assertEqual(
                        backup.execute("PRAGMA integrity_check").fetchone(),
                        ("ok",),
                    )
            finally:
                connection.close()
                database.DATABASE_PATH = previous_path
                database.STORAGE_DIR = previous_storage

    def test_existing_database_is_backed_up_before_create_all(self):
        with tempfile.TemporaryDirectory(prefix="comic-db-backup-test-") as temp:
            root = Path(temp)
            db_path = root / "existing.db"
            storage_path = root / "storage"

            with sqlite_connection(db_path) as connection:
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

                with sqlite_connection(backups[0]) as backup:
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

                with sqlite_connection(db_path) as live:
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
            with sqlite_connection(db_path) as connection:
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

                with sqlite_connection(db_path) as restored:
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
                with sqlite_connection(db_path) as connection:
                    rows = connection.execute(
                        "SELECT version, name FROM schema_migrations ORDER BY version"
                    ).fetchall()
                self.assertEqual(rows, [(1, "baseline_schema")])
            finally:
                test_engine.dispose()
                database.engine = previous_engine
                database.DATABASE_PATH = previous_path
                database.STORAGE_DIR = previous_storage


    def test_corrupt_rollback_snapshot_is_rejected_before_live_database_is_touched(self):
        with tempfile.TemporaryDirectory(prefix="comic-db-corrupt-rollback-") as temp:
            root = Path(temp)
            db_path = root / "live.db"
            backup_path = root / "corrupt-backup.db"
            with sqlite_connection(db_path) as connection:
                connection.execute(
                    "CREATE TABLE protected_data (id INTEGER PRIMARY KEY, value TEXT NOT NULL)"
                )
                connection.execute(
                    "INSERT INTO protected_data (value) VALUES (?)",
                    ("must-remain-untouched",),
                )
            backup_path.write_bytes(b"SQLite format 3\\x00" + b"corrupt-snapshot" * 32)

            previous_engine = database.engine
            previous_path = database.DATABASE_PATH
            test_engine = create_engine(
                f"sqlite:///{db_path.as_posix()}",
                connect_args={"check_same_thread": False, "timeout": 30},
                poolclass=NullPool,
                future=True,
            )
            try:
                database.engine = test_engine
                database.DATABASE_PATH = db_path
                with self.assertRaisesRegex(RuntimeError, "不是有效的 SQLite 数据库"):
                    database._restore_database_backup(backup_path)

                with sqlite_connection(db_path) as connection:
                    self.assertEqual(
                        connection.execute(
                            "SELECT value FROM protected_data WHERE id=1"
                        ).fetchone(),
                        ("must-remain-untouched",),
                    )
                    self.assertEqual(
                        connection.execute("PRAGMA integrity_check").fetchone(),
                        ("ok",),
                    )
                with test_engine.connect() as connection:
                    self.assertEqual(
                        connection.exec_driver_sql(
                            "SELECT value FROM protected_data WHERE id=1"
                        ).scalar_one(),
                        "must-remain-untouched",
                    )
            finally:
                test_engine.dispose()
                database.engine = previous_engine
                database.DATABASE_PATH = previous_path

    def test_newer_schema_is_rejected_before_any_mutation(self):
        with tempfile.TemporaryDirectory(prefix="comic-newer-schema-") as temp:
            root = Path(temp)
            db_path = root / "newer.db"
            storage_path = root / "storage"
            with sqlite_connection(db_path) as connection:
                connection.execute(
                    "CREATE TABLE legacy_marker (id INTEGER PRIMARY KEY, value TEXT NOT NULL)"
                )
                connection.execute(
                    "INSERT INTO legacy_marker (value) VALUES (?)",
                    ("preserve-newer-database",),
                )
                connection.execute(
                    "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
                )
                connection.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                    (database.SCHEMA_VERSION + 1, "future_schema", "2099-01-01T00:00:00Z"),
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

                with self.assertRaisesRegex(RuntimeError, "任何结构变更前停止启动"):
                    database.init_database()

                with sqlite_connection(db_path) as connection:
                    tables = {
                        row[0] for row in connection.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'"
                        )
                    }
                    self.assertEqual(tables, {"legacy_marker", "schema_migrations"})
                    self.assertEqual(
                        connection.execute(
                            "SELECT value FROM legacy_marker WHERE id=1"
                        ).fetchone(),
                        ("preserve-newer-database",),
                    )
                    self.assertEqual(
                        connection.execute(
                            "SELECT version, name FROM schema_migrations"
                        ).fetchone(),
                        (database.SCHEMA_VERSION + 1, "future_schema"),
                    )
                    self.assertEqual(
                        connection.execute("PRAGMA integrity_check").fetchone(),
                        ("ok",),
                    )

                self.assertFalse((storage_path / "backups" / "schema").exists())
            finally:
                test_engine.dispose()
                database.engine = previous_engine
                database.DATABASE_PATH = previous_path
                database.STORAGE_DIR = previous_storage


if __name__ == "__main__":
    unittest.main()
