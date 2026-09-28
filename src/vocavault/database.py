"""SQLite lifecycle and schema management for VocaVault.

This module deliberately contains no catalogue business logic. It owns database
connections, versioned schema migration, and consistent SQLite backup/restore.
Each worker should obtain its own connection; connections are never shared across
threads.
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

APPLICATION_ID = 0x564F4341  # ASCII "VOCA"
DEFAULT_BUSY_TIMEOUT_MS = 5_000

_REQUIRED_TABLES_V1 = frozenset(
    {
        "schema_migrations",
        "storage_roots",
        "songs",
        "song_names",
        "contributors",
        "contributor_names",
        "workflow_statuses",
        "projects",
        "versions",
        "files",
        "song_credits",
        "project_credits",
        "version_credits",
        "tags",
        "project_tags",
        "parse_observations",
        "file_tracks",
        "dependency_references",
        "song_links",
        "project_links",
        "version_links",
        "file_links",
        "song_overrides",
        "project_overrides",
        "version_overrides",
        "file_overrides",
    }
)
_REQUIRED_TRIGGERS_V1 = frozenset(
    {
        "projects_preferred_version_insert",
        "projects_preferred_version_update",
        "preferred_version_reparent_guard",
        "versions_default_file_insert",
        "versions_default_file_update",
        "default_file_reparent_guard",
    }
)
_REQUIRED_TABLES_V2 = frozenset(
    {
        *_REQUIRED_TABLES_V1,
        "search_fts",
        "api_cache",
    }
)
_REQUIRED_TABLES_V3 = _REQUIRED_TABLES_V2
_REQUIRED_SCHEMA_OBJECTS = {
    1: {
        "table": _REQUIRED_TABLES_V1,
        "trigger": _REQUIRED_TRIGGERS_V1,
    },
    2: {
        "table": _REQUIRED_TABLES_V2,
        "trigger": _REQUIRED_TRIGGERS_V1,
    },
    3: {
        "table": _REQUIRED_TABLES_V3,
        "trigger": _REQUIRED_TRIGGERS_V1,
    },
}


class DatabaseError(RuntimeError):
    """Base class for VocaVault database lifecycle errors."""


class MigrationError(DatabaseError):
    """Raised when a migration cannot be applied atomically."""


class BackupError(DatabaseError):
    """Raised when a backup is invalid or a backup operation fails."""


@dataclass(frozen=True)
class Migration:
    version: int
    statements: tuple[str, ...]


_MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        1,
        (
            """
            CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT
                    (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            )
            """,
            """
            CREATE TABLE storage_roots (
                id TEXT PRIMARY KEY NOT NULL,
                location TEXT NOT NULL UNIQUE,
                kind TEXT NOT NULL DEFAULT 'indexed'
                    CHECK (kind IN ('indexed', 'managed')),
                availability TEXT NOT NULL DEFAULT 'unknown'
                    CHECK (availability IN ('available', 'unavailable', 'unknown')),
                is_default INTEGER NOT NULL DEFAULT 0 CHECK (is_default IN (0, 1)),
                created_at TEXT NOT NULL DEFAULT
                    (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                updated_at TEXT NOT NULL DEFAULT
                    (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            )
            """,
            """
            CREATE UNIQUE INDEX one_default_storage_root
                ON storage_roots(is_default) WHERE is_default = 1
            """,
            """
            CREATE TABLE songs (
                id TEXT PRIMARY KEY NOT NULL,
                vocadb_id INTEGER UNIQUE,
                created_at TEXT NOT NULL DEFAULT
                    (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                updated_at TEXT NOT NULL DEFAULT
                    (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            )
            """,
            """
            CREATE TABLE song_names (
                id TEXT PRIMARY KEY NOT NULL,
                song_id TEXT NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
                text TEXT NOT NULL,
                normalized_text TEXT NOT NULL,
                language TEXT,
                script TEXT,
                kind TEXT NOT NULL DEFAULT 'alias',
                source_type TEXT NOT NULL DEFAULT 'user',
                source_identifier TEXT,
                is_display INTEGER NOT NULL DEFAULT 0 CHECK (is_display IN (0, 1)),
                is_dismissed INTEGER NOT NULL DEFAULT 0 CHECK (is_dismissed IN (0, 1)),
                observed_at TEXT,
                created_at TEXT NOT NULL DEFAULT
                    (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            )
            """,
            """
            CREATE UNIQUE INDEX one_display_name_per_song
                ON song_names(song_id) WHERE is_display = 1
            """,
            "CREATE INDEX song_names_search ON song_names(normalized_text)",
            "CREATE INDEX song_names_owner ON song_names(song_id)",
            """
            CREATE TABLE contributors (
                id TEXT PRIMARY KEY NOT NULL,
                display_name TEXT NOT NULL,
                normalized_name TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT
                    (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                updated_at TEXT NOT NULL DEFAULT
                    (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            )
            """,
            "CREATE INDEX contributors_search ON contributors(normalized_name)",
            """
            CREATE TABLE contributor_names (
                id TEXT PRIMARY KEY NOT NULL,
                contributor_id TEXT NOT NULL
                    REFERENCES contributors(id) ON DELETE CASCADE,
                text TEXT NOT NULL,
                normalized_text TEXT NOT NULL,
                language TEXT,
                script TEXT,
                source_type TEXT NOT NULL DEFAULT 'user',
                is_dismissed INTEGER NOT NULL DEFAULT 0 CHECK (is_dismissed IN (0, 1))
            )
            """,
            "CREATE INDEX contributor_names_search ON contributor_names(normalized_text)",
            """
            CREATE TABLE workflow_statuses (
                id TEXT PRIMARY KEY NOT NULL,
                name TEXT NOT NULL UNIQUE,
                normalized_name TEXT NOT NULL UNIQUE,
                display_order INTEGER NOT NULL DEFAULT 0
            )
            """,
            """
            CREATE TABLE projects (
                id TEXT PRIMARY KEY NOT NULL,
                song_id TEXT REFERENCES songs(id) ON DELETE SET NULL,
                name TEXT NOT NULL,
                normalized_name TEXT NOT NULL,
                description TEXT,
                workflow_status_id TEXT
                    REFERENCES workflow_statuses(id) ON DELETE SET NULL,
                preferred_version_id TEXT
                    REFERENCES versions(id) ON DELETE SET NULL DEFERRABLE INITIALLY DEFERRED,
                created_at TEXT NOT NULL DEFAULT
                    (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                updated_at TEXT NOT NULL DEFAULT
                    (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            )
            """,
            "CREATE INDEX projects_search ON projects(normalized_name)",
            "CREATE INDEX projects_song ON projects(song_id)",
            "CREATE INDEX projects_status ON projects(workflow_status_id)",
            """
            CREATE TABLE versions (
                id TEXT PRIMARY KEY NOT NULL,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                label TEXT NOT NULL,
                normalized_label TEXT NOT NULL,
                sort_order INTEGER NOT NULL DEFAULT 0,
                notes TEXT,
                default_file_id TEXT
                    REFERENCES files(id) ON DELETE SET NULL DEFERRABLE INITIALLY DEFERRED,
                created_at TEXT NOT NULL DEFAULT
                    (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                updated_at TEXT NOT NULL DEFAULT
                    (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                UNIQUE (project_id, sort_order)
            )
            """,
            "CREATE INDEX versions_project ON versions(project_id)",
            """
            CREATE TABLE files (
                id TEXT PRIMARY KEY NOT NULL,
                version_id TEXT NOT NULL REFERENCES versions(id) ON DELETE CASCADE,
                storage_root_id TEXT REFERENCES storage_roots(id) ON DELETE SET NULL,
                locator TEXT NOT NULL UNIQUE,
                relative_path TEXT,
                management_mode TEXT NOT NULL DEFAULT 'indexed'
                    CHECK (management_mode IN ('indexed', 'managed')),
                role TEXT NOT NULL DEFAULT 'project',
                is_present INTEGER CHECK (is_present IN (0, 1)),
                size_bytes INTEGER CHECK (size_bytes IS NULL OR size_bytes >= 0),
                modified_ns INTEGER,
                content_hash TEXT,
                detected_format TEXT,
                detected_version TEXT,
                created_at TEXT NOT NULL DEFAULT
                    (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                updated_at TEXT NOT NULL DEFAULT
                    (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                CHECK (management_mode != 'managed' OR
                       (storage_root_id IS NOT NULL AND relative_path IS NOT NULL))
            )
            """,
            "CREATE INDEX files_version ON files(version_id)",
            "CREATE INDEX files_root ON files(storage_root_id)",
            "CREATE INDEX files_hash ON files(content_hash)",
            "CREATE INDEX files_format ON files(detected_format)",
            """
            CREATE TRIGGER projects_preferred_version_insert
            BEFORE INSERT ON projects
            WHEN NEW.preferred_version_id IS NOT NULL
             AND NOT EXISTS (
                SELECT 1 FROM versions
                 WHERE id = NEW.preferred_version_id AND project_id = NEW.id
             )
            BEGIN
                SELECT RAISE(ABORT, 'preferred version must belong to project');
            END
            """,
            """
            CREATE TRIGGER projects_preferred_version_update
            BEFORE UPDATE OF preferred_version_id ON projects
            WHEN NEW.preferred_version_id IS NOT NULL
             AND NOT EXISTS (
                SELECT 1 FROM versions
                 WHERE id = NEW.preferred_version_id AND project_id = NEW.id
             )
            BEGIN
                SELECT RAISE(ABORT, 'preferred version must belong to project');
            END
            """,
            """
            CREATE TRIGGER preferred_version_reparent_guard
            BEFORE UPDATE OF project_id ON versions
            WHEN NEW.project_id != OLD.project_id
             AND EXISTS (
                SELECT 1 FROM projects
                 WHERE id = OLD.project_id AND preferred_version_id = OLD.id
             )
            BEGIN
                SELECT RAISE(ABORT, 'cannot move a preferred version');
            END
            """,
            """
            CREATE TRIGGER versions_default_file_insert
            BEFORE INSERT ON versions
            WHEN NEW.default_file_id IS NOT NULL
             AND NOT EXISTS (
                SELECT 1 FROM files
                 WHERE id = NEW.default_file_id AND version_id = NEW.id
             )
            BEGIN
                SELECT RAISE(ABORT, 'default file must belong to version');
            END
            """,
            """
            CREATE TRIGGER versions_default_file_update
            BEFORE UPDATE OF default_file_id ON versions
            WHEN NEW.default_file_id IS NOT NULL
             AND NOT EXISTS (
                SELECT 1 FROM files
                 WHERE id = NEW.default_file_id AND version_id = NEW.id
             )
            BEGIN
                SELECT RAISE(ABORT, 'default file must belong to version');
            END
            """,
            """
            CREATE TRIGGER default_file_reparent_guard
            BEFORE UPDATE OF version_id ON files
            WHEN NEW.version_id != OLD.version_id
             AND EXISTS (
                SELECT 1 FROM versions
                 WHERE id = OLD.version_id AND default_file_id = OLD.id
             )
            BEGIN
                SELECT RAISE(ABORT, 'cannot move a default file');
            END
            """,
            """
            CREATE TABLE song_credits (
                id TEXT PRIMARY KEY NOT NULL,
                song_id TEXT NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
                contributor_id TEXT NOT NULL REFERENCES contributors(id) ON DELETE RESTRICT,
                role TEXT NOT NULL,
                source_type TEXT NOT NULL DEFAULT 'user',
                source_identifier TEXT,
                UNIQUE (song_id, contributor_id, role, source_type)
            )
            """,
            """
            CREATE TABLE project_credits (
                id TEXT PRIMARY KEY NOT NULL,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                contributor_id TEXT NOT NULL REFERENCES contributors(id) ON DELETE RESTRICT,
                role TEXT NOT NULL,
                source_type TEXT NOT NULL DEFAULT 'user',
                source_identifier TEXT,
                UNIQUE (project_id, contributor_id, role, source_type)
            )
            """,
            """
            CREATE TABLE version_credits (
                id TEXT PRIMARY KEY NOT NULL,
                version_id TEXT NOT NULL REFERENCES versions(id) ON DELETE CASCADE,
                contributor_id TEXT NOT NULL REFERENCES contributors(id) ON DELETE RESTRICT,
                role TEXT NOT NULL,
                source_type TEXT NOT NULL DEFAULT 'user',
                source_identifier TEXT,
                UNIQUE (version_id, contributor_id, role, source_type)
            )
            """,
            """
            CREATE TABLE tags (
                id TEXT PRIMARY KEY NOT NULL,
                name TEXT NOT NULL,
                normalized_name TEXT NOT NULL UNIQUE
            )
            """,
            """
            CREATE TABLE project_tags (
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                tag_id TEXT NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
                PRIMARY KEY (project_id, tag_id)
            ) WITHOUT ROWID
            """,
            """
            CREATE TABLE parse_observations (
                id TEXT PRIMARY KEY NOT NULL,
                file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
                content_hash TEXT NOT NULL,
                parser_id TEXT NOT NULL,
                parser_version TEXT NOT NULL,
                status TEXT NOT NULL CHECK (
                    status IN ('not_parsed', 'parsed', 'partial', 'unsupported', 'failed')
                ),
                observed_at TEXT NOT NULL DEFAULT
                    (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                is_stale INTEGER NOT NULL DEFAULT 0 CHECK (is_stale IN (0, 1)),
                initial_bpm REAL,
                minimum_bpm REAL,
                maximum_bpm REAL,
                tempo_change_count INTEGER CHECK (
                    tempo_change_count IS NULL OR tempo_change_count >= 0
                ),
                tempo_map_json TEXT,
                lyric_note_count INTEGER CHECK (
                    lyric_note_count IS NULL OR lyric_note_count >= 0
                ),
                populated_lyric_count INTEGER CHECK (
                    populated_lyric_count IS NULL OR populated_lyric_count >= 0
                ),
                details_json TEXT,
                warnings_json TEXT,
                error_message TEXT,
                UNIQUE (file_id, content_hash, parser_id, parser_version)
            )
            """,
            "CREATE INDEX observations_file ON parse_observations(file_id, observed_at)",
            "CREATE INDEX observations_health ON parse_observations(status, is_stale)",
            """
            CREATE TABLE file_tracks (
                id TEXT PRIMARY KEY NOT NULL,
                observation_id TEXT NOT NULL
                    REFERENCES parse_observations(id) ON DELETE CASCADE,
                track_index INTEGER NOT NULL CHECK (track_index >= 0),
                part_index INTEGER,
                name TEXT,
                track_kind TEXT NOT NULL DEFAULT 'unknown'
                    CHECK (track_kind IN ('vocal', 'audio', 'unknown')),
                engine TEXT,
                voice_identifier TEXT,
                voice_name TEXT,
                normalized_voice_name TEXT,
                declared_language TEXT,
                note_count INTEGER NOT NULL DEFAULT 0 CHECK (note_count >= 0),
                lyric_count INTEGER NOT NULL DEFAULT 0 CHECK (lyric_count >= 0),
                pitch_state TEXT NOT NULL DEFAULT 'unknown'
                    CHECK (pitch_state IN ('unknown', 'none_detected', 'detected')),
                vibrato_state TEXT NOT NULL DEFAULT 'unknown'
                    CHECK (vibrato_state IN ('unknown', 'none_detected', 'detected')),
                dynamics_state TEXT NOT NULL DEFAULT 'unknown'
                    CHECK (dynamics_state IN ('unknown', 'none_detected', 'detected')),
                details_json TEXT,
                UNIQUE (observation_id, track_index, part_index)
            )
            """,
            "CREATE INDEX tracks_observation ON file_tracks(observation_id)",
            "CREATE INDEX tracks_filters ON file_tracks(engine, declared_language, normalized_voice_name)",
            """
            CREATE TABLE dependency_references (
                id TEXT PRIMARY KEY NOT NULL,
                observation_id TEXT NOT NULL
                    REFERENCES parse_observations(id) ON DELETE CASCADE,
                original_reference TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'unknown',
                resolution_base TEXT,
                resolved_path TEXT,
                availability TEXT NOT NULL DEFAULT 'unknown'
                    CHECK (availability IN ('available', 'missing', 'unavailable', 'unknown'))
            )
            """,
            "CREATE INDEX dependencies_observation ON dependency_references(observation_id)",
            """
            CREATE TABLE song_links (
                id TEXT PRIMARY KEY NOT NULL,
                song_id TEXT NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
                kind TEXT NOT NULL,
                url TEXT NOT NULL,
                label TEXT
            )
            """,
            """
            CREATE TABLE project_links (
                id TEXT PRIMARY KEY NOT NULL,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                kind TEXT NOT NULL,
                url TEXT NOT NULL,
                label TEXT
            )
            """,
            """
            CREATE TABLE version_links (
                id TEXT PRIMARY KEY NOT NULL,
                version_id TEXT NOT NULL REFERENCES versions(id) ON DELETE CASCADE,
                kind TEXT NOT NULL,
                url TEXT NOT NULL,
                label TEXT,
                terms_text TEXT
            )
            """,
            """
            CREATE TABLE file_links (
                id TEXT PRIMARY KEY NOT NULL,
                file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
                kind TEXT NOT NULL,
                url TEXT NOT NULL,
                label TEXT,
                terms_text TEXT
            )
            """,
            """
            CREATE TABLE song_overrides (
                song_id TEXT NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
                field_name TEXT NOT NULL,
                value_text TEXT,
                intentionally_cleared INTEGER NOT NULL DEFAULT 0
                    CHECK (intentionally_cleared IN (0, 1)),
                updated_at TEXT NOT NULL DEFAULT
                    (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                PRIMARY KEY (song_id, field_name),
                CHECK (intentionally_cleared = 0 OR value_text IS NULL)
            ) WITHOUT ROWID
            """,
            """
            CREATE TABLE project_overrides (
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                field_name TEXT NOT NULL,
                value_text TEXT,
                intentionally_cleared INTEGER NOT NULL DEFAULT 0
                    CHECK (intentionally_cleared IN (0, 1)),
                updated_at TEXT NOT NULL DEFAULT
                    (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                PRIMARY KEY (project_id, field_name),
                CHECK (intentionally_cleared = 0 OR value_text IS NULL)
            ) WITHOUT ROWID
            """,
            """
            CREATE TABLE version_overrides (
                version_id TEXT NOT NULL REFERENCES versions(id) ON DELETE CASCADE,
                field_name TEXT NOT NULL,
                value_text TEXT,
                intentionally_cleared INTEGER NOT NULL DEFAULT 0
                    CHECK (intentionally_cleared IN (0, 1)),
                updated_at TEXT NOT NULL DEFAULT
                    (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                PRIMARY KEY (version_id, field_name),
                CHECK (intentionally_cleared = 0 OR value_text IS NULL)
            ) WITHOUT ROWID
            """,
            """
            CREATE TABLE file_overrides (
                file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
                field_name TEXT NOT NULL,
                value_text TEXT,
                intentionally_cleared INTEGER NOT NULL DEFAULT 0
                    CHECK (intentionally_cleared IN (0, 1)),
                updated_at TEXT NOT NULL DEFAULT
                    (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                PRIMARY KEY (file_id, field_name),
                CHECK (intentionally_cleared = 0 OR value_text IS NULL)
            ) WITHOUT ROWID
            """,
        ),
    ),
    Migration(
        2,
        (
            """
            CREATE VIRTUAL TABLE search_fts USING fts5(
                project_id UNINDEXED,
                entity_id UNINDEXED,
                field_type UNINDEXED,
                raw_text UNINDEXED,
                normalized_text,
                tokenize='trigram'
            )
            """,
            """
            CREATE TABLE api_cache (
                cache_key TEXT PRIMARY KEY NOT NULL,
                endpoint TEXT NOT NULL,
                query_or_id TEXT NOT NULL,
                response_json TEXT NOT NULL,
                cached_at TEXT NOT NULL DEFAULT
                    (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            )
            """,
            """
            CREATE INDEX api_cache_lookup
                ON api_cache(endpoint, query_or_id)
            """,
        ),
    ),
    Migration(
        3,
        (
            "ALTER TABLE versions ADD COLUMN distribution_terms TEXT",
        ),
    ),
)

LATEST_SCHEMA_VERSION = _MIGRATIONS[-1].version


def new_id() -> str:
    """Return a stable, application-generated identifier."""

    return str(uuid.uuid4())


@contextmanager
def transaction(
    connection: sqlite3.Connection, *, immediate: bool = False
) -> Iterator[sqlite3.Connection]:
    """Run statements in an explicit transaction, with nested savepoint support."""

    if connection.in_transaction:
        savepoint = f"vocavault_{uuid.uuid4().hex}"
        connection.execute(f"SAVEPOINT {savepoint}")
        try:
            yield connection
        except BaseException:
            connection.execute(f"ROLLBACK TO {savepoint}")
            connection.execute(f"RELEASE {savepoint}")
            raise
        else:
            connection.execute(f"RELEASE {savepoint}")
        return

    connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
    try:
        yield connection
    except BaseException:
        connection.rollback()
        raise
    else:
        connection.commit()


class Database:
    """A path-based SQLite database with short-lived configured connections."""

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
    ) -> None:
        self.path = Path(path).expanduser().resolve()
        self.busy_timeout_ms = busy_timeout_ms

    def connect(self, *, readonly: bool = False) -> sqlite3.Connection:
        """Open a configured connection owned by the caller."""

        if readonly:
            uri = f"{self.path.as_uri()}?mode=ro"
            connection = sqlite3.connect(
                uri,
                uri=True,
                isolation_level=None,
                timeout=self.busy_timeout_ms / 1_000,
            )
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(
                self.path,
                isolation_level=None,
                timeout=self.busy_timeout_ms / 1_000,
            )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {int(self.busy_timeout_ms)}")
        if not readonly:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    @contextmanager
    def connection(self, *, readonly: bool = False) -> Iterator[sqlite3.Connection]:
        """Yield a configured connection and always close it."""

        connection = self.connect(readonly=readonly)
        try:
            yield connection
        finally:
            if connection.in_transaction:
                connection.rollback()
            connection.close()

    def initialize(self) -> None:
        """Create or migrate the schema, one migration per atomic transaction."""

        with self.connection() as connection:
            self._validate_database_identity(connection)
            current = _schema_version(connection)
            if current > LATEST_SCHEMA_VERSION:
                raise MigrationError(
                    f"database schema {current} is newer than supported "
                    f"schema {LATEST_SCHEMA_VERSION}"
                )
            if current > 0:
                _require_schema(connection, error_class=MigrationError)
                _require_integrity(connection, error_class=MigrationError)
        if 0 < current < LATEST_SCHEMA_VERSION:
            timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
            backup = self.path.with_name(
                f"{self.path.stem}.pre-migration-v{current}-{timestamp}{self.path.suffix}"
            )
            self.backup_to(backup)

        with self.connection() as connection:
            self._validate_database_identity(connection)
            current = _schema_version(connection)
            if current > LATEST_SCHEMA_VERSION:
                raise MigrationError(
                    f"database schema {current} is newer than supported "
                    f"schema {LATEST_SCHEMA_VERSION}"
                )

            for migration in _MIGRATIONS:
                if migration.version <= current:
                    continue
                try:
                    with transaction(connection, immediate=True):
                        locked_version = _schema_version(connection)
                        if locked_version >= migration.version:
                            continue
                        if locked_version != migration.version - 1:
                            raise MigrationError(
                                f"cannot migrate schema {locked_version} directly to "
                                f"{migration.version}"
                            )
                        for statement in migration.statements:
                            connection.execute(statement)
                        connection.execute(
                            "INSERT INTO schema_migrations(version) VALUES (?)",
                            (migration.version,),
                        )
                        connection.execute(f"PRAGMA user_version = {migration.version}")
                        connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
                        _require_schema(connection, error_class=MigrationError)
                        _require_integrity(connection, error_class=MigrationError)
                except (sqlite3.Error, MigrationError) as error:
                    raise MigrationError(
                        f"failed to apply database migration {migration.version}"
                    ) from error
                current = migration.version

    def schema_version(self) -> int:
        with self.connection(readonly=True) as connection:
            return _schema_version(connection)

    def backup_to(self, destination: str | os.PathLike[str]) -> Path:
        """Create a consistent online backup and atomically publish it."""

        destination_path = Path(destination).expanduser().resolve()
        if destination_path == self.path:
            raise BackupError("backup destination must differ from the live database")
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = destination_path.with_name(
            f".{destination_path.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            with self.connection(readonly=True) as source:
                self._require_vocavault_database(source)
                target = sqlite3.connect(temporary_path, isolation_level=None)
                try:
                    source.backup(target)
                    _require_integrity(target)
                finally:
                    target.close()
            os.replace(temporary_path, destination_path)
        except (OSError, sqlite3.Error, DatabaseError) as error:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
            if isinstance(error, BackupError):
                raise
            raise BackupError(
                f"could not back up database to {destination_path}"
            ) from error
        return destination_path

    def restore_from(self, source: str | os.PathLike[str]) -> None:
        """Restore a validated backup using SQLite's online backup API."""

        source_path = Path(source).expanduser().resolve()
        if source_path == self.path:
            raise BackupError("restore source must differ from the live database")
        if not source_path.is_file():
            raise BackupError(f"backup does not exist: {source_path}")

        source_db = Database(source_path, busy_timeout_ms=self.busy_timeout_ms)
        try:
            with source_db.connection(readonly=True) as backup:
                source_db._require_vocavault_database(backup)
                _require_integrity(backup)
                with self.connection() as live:
                    backup.backup(live)
                    _require_integrity(live)
        except (sqlite3.Error, OSError, DatabaseError) as error:
            if isinstance(error, BackupError):
                raise
            raise BackupError(
                f"could not restore database from {source_path}"
            ) from error

    @staticmethod
    def _validate_database_identity(connection: sqlite3.Connection) -> None:
        application_id = int(connection.execute("PRAGMA application_id").fetchone()[0])
        if application_id not in (0, APPLICATION_ID):
            raise MigrationError("file belongs to a different SQLite application")
        if application_id == 0 and _schema_version(connection) > 0:
            raise MigrationError(
                "versioned database has no VocaVault application identity"
            )
        if application_id == 0 and _schema_version(connection) == 0:
            user_tables = connection.execute(
                """
                SELECT name FROM sqlite_master
                 WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                 LIMIT 1
                """
            ).fetchone()
            if user_tables is not None:
                raise MigrationError(
                    "refusing to initialize a non-empty foreign database"
                )

    @staticmethod
    def _require_vocavault_database(connection: sqlite3.Connection) -> None:
        application_id = int(connection.execute("PRAGMA application_id").fetchone()[0])
        version = _schema_version(connection)
        if application_id != APPLICATION_ID or version < 1:
            raise BackupError("file is not an initialized VocaVault database")
        if version > LATEST_SCHEMA_VERSION:
            raise BackupError("database schema is newer than this application")
        _require_schema(connection)


def _schema_version(connection: sqlite3.Connection) -> int:
    return int(connection.execute("PRAGMA user_version").fetchone()[0])


def _require_schema(
    connection: sqlite3.Connection,
    *,
    error_class: type[DatabaseError] = BackupError,
) -> None:
    version = _schema_version(connection)
    try:
        objects = connection.execute(
            """
            SELECT type, name FROM sqlite_master
             WHERE type IN ('table', 'trigger')
            """
        ).fetchall()
        found: dict[str, set[str]] = {"table": set(), "trigger": set()}
        for row in objects:
            found[row[0]].add(row[1])

        for migration_version, required_by_type in _REQUIRED_SCHEMA_OBJECTS.items():
            if migration_version > version:
                continue
            for object_type, required_names in required_by_type.items():
                missing = required_names - found[object_type]
                if missing:
                    missing_list = ", ".join(sorted(missing))
                    raise error_class(
                        f"database schema is missing {object_type}(s): {missing_list}"
                    )

        history = [
            int(row[0])
            for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            )
        ]
    except sqlite3.Error as error:
        raise error_class("database schema could not be validated") from error

    expected_history = [
        migration.version for migration in _MIGRATIONS if migration.version <= version
    ]
    if history != expected_history:
        raise error_class(
            "database migration history does not match its schema version"
        )


def _require_integrity(
    connection: sqlite3.Connection,
    *,
    error_class: type[DatabaseError] = BackupError,
) -> None:
    try:
        result = connection.execute("PRAGMA quick_check").fetchone()
    except sqlite3.Error as error:
        raise error_class("database integrity check could not be completed") from error
    if result is None or result[0] != "ok":
        detail = "no result" if result is None else str(result[0])
        raise error_class(f"database integrity check failed: {detail}")
    try:
        foreign_key_error = connection.execute("PRAGMA foreign_key_check").fetchone()
    except sqlite3.Error as error:
        raise error_class(
            "database foreign-key check could not be completed"
        ) from error
    if foreign_key_error is not None:
        raise error_class(
            f"database foreign-key check failed in table {foreign_key_error[0]}"
        )

    try:
        preferred_owner_error = connection.execute(
            """
            SELECT p.id FROM projects p
            JOIN versions v ON v.id = p.preferred_version_id
            WHERE v.project_id != p.id LIMIT 1
            """
        ).fetchone()
        default_owner_error = connection.execute(
            """
            SELECT v.id FROM versions v
            JOIN files f ON f.id = v.default_file_id
            WHERE f.version_id != v.id LIMIT 1
            """
        ).fetchone()
    except sqlite3.Error as error:
        raise error_class("database ownership checks could not be completed") from error
    if preferred_owner_error is not None:
        raise error_class("preferred version belongs to a different project")

    if default_owner_error is not None:
        raise error_class("default file belongs to a different version")


def initialize_database(path: str | os.PathLike[str]) -> Database:
    """Return an initialized :class:`Database`."""

    database = Database(path)
    database.initialize()
    return database


__all__: Sequence[str] = (
    "APPLICATION_ID",
    "LATEST_SCHEMA_VERSION",
    "BackupError",
    "Database",
    "DatabaseError",
    "MigrationError",
    "initialize_database",
    "new_id",
    "transaction",
)
