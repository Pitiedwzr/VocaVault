from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from vocavault import database as database_module
from vocavault.database import (
    APPLICATION_ID,
    LATEST_SCHEMA_VERSION,
    BackupError,
    Database,
    Migration,
    MigrationError,
    new_id,
    transaction,
)


class DatabaseTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.database = Database(self.root / "library.sqlite3")

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_initialize_is_idempotent_and_configures_connections(self) -> None:
        self.database.initialize()
        self.database.initialize()

        with self.database.connection() as connection:
            self.assertEqual(
                connection.execute("PRAGMA user_version").fetchone()[0],
                LATEST_SCHEMA_VERSION,
            )
            self.assertEqual(
                connection.execute("PRAGMA application_id").fetchone()[0],
                APPLICATION_ID,
            )
            self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            applied = connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
            self.assertEqual([row[0] for row in applied], [LATEST_SCHEMA_VERSION])

    def test_foreign_keys_and_selection_ownership_are_enforced(self) -> None:
        self.database.initialize()
        with self.database.connection() as connection, transaction(connection):
            connection.execute(
                "INSERT INTO projects(id, name, normalized_name) VALUES (?, ?, ?)",
                ("project-a", "Project A", "project a"),
            )
            connection.execute(
                "INSERT INTO projects(id, name, normalized_name) VALUES (?, ?, ?)",
                ("project-b", "Project B", "project b"),
            )
            connection.execute(
                """
                INSERT INTO versions(id, project_id, label, normalized_label, sort_order)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("version-a", "project-a", "Initial", "initial", 0),
            )
            connection.execute(
                """
                INSERT INTO versions(id, project_id, label, normalized_label, sort_order)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("version-b", "project-b", "Initial", "initial", 0),
            )
            connection.execute(
                """
                INSERT INTO files(id, version_id, locator)
                VALUES ('file-a', 'version-a', 'C:/music/a.svp')
                """
            )
            connection.execute(
                """
                INSERT INTO files(id, version_id, locator)
                VALUES ('file-b', 'version-b', 'C:/music/b.svp')
                """
            )

            with self.assertRaisesRegex(sqlite3.IntegrityError, "must belong"):
                connection.execute(
                    "UPDATE projects SET preferred_version_id = ? WHERE id = ?",
                    ("version-b", "project-a"),
                )
            connection.execute(
                "UPDATE projects SET preferred_version_id = ? WHERE id = ?",
                ("version-a", "project-a"),
            )
            with self.assertRaisesRegex(sqlite3.IntegrityError, "must belong"):
                connection.execute(
                    "UPDATE versions SET default_file_id = ? WHERE id = ?",
                    ("file-b", "version-a"),
                )
            connection.execute(
                "UPDATE versions SET default_file_id = ? WHERE id = ?",
                ("file-a", "version-a"),
            )
            with self.assertRaisesRegex(sqlite3.IntegrityError, "preferred version"):
                connection.execute(
                    "UPDATE versions SET project_id = ? WHERE id = ?",
                    ("project-b", "version-a"),
                )
            with self.assertRaisesRegex(sqlite3.IntegrityError, "default file"):
                connection.execute(
                    "UPDATE files SET version_id = ? WHERE id = ?",
                    ("version-b", "file-a"),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO files(id, version_id, locator)
                    VALUES ('orphan', 'missing-version', 'C:/music/orphan.svp')
                    """
                )

    def test_transaction_rolls_back_and_nested_savepoint_is_isolated(self) -> None:
        self.database.initialize()
        with self.database.connection() as connection:
            with transaction(connection):
                connection.execute(
                    "INSERT INTO tags(id, name, normalized_name) VALUES ('one', 'One', 'one')"
                )
                with self.assertRaises(ValueError), transaction(connection):
                    connection.execute(
                        """
                        INSERT INTO tags(id, name, normalized_name)
                        VALUES ('two', 'Two', 'two')
                        """
                    )
                    raise ValueError("cancel nested work")
            tags = connection.execute("SELECT id FROM tags ORDER BY id").fetchall()
            self.assertEqual([row[0] for row in tags], ["one"])

    def test_failed_migration_is_atomic(self) -> None:
        self.database.initialize()
        broken = Migration(
            LATEST_SCHEMA_VERSION + 1,
            (
                "CREATE TABLE should_be_rolled_back(id INTEGER PRIMARY KEY)",
                "THIS IS NOT VALID SQL",
            ),
        )
        migrations = database_module._MIGRATIONS + (broken,)
        with (
            mock.patch.object(database_module, "_MIGRATIONS", migrations),
            mock.patch.object(database_module, "LATEST_SCHEMA_VERSION", broken.version),
            self.assertRaises(MigrationError),
        ):
            self.database.initialize()

        with self.database.connection() as connection:
            self.assertEqual(
                connection.execute("PRAGMA user_version").fetchone()[0],
                LATEST_SCHEMA_VERSION,
            )
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'should_be_rolled_back'"
            ).fetchone()
            self.assertIsNone(table)

        migration_backups = list(self.root.glob("library.pre-migration-v1-*.sqlite3"))
        self.assertEqual(len(migration_backups), 1)
        self.assertEqual(Database(migration_backups[0]).schema_version(), 1)

    def test_successful_migration_creates_usable_pre_upgrade_backup(self) -> None:
        self.database.initialize()
        with self.database.connection() as connection, transaction(connection):
            connection.execute(
                "INSERT INTO projects(id, name, normalized_name) VALUES (?, ?, ?)",
                ("before-upgrade", "Before upgrade", "before upgrade"),
            )

        migration = Migration(
            LATEST_SCHEMA_VERSION + 1,
            ("CREATE TABLE future_feature(id TEXT PRIMARY KEY NOT NULL)",),
        )
        migrations = database_module._MIGRATIONS + (migration,)
        with (
            mock.patch.object(database_module, "_MIGRATIONS", migrations),
            mock.patch.object(
                database_module, "LATEST_SCHEMA_VERSION", migration.version
            ),
        ):
            self.database.initialize()

        migration_backups = list(self.root.glob("library.pre-migration-v1-*.sqlite3"))
        self.assertEqual(len(migration_backups), 1)
        backup = Database(migration_backups[0])
        self.assertEqual(backup.schema_version(), 1)
        with backup.connection(readonly=True) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT name FROM projects WHERE id = 'before-upgrade'"
                ).fetchone()[0],
                "Before upgrade",
            )
            self.assertIsNone(
                connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE name = 'future_feature'"
                ).fetchone()
            )
        with self.database.connection() as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertIsNotNone(
                connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE name = 'future_feature'"
                ).fetchone()
            )

    def test_online_backup_and_restore_round_trip(self) -> None:
        self.database.initialize()
        with self.database.connection() as connection, transaction(connection):
            connection.execute(
                "INSERT INTO projects(id, name, normalized_name) VALUES (?, ?, ?)",
                (new_id(), "燈", "燈"),
            )

        backup_path = self.database.backup_to(
            self.root / "backups" / "metadata.sqlite3"
        )
        with self.database.connection() as connection, transaction(connection):
            connection.execute("DELETE FROM projects")
        self.database.restore_from(backup_path)

        with self.database.connection() as connection:
            projects = connection.execute("SELECT name FROM projects").fetchall()
            self.assertEqual([row[0] for row in projects], ["燈"])
            self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)

    def test_restore_rejects_non_vocavault_database(self) -> None:
        foreign_path = self.root / "foreign.sqlite3"
        connection = sqlite3.connect(foreign_path)
        connection.execute("CREATE TABLE foreign_data(value TEXT)")
        connection.commit()
        connection.close()

        with self.assertRaises(BackupError):
            self.database.restore_from(foreign_path)

    def test_restore_rejects_incomplete_branded_database_and_preserves_live(
        self,
    ) -> None:
        self.database.initialize()
        with self.database.connection() as connection, transaction(connection):
            connection.execute(
                "INSERT INTO projects(id, name, normalized_name) VALUES (?, ?, ?)",
                ("live", "Live project", "live project"),
            )

        incomplete_path = self.root / "incomplete.sqlite3"
        incomplete = sqlite3.connect(incomplete_path)
        incomplete.execute(
            "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY)"
        )
        incomplete.execute("INSERT INTO schema_migrations(version) VALUES (1)")
        incomplete.execute(f"PRAGMA application_id = {APPLICATION_ID}")
        incomplete.execute("PRAGMA user_version = 1")
        incomplete.commit()
        incomplete.close()

        with self.assertRaisesRegex(BackupError, "missing table"):
            self.database.restore_from(incomplete_path)
        with self.database.connection() as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT name FROM projects WHERE id = 'live'"
                ).fetchone()[0],
                "Live project",
            )

    def test_restore_rejects_foreign_key_violations_and_preserves_live(self) -> None:
        self.database.initialize()
        with self.database.connection() as connection, transaction(connection):
            connection.execute(
                "INSERT INTO projects(id, name, normalized_name) VALUES (?, ?, ?)",
                ("live", "Live project", "live project"),
            )
        damaged_path = self.database.backup_to(self.root / "damaged.sqlite3")
        damaged = sqlite3.connect(damaged_path)
        damaged.execute(
            """
            INSERT INTO projects(id, song_id, name, normalized_name)
            VALUES ('broken', 'missing-song', 'Broken', 'broken')
            """
        )
        damaged.commit()
        damaged.close()

        with self.assertRaisesRegex(BackupError, "foreign-key check failed"):
            self.database.restore_from(damaged_path)
        with self.database.connection() as connection:
            projects = connection.execute(
                "SELECT id FROM projects ORDER BY id"
            ).fetchall()
            self.assertEqual([row[0] for row in projects], ["live"])

    def test_restore_rejects_cross_owner_selection(self) -> None:
        self.database.initialize()
        with self.database.connection() as connection, transaction(connection):
            for project_id in ("project-a", "project-b"):
                connection.execute(
                    "INSERT INTO projects(id, name, normalized_name) VALUES (?, ?, ?)",
                    (project_id, project_id, project_id),
                )
                connection.execute(
                    """
                    INSERT INTO versions(
                        id, project_id, label, normalized_label, sort_order
                    ) VALUES (?, ?, 'Default', 'default', 0)
                    """,
                    (f"version-{project_id[-1]}", project_id),
                )
        damaged_path = self.database.backup_to(self.root / "wrong-owner.sqlite3")

        damaged = sqlite3.connect(damaged_path)
        trigger_sql = damaged.execute(
            """
            SELECT sql FROM sqlite_master
             WHERE type = 'trigger' AND name = 'projects_preferred_version_update'
            """
        ).fetchone()[0]
        damaged.execute("DROP TRIGGER projects_preferred_version_update")
        damaged.execute(
            """
            UPDATE projects SET preferred_version_id = 'version-b'
             WHERE id = 'project-a'
            """
        )
        damaged.execute(trigger_sql)
        damaged.commit()
        damaged.close()

        with self.assertRaisesRegex(BackupError, "different project"):
            self.database.restore_from(damaged_path)

    def test_restore_rejects_newer_schema_before_replacing_live(self) -> None:
        self.database.initialize()
        with self.database.connection() as connection, transaction(connection):
            connection.execute(
                "INSERT INTO projects(id, name, normalized_name) VALUES (?, ?, ?)",
                ("live", "Live project", "live project"),
            )
        newer_path = self.database.backup_to(self.root / "newer.sqlite3")
        newer = sqlite3.connect(newer_path)
        newer_version = LATEST_SCHEMA_VERSION + 1
        newer.execute(
            "INSERT INTO schema_migrations(version) VALUES (?)", (newer_version,)
        )
        newer.execute(f"PRAGMA user_version = {newer_version}")
        newer.commit()
        newer.close()

        with self.assertRaisesRegex(BackupError, "newer"):
            self.database.restore_from(newer_path)
        with self.database.connection() as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT name FROM projects WHERE id = 'live'"
                ).fetchone()[0],
                "Live project",
            )


if __name__ == "__main__":
    unittest.main()
