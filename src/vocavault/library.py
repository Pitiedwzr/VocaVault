"""Application services for the v0.1 external-file library."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
from collections.abc import Callable, Iterable
from functools import wraps
from pathlib import Path
from threading import RLock
from typing import Any

from .database import Database, new_id, transaction
from .fileops import FileFingerprint, fingerprint, open_path, reveal_path
from .models import Detection, ParseResult, ParseStatus
from .normalization import normalize_search_text

PROJECT_EXTENSIONS = {
    ".svp",
    ".ust",
    ".vsqx",
    ".ustx",
    ".vpr",
    ".ccs",
    ".ppsf",
    ".mid",
    ".midi",
}

_EXPORT_TABLES = (
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
)


class LibraryError(RuntimeError):
    """A user-visible library operation error."""


def _serialized_write(method):
    @wraps(method)
    def locked(self, *args, **kwargs):
        with self._write_lock:
            return method(self, *args, **kwargs)

    return locked


class LibraryService:
    """Coordinate indexing, parsing, search, editing, and backup operations.

    The service holds only a database path. Every call owns its SQLite
    connection, making the facade safe to call from separate Qt workers.
    """

    def __init__(self, database_path: str | Path) -> None:
        self._write_lock = RLock()
        self.database = Database(database_path)
        self.database.initialize()

    def close(self) -> None:
        """Present for UI lifecycle symmetry; connections are short-lived."""

    @_serialized_write
    def import_paths(
        self,
        paths: Iterable[str | Path],
        *,
        cancelled: Callable[[], bool] | None = None,
        progress: Callable[[int, int, str], None] | None = None,
    ) -> list[dict[str, Any]]:
        candidates: list[tuple[Path, Path]] = []
        outcomes: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw_path in paths:
            if cancelled and cancelled():
                return outcomes
            try:
                selected = Path(raw_path).expanduser().resolve(strict=True)
                if selected.is_dir():
                    root = selected

                    def scan_error(error: OSError) -> None:
                        outcomes.append(
                            {
                                "created": False,
                                "path": str(error.filename),
                                "error": str(error),
                            }
                        )

                    files = self._scan_projects(selected, scan_error, cancelled)
                elif selected.is_file():
                    root = selected.parent
                    files = (selected,)
                else:
                    continue
                for item in files:
                    if cancelled and cancelled():
                        return outcomes
                    locator = str(item.resolve(strict=True))
                    if locator not in seen:
                        seen.add(locator)
                        candidates.append((item, root))
            except OSError as error:
                outcomes.append(
                    {"created": False, "path": str(raw_path), "error": str(error)}
                )

        for index, (path, root) in enumerate(candidates):
            if cancelled and cancelled():
                break
            try:
                outcomes.append(self._import_one(path, root))
            except (OSError, LibraryError) as error:
                outcomes.append(
                    {"created": False, "path": str(path), "error": str(error)}
                )
            if progress:
                progress(index + 1, len(candidates), str(path))
        return outcomes

    @staticmethod
    def _scan_projects(
        root: Path,
        on_error: Callable[[OSError], None],
        cancelled: Callable[[], bool] | None,
    ) -> Iterable[Path]:
        for directory, directories, filenames in os.walk(
            root, onerror=on_error, followlinks=False
        ):
            if cancelled and cancelled():
                return
            directories[:] = [
                name
                for name in directories
                if not (Path(directory) / name).is_symlink()
                and not (Path(directory) / name).is_junction()
            ]
            for name in filenames:
                item = Path(directory) / name
                if (
                    item.suffix.casefold() in PROJECT_EXTENSIONS
                    and not item.is_symlink()
                ):
                    yield item

    def _import_one(self, path: Path, root: Path) -> dict[str, Any]:
        locator = str(path.resolve(strict=True))
        with self.database.connection() as connection:
            existing = connection.execute(
                "SELECT id FROM files WHERE locator = ?", (locator,)
            ).fetchone()
        if existing is not None:
            refreshed = self.refresh_file(existing["id"])
            refreshed["created"] = False
            return refreshed

        before = fingerprint(path)
        result = self._parse(path)
        after = fingerprint(path)
        if before != after:
            raise LibraryError(f"File changed while it was being indexed: {path}")

        project_id, version_id, file_id, root_id = (
            new_id(),
            new_id(),
            new_id(),
            new_id(),
        )
        project_name = path.stem or path.name
        detected_format = result.detection.format_name or path.suffix.casefold().lstrip(
            "."
        )
        with (
            self.database.connection() as connection,
            transaction(connection, immediate=True),
        ):
            concurrent = connection.execute(
                "SELECT id FROM files WHERE locator = ?", (locator,)
            ).fetchone()
            if concurrent is not None:
                return {
                    "created": False,
                    "file_id": concurrent["id"],
                    "path": locator,
                    "parse_status": result.status.value,
                    "warnings": list(result.warnings),
                }
            existing_root = connection.execute(
                "SELECT id FROM storage_roots WHERE location = ?", (str(root),)
            ).fetchone()
            if existing_root is None:
                connection.execute(
                    """
                    INSERT INTO storage_roots(id, location, kind, availability)
                    VALUES (?, ?, 'indexed', 'available')
                    """,
                    (root_id, str(root)),
                )
            else:
                root_id = existing_root["id"]
                connection.execute(
                    "UPDATE storage_roots SET availability = 'available', updated_at = "
                    "strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
                    (root_id,),
                )
            connection.execute(
                """
                INSERT INTO projects(id, name, normalized_name)
                VALUES (?, ?, ?)
                """,
                (project_id, project_name, normalize_search_text(project_name)),
            )
            connection.execute(
                """
                INSERT INTO versions(id, project_id, label, normalized_label, sort_order)
                VALUES (?, ?, 'Default', 'default', 0)
                """,
                (version_id, project_id),
            )
            connection.execute(
                """
                INSERT INTO files(
                    id, version_id, storage_root_id, locator, management_mode, role,
                    is_present, size_bytes, modified_ns, content_hash,
                    detected_format, detected_version
                ) VALUES (?, ?, ?, ?, 'indexed', 'project', 1, ?, ?, ?, ?, ?)
                """,
                (
                    file_id,
                    version_id,
                    root_id,
                    locator,
                    before.size,
                    before.modified_ns,
                    before.sha256,
                    detected_format,
                    result.detection.format_version,
                ),
            )
            connection.execute(
                "UPDATE versions SET default_file_id = ? WHERE id = ?",
                (file_id, version_id),
            )
            connection.execute(
                "UPDATE projects SET preferred_version_id = ? WHERE id = ?",
                (version_id, project_id),
            )
            self._store_observation(connection, file_id, before, result)
        return {
            "created": True,
            "project_id": project_id,
            "version_id": version_id,
            "file_id": file_id,
            "path": locator,
            "parse_status": result.status.value,
            "warnings": list(result.warnings),
        }

    @staticmethod
    def _parse(path: Path) -> ParseResult:
        if path.suffix.casefold() == ".svp":
            from .parsers import SvpParser

            return SvpParser().parse(path)
        format_name = path.suffix.casefold().lstrip(".") or None
        return ParseResult(
            parser_id="builtin.opaque",
            parser_version="1",
            status=ParseStatus.UNSUPPORTED,
            detection=Detection(
                False,
                format_name=format_name,
                evidence="Catalogued without a v0.1 metadata adapter",
            ),
            warnings=("Metadata extraction is not supported for this format in v0.1.",),
        )

    def _store_observation(
        self,
        connection: sqlite3.Connection,
        file_id: str,
        fingerprint_value: FileFingerprint,
        result: ParseResult,
    ) -> None:
        observation_id = new_id()
        details = {
            "capabilities": list(result.capabilities),
            "detection_evidence": result.detection.evidence,
            "tempo_has_changes": result.tempo.has_changes,
            "tempo_time_unit": result.tempo.time_unit,
            "tempo_resolution": result.tempo.resolution,
            **result.details,
        }
        connection.execute(
            """
            INSERT INTO parse_observations(
                id, file_id, content_hash, parser_id, parser_version, status,
                initial_bpm, minimum_bpm, maximum_bpm, tempo_change_count,
                tempo_map_json, lyric_note_count, populated_lyric_count,
                details_json, warnings_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(file_id, content_hash, parser_id, parser_version)
            DO UPDATE SET
                status = excluded.status,
                observed_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                is_stale = 0,
                initial_bpm = excluded.initial_bpm,
                minimum_bpm = excluded.minimum_bpm,
                maximum_bpm = excluded.maximum_bpm,
                tempo_change_count = excluded.tempo_change_count,
                tempo_map_json = excluded.tempo_map_json,
                lyric_note_count = excluded.lyric_note_count,
                populated_lyric_count = excluded.populated_lyric_count,
                details_json = excluded.details_json,
                warnings_json = excluded.warnings_json
            """,
            (
                observation_id,
                file_id,
                fingerprint_value.sha256,
                result.parser_id,
                result.parser_version,
                result.status.value,
                result.tempo.initial_bpm,
                result.tempo.minimum_bpm,
                result.tempo.maximum_bpm,
                result.tempo.change_count,
                json.dumps(result.tempo.map, ensure_ascii=False),
                sum(track.note_count for track in result.tracks)
                if "lyrics" in result.capabilities
                and result.status in (ParseStatus.PARSED, ParseStatus.PARTIAL)
                else None,
                sum(track.lyric_count for track in result.tracks)
                if "lyrics" in result.capabilities
                and result.status in (ParseStatus.PARSED, ParseStatus.PARTIAL)
                else None,
                json.dumps(details, ensure_ascii=False),
                json.dumps(result.warnings, ensure_ascii=False),
            ),
        )
        stored = connection.execute(
            """
            SELECT id FROM parse_observations
             WHERE file_id = ? AND content_hash = ? AND parser_id = ? AND parser_version = ?
            """,
            (
                file_id,
                fingerprint_value.sha256,
                result.parser_id,
                result.parser_version,
            ),
        ).fetchone()
        observation_id = stored["id"]
        connection.execute(
            "DELETE FROM file_tracks WHERE observation_id = ?", (observation_id,)
        )
        connection.execute(
            "DELETE FROM dependency_references WHERE observation_id = ?",
            (observation_id,),
        )
        for track in result.tracks:
            connection.execute(
                """
                INSERT INTO file_tracks(
                    id, observation_id, track_index, name, track_kind, engine,
                    voice_identifier, voice_name, normalized_voice_name,
                    declared_language, note_count, lyric_count, pitch_state,
                    vibrato_state, dynamics_state, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id(),
                    observation_id,
                    track.index,
                    track.name,
                    track.kind.value,
                    result.detection.format_name,
                    track.voice_identifier,
                    track.voice_name,
                    normalize_search_text(track.voice_name or ""),
                    track.languages[0] if track.languages else None,
                    track.note_count,
                    track.lyric_count,
                    track.pitch.value,
                    track.vibrato.value,
                    track.dynamics.value,
                    json.dumps(
                        {**track.details, "languages": list(track.languages)},
                        ensure_ascii=False,
                    ),
                ),
            )
        for reference in result.references:
            connection.execute(
                """
                INSERT INTO dependency_references(
                    id, observation_id, original_reference, kind, resolution_base,
                    availability
                ) VALUES (?, ?, ?, ?, ?, 'unknown')
                """,
                (
                    new_id(),
                    observation_id,
                    reference.original,
                    reference.kind,
                    reference.resolution_base,
                ),
            )

    @_serialized_write
    def refresh_file(self, file_id: str) -> dict[str, Any]:
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT f.locator, f.content_hash, sr.location AS root_location,
                       o.parser_id, o.parser_version, o.status AS parse_status,
                       o.is_stale
                  FROM files f
                  LEFT JOIN storage_roots sr ON sr.id = f.storage_root_id
                  LEFT JOIN parse_observations o ON o.id = (
                      SELECT po.id FROM parse_observations po
                       WHERE po.file_id = f.id
                       ORDER BY po.is_stale ASC, po.observed_at DESC LIMIT 1
                  )
                 WHERE f.id = ?
                """,
                (file_id,),
            ).fetchone()
        if row is None:
            raise LibraryError("The selected file is no longer registered.")
        path = Path(row["locator"])
        availability = self._file_availability(path)
        if availability != "available":
            unavailable = availability == "unavailable"
            with (
                self.database.connection() as connection,
                transaction(connection, immediate=True),
            ):
                connection.execute(
                    """
                    UPDATE files SET is_present = ?,
                        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                     WHERE id = ?
                    """,
                    (None if unavailable else 0, file_id),
                )
                if row["root_location"]:
                    connection.execute(
                        """
                        UPDATE storage_roots SET availability = ?,
                            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                         WHERE location = ?
                        """,
                        (
                            "unavailable" if unavailable else "available",
                            row["root_location"],
                        ),
                    )
            return {"file_id": file_id, "path": str(path), "health": availability}

        try:
            before = fingerprint(path)
        except OSError:
            with (
                self.database.connection() as connection,
                transaction(connection, immediate=True),
            ):
                connection.execute(
                    "UPDATE parse_observations SET is_stale = 1 WHERE file_id = ?",
                    (file_id,),
                )
            raise
        expected_parser = self._parser_identity(path)
        observation_is_current = (
            (row["parser_id"], row["parser_version"]) == expected_parser
            and row["parse_status"] != ParseStatus.FAILED.value
            and not row["is_stale"]
        )
        if before.sha256 == row["content_hash"] and observation_is_current:
            with (
                self.database.connection() as connection,
                transaction(connection, immediate=True),
            ):
                connection.execute(
                    """
                    UPDATE files SET is_present = 1, size_bytes = ?, modified_ns = ?,
                        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                     WHERE id = ?
                    """,
                    (before.size, before.modified_ns, file_id),
                )
            stored_health = self._health(
                True, row["parse_status"], bool(row["is_stale"])
            )
            return {"file_id": file_id, "path": str(path), "health": stored_health}

        with (
            self.database.connection() as connection,
            transaction(connection, immediate=True),
        ):
            connection.execute(
                "UPDATE parse_observations SET is_stale = 1 WHERE file_id = ?",
                (file_id,),
            )
        result = self._parse(path)
        after = fingerprint(path)
        if before != after:
            raise LibraryError(f"File changed while it was being refreshed: {path}")
        with (
            self.database.connection() as connection,
            transaction(connection, immediate=True),
        ):
            connection.execute(
                """
                UPDATE files SET is_present = 1, size_bytes = ?, modified_ns = ?,
                    content_hash = ?, detected_format = ?, detected_version = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                 WHERE id = ?
                """,
                (
                    before.size,
                    before.modified_ns,
                    before.sha256,
                    result.detection.format_name or path.suffix.casefold().lstrip("."),
                    result.detection.format_version,
                    file_id,
                ),
            )
            self._store_observation(connection, file_id, before, result)
        return {
            "file_id": file_id,
            "path": str(path),
            "health": self._health(True, result.status.value, False),
            "parse_status": result.status.value,
        }

    @staticmethod
    def _file_availability(path: Path) -> str:
        try:
            return "available" if stat.S_ISREG(path.stat().st_mode) else "missing"
        except FileNotFoundError:
            try:
                Path(path.anchor).stat()
            except OSError:
                return "unavailable"
            return "missing"
        except OSError:
            return "unavailable"

    @staticmethod
    def _parser_identity(path: Path) -> tuple[str, str]:
        if path.suffix.casefold() == ".svp":
            from .parsers import SvpParser

            return SvpParser.parser_id, SvpParser.parser_version
        return "builtin.opaque", "1"

    @_serialized_write
    def relink_file(
        self, file_id: str, new_path: str | Path, *, require_hash_match: bool = True
    ) -> dict[str, Any]:
        replacement = Path(new_path).expanduser().resolve(strict=True)
        if not replacement.is_file():
            raise LibraryError("Relink target must be a readable file.")
        replacement_fingerprint = fingerprint(replacement)
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT f.content_hash, o.status AS parse_status, o.is_stale
                  FROM files f
                  LEFT JOIN parse_observations o ON o.id = (
                      SELECT po.id FROM parse_observations po
                       WHERE po.file_id = f.id
                       ORDER BY po.is_stale ASC, po.observed_at DESC LIMIT 1
                  )
                 WHERE f.id = ?
                """,
                (file_id,),
            ).fetchone()
            conflict = connection.execute(
                "SELECT id FROM files WHERE locator = ? AND id != ?",
                (str(replacement), file_id),
            ).fetchone()
        if row is None:
            raise LibraryError("The selected file is no longer registered.")
        if conflict is not None:
            raise LibraryError("That physical location is already registered.")
        if require_hash_match and row["content_hash"] != replacement_fingerprint.sha256:
            raise LibraryError(
                "Relink target content does not match the registered file."
            )
        content_changed = row["content_hash"] != replacement_fingerprint.sha256
        result = self._parse(replacement) if content_changed else None
        if fingerprint(replacement) != replacement_fingerprint:
            raise LibraryError(
                f"File changed while it was being relinked: {replacement}"
            )
        with (
            self.database.connection() as connection,
            transaction(connection, immediate=True),
        ):
            root_row = connection.execute(
                "SELECT id FROM storage_roots WHERE location = ?",
                (str(replacement.parent),),
            ).fetchone()
            root_id = root_row["id"] if root_row else new_id()
            if root_row is None:
                connection.execute(
                    """
                    INSERT INTO storage_roots(id, location, kind, availability)
                    VALUES (?, ?, 'indexed', 'available')
                    """,
                    (root_id, str(replacement.parent)),
                )
            if content_changed:
                connection.execute(
                    "UPDATE parse_observations SET is_stale = 1 WHERE file_id = ?",
                    (file_id,),
                )
            connection.execute(
                """
                UPDATE files SET locator = ?, storage_root_id = ?, is_present = 1,
                    size_bytes = ?, modified_ns = ?, content_hash = ?,
                    detected_format = COALESCE(?, detected_format),
                    detected_version = COALESCE(?, detected_version),
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                 WHERE id = ?
                """,
                (
                    str(replacement),
                    root_id,
                    replacement_fingerprint.size,
                    replacement_fingerprint.modified_ns,
                    replacement_fingerprint.sha256,
                    result.detection.format_name if result else None,
                    result.detection.format_version if result else None,
                    file_id,
                ),
            )
            if result is not None:
                self._store_observation(
                    connection, file_id, replacement_fingerprint, result
                )
        parse_status = result.status.value if result else row["parse_status"]
        return {
            "file_id": file_id,
            "path": str(replacement),
            "health": self._health(
                True, parse_status, bool(row["is_stale"]) if result is None else False
            ),
            "parse_status": parse_status,
        }

    def list_projects(
        self,
        query: str = "",
        engine: str | None = None,
        language: str | None = None,
        health: str | None = None,
        voice: str | None = None,
        tuning: str | None = None,
        version_scope: str = "all",
    ) -> list[dict[str, Any]]:
        if version_scope not in {"all", "preferred"}:
            raise LibraryError("Version scope must be 'all' or 'preferred'.")
        with (
            self.database.connection(readonly=True) as connection,
            transaction(connection),
        ):
            rows = connection.execute(
                """
                SELECT p.id AS project_id, p.name AS project_name, p.description,
                       p.song_id, p.preferred_version_id,
                       ws.id AS status_id, ws.name AS status_name,
                       v.id AS version_id, v.label AS version_label,
                       f.id AS file_id, f.locator, f.is_present, f.detected_format,
                       f.detected_version, f.content_hash,
                       o.status AS parse_status, o.is_stale, o.initial_bpm,
                       o.minimum_bpm, o.maximum_bpm, o.tempo_change_count,
                       o.lyric_note_count, o.populated_lyric_count, o.warnings_json,
                       o.id AS observation_id
                  FROM projects p
                  LEFT JOIN workflow_statuses ws ON ws.id = p.workflow_status_id
                  JOIN versions v ON v.project_id = p.id
                  JOIN files f ON f.version_id = v.id
                  LEFT JOIN parse_observations o ON o.id = (
                      SELECT po.id FROM parse_observations po
                       WHERE po.file_id = f.id
                       ORDER BY po.is_stale ASC, po.observed_at DESC LIMIT 1
                  )
                 ORDER BY p.name COLLATE NOCASE, v.sort_order, f.locator
                """
            ).fetchall()
            extras = self._load_search_metadata(connection)

            track_rows = connection.execute(
                """
                SELECT ft.observation_id, ft.engine, ft.voice_name,
                       ft.normalized_voice_name, ft.declared_language,
                       ft.pitch_state, ft.vibrato_state, ft.dynamics_state,
                       ft.details_json
                  FROM file_tracks ft
                """
            ).fetchall()

        tracks: dict[str, list[sqlite3.Row]] = {}
        for track in track_rows:
            tracks.setdefault(track["observation_id"], []).append(track)

        normalized_query = normalize_search_text(query)
        normalized_voice = normalize_search_text(voice or "")
        projects: dict[str, dict[str, Any]] = {}
        for row in rows:
            if (
                version_scope == "preferred"
                and row["preferred_version_id"] != row["version_id"]
            ):
                continue
            project_id = row["project_id"]
            metadata = extras.get(project_id, {})
            file_tracks = tracks.get(row["observation_id"], [])
            present = bool(row["is_present"])
            file_health = self._health(
                present,
                row["parse_status"],
                bool(row["is_stale"]),
                unavailable=row["is_present"] is None,
            )
            language_values: set[str] = set()
            voice_values: set[str] = set()
            for track in file_tracks:
                if track["declared_language"]:
                    language_values.add(track["declared_language"])
                if track["voice_name"]:
                    voice_values.add(track["voice_name"])
                try:
                    track_details = json.loads(track["details_json"] or "{}")
                except (TypeError, json.JSONDecodeError):
                    track_details = {}
                language_values.update(track_details.get("languages") or [])
                voice_values.update(
                    item["name"]
                    for item in track_details.get("voices") or []
                    if isinstance(item, dict) and item.get("name")
                )
            languages = sorted(language_values)
            voices = sorted(voice_values)
            engines = {row["detected_format"] or ""} | {
                track["engine"] for track in file_tracks if track["engine"]
            }
            if engine and engine.casefold() not in {
                value.casefold() for value in engines
            }:
                continue
            if language and not any(
                self._language_matches(language, item) for item in languages
            ):
                continue
            if health and health != file_health:
                continue
            if normalized_voice and not any(
                normalized_voice in normalize_search_text(item) for item in voices
            ):
                continue
            signal_states = {
                track[field]
                for track in file_tracks
                for field in ("pitch_state", "vibrato_state", "dynamics_state")
            }
            if tuning == "detected" and "detected" not in signal_states:
                continue
            if tuning == "none_detected" and (
                "detected" in signal_states or "none_detected" not in signal_states
            ):
                continue
            if tuning == "unknown" and signal_states not in (set(), {"unknown"}):
                continue

            match_candidates = [
                (
                    0,
                    "project name",
                    row["project_name"],
                    normalize_search_text(row["project_name"]),
                ),
                *(
                    (1, "song alias", text, normalized)
                    for text, normalized in zip(
                        metadata.get("aliases", []),
                        metadata.get("normalized_aliases", []),
                        strict=False,
                    )
                ),
                *(
                    (2, "credit", text, normalized)
                    for text, normalized in zip(
                        metadata.get("credits", []),
                        metadata.get("normalized_credits", []),
                        strict=False,
                    )
                ),
                *(
                    (2, "contributor alias", normalized, normalized)
                    for normalized in metadata.get("normalized_credits", [])[
                        len(metadata.get("credits", [])) :
                    ]
                ),
                (
                    3,
                    "workflow status",
                    row["status_name"] or "",
                    normalize_search_text(row["status_name"] or ""),
                ),
                *(
                    (4, "tag", text, normalized)
                    for text, normalized in zip(
                        metadata.get("tags", []),
                        metadata.get("normalized_tags", []),
                        strict=False,
                    )
                ),
                *((5, "voice", item, normalize_search_text(item)) for item in voices),
                (
                    6,
                    "filename",
                    Path(row["locator"]).name,
                    normalize_search_text(Path(row["locator"]).name),
                ),
            ]
            matches = [
                candidate
                for candidate in match_candidates
                if normalized_query and normalized_query in candidate[3]
            ]
            if normalized_query and not matches:
                continue

            def match_rank(item):
                # Exact names precede substrings; filenames remain lower priority.
                return (
                    0 if item[3] == normalized_query and item[0] < 2 else 1,
                    item[0],
                )

            best_match = min(matches, default=None, key=match_rank)
            project = projects.setdefault(
                project_id,
                {
                    "id": project_id,
                    "project_id": project_id,
                    "name": row["project_name"],
                    "description": row["description"] or "",
                    "status_id": row["status_id"],
                    "status_name": row["status_name"],
                    "aliases": metadata.get("aliases", []),
                    "editable_aliases": metadata.get("editable_aliases", []),
                    "credits": metadata.get("credits", []),
                    "credit_records": metadata.get("credit_records", []),
                    "editable_credit_records": metadata.get(
                        "editable_credit_records", []
                    ),
                    "tags": metadata.get("tags", []),
                    "files": [],
                    "match_rank": match_rank(best_match) if best_match else None,
                    "match_field": best_match[1] if best_match else None,
                    "match_value": best_match[2] if best_match else None,
                },
            )
            if best_match is not None and (
                project["match_rank"] is None
                or match_rank(best_match) < project["match_rank"]
            ):
                project["match_rank"] = match_rank(best_match)
                project["match_field"] = best_match[1]
                project["match_value"] = best_match[2]
            project["files"].append(
                {
                    "id": row["file_id"],
                    "file_id": row["file_id"],
                    "version_id": row["version_id"],
                    "version_label": row["version_label"],
                    "path": row["locator"],
                    "locator": row["locator"],
                    "format_name": row["detected_format"],
                    "format_version": row["detected_version"],
                    "parse_status": row["parse_status"] or "not_parsed",
                    "health": file_health,
                    "initial_bpm": row["initial_bpm"],
                    "tempo": row["initial_bpm"],
                    "voices": voices,
                    "voice": voices,
                    "languages": languages,
                    "language": languages,
                    "warnings": json.loads(row["warnings_json"] or "[]"),
                    "details": {
                        "Tracks": len(file_tracks),
                        "Tempo changes": row["tempo_change_count"],
                        "Minimum BPM": row["minimum_bpm"],
                        "Maximum BPM": row["maximum_bpm"],
                        "Vocal notes": row["lyric_note_count"],
                        "Populated lyrics": row["populated_lyric_count"],
                        "Tuning signals": ", ".join(sorted(signal_states)) or "unknown",
                        "Dependencies": "not checked",
                    },
                }
            )

        output = list(projects.values())
        output.sort(
            key=lambda item: (
                item["match_rank"] or (0, 0),
                normalize_search_text(item["name"]),
            )
        )
        for project in output:
            project.pop("match_rank", None)
            files = project["files"]
            project["file_count"] = len(files)
            project["engine"] = sorted(
                {item["format_name"] for item in files if item["format_name"]}
            )
            project["language"] = sorted(
                {language for item in files for language in item["languages"]}
            )
            health_values = {item["health"] for item in files}
            project["health"] = (
                next(iter(health_values)) if len(health_values) == 1 else "mixed"
            )
        return output

    @staticmethod
    def _load_search_metadata(
        connection: sqlite3.Connection,
    ) -> dict[str, dict[str, Any]]:
        def empty_metadata() -> dict[str, Any]:
            return {
                "aliases": [],
                "editable_aliases": [],
                "normalized_aliases": [],
                "credits": [],
                "credit_records": [],
                "editable_credit_records": [],
                "normalized_credits": [],
                "tags": [],
                "normalized_tags": [],
            }

        result = {
            row["id"]: empty_metadata()
            for row in connection.execute("SELECT id FROM projects")
        }
        aliases = connection.execute(
            """
            SELECT p.id AS project_id, sn.text, sn.normalized_text, sn.source_type
              FROM projects p JOIN song_names sn ON sn.song_id = p.song_id
             WHERE sn.is_dismissed = 0
             ORDER BY p.id, sn.normalized_text, sn.id
            """
        ).fetchall()
        for item in aliases:
            data = result[item["project_id"]]
            data["aliases"].append(item["text"])
            data["normalized_aliases"].append(item["normalized_text"])
            if item["source_type"] == "user":
                data["editable_aliases"].append(item["text"])

        credits = connection.execute(
            """
            SELECT pc.project_id, c.display_name, c.normalized_name,
                   pc.role, pc.source_type
              FROM project_credits pc JOIN contributors c ON c.id = pc.contributor_id
             ORDER BY pc.project_id, c.normalized_name, pc.role, pc.id
            """
        ).fetchall()
        for item in credits:
            data = result[item["project_id"]]
            data["credits"].append(f"{item['display_name']} ({item['role']})")
            record = {"name": item["display_name"], "role": item["role"]}
            data["credit_records"].append(record)
            data["normalized_credits"].append(item["normalized_name"])
            if item["source_type"] == "user":
                data["editable_credit_records"].append(record)

        contributor_aliases = connection.execute(
            """
            SELECT pc.project_id, cn.normalized_text
              FROM project_credits pc
              JOIN contributor_names cn ON cn.contributor_id = pc.contributor_id
             WHERE cn.is_dismissed = 0
            """
        ).fetchall()
        for item in contributor_aliases:
            result[item["project_id"]]["normalized_credits"].append(
                item["normalized_text"]
            )

        tags = connection.execute(
            """
            SELECT pt.project_id, t.name, t.normalized_name
              FROM project_tags pt JOIN tags t ON t.id = pt.tag_id
             ORDER BY pt.project_id, t.normalized_name, t.id
            """
        ).fetchall()
        for item in tags:
            data = result[item["project_id"]]
            data["tags"].append(item["name"])
            data["normalized_tags"].append(item["normalized_name"])
        return result

    @staticmethod
    def _language_matches(requested: str, actual: str) -> bool:
        groups = {
            "chinese": {"chinese", "mandarin", "cantonese", "zh", "cmn", "yue"},
            "japanese": {"japanese", "ja", "jpn"},
            "english": {"english", "en", "eng"},
            "korean": {"korean", "ko", "kor"},
        }
        requested_value = normalize_search_text(requested)
        actual_value = normalize_search_text(actual)
        return actual_value in groups.get(requested_value, {requested_value})

    @staticmethod
    def _health(
        present: bool, status: str | None, stale: bool, *, unavailable: bool = False
    ) -> str:
        if unavailable:
            return "unavailable"
        if not present:
            return "missing"
        if stale:
            return "stale"
        if status == ParseStatus.FAILED.value:
            return "failed"
        if status == ParseStatus.UNSUPPORTED.value:
            return "unsupported"
        return "available"

    @_serialized_write
    def update_project(
        self,
        project_id: str,
        *,
        name: str,
        description: str = "",
        status_id: str | None = None,
    ) -> None:
        normalized_name = normalize_search_text(name)
        if not normalized_name:
            raise LibraryError("Project name cannot be empty.")
        with (
            self.database.connection() as connection,
            transaction(connection, immediate=True),
        ):
            status_value = self._resolve_status(connection, status_id)
            cursor = connection.execute(
                """
                UPDATE projects SET name = ?, normalized_name = ?, description = ?,
                    workflow_status_id = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                 WHERE id = ?
                """,
                (name, normalized_name, description, status_value, project_id),
            )
            if cursor.rowcount != 1:
                raise LibraryError("The selected project is no longer registered.")

    @staticmethod
    def _resolve_status(
        connection: sqlite3.Connection, value: str | None
    ) -> str | None:
        if not value:
            return None
        existing = connection.execute(
            "SELECT id FROM workflow_statuses WHERE id = ?", (value,)
        ).fetchone()
        if existing:
            return existing["id"]
        normalized = normalize_search_text(value)
        existing = connection.execute(
            "SELECT id FROM workflow_statuses WHERE normalized_name = ?", (normalized,)
        ).fetchone()
        if existing:
            return existing["id"]
        status_id = new_id()
        order = connection.execute(
            "SELECT COALESCE(MAX(display_order), -1) + 1 FROM workflow_statuses"
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO workflow_statuses(id, name, normalized_name, display_order) "
            "VALUES (?, ?, ?, ?)",
            (status_id, value, normalized, order),
        )
        return status_id

    @_serialized_write
    def add_alias(
        self,
        project_id: str,
        text: str,
        *,
        language: str | None = None,
        script: str | None = None,
        kind: str = "alias",
        display: bool = False,
    ) -> str:
        normalized = normalize_search_text(text)
        if not normalized:
            raise LibraryError("Alias cannot be empty.")
        with (
            self.database.connection() as connection,
            transaction(connection, immediate=True),
        ):
            project = connection.execute(
                "SELECT song_id FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
            if project is None:
                raise LibraryError("The selected project is no longer registered.")
            song_id = project["song_id"] or new_id()
            has_display = False
            if project["song_id"] is None:
                connection.execute("INSERT INTO songs(id) VALUES (?)", (song_id,))
                connection.execute(
                    "UPDATE projects SET song_id = ? WHERE id = ?",
                    (song_id, project_id),
                )
            else:
                has_display = (
                    connection.execute(
                        "SELECT 1 FROM song_names WHERE song_id = ? AND is_display = 1",
                        (song_id,),
                    ).fetchone()
                    is not None
                )
            use_display = display or not has_display
            if display and has_display:
                connection.execute(
                    "UPDATE song_names SET is_display = 0 WHERE song_id = ?",
                    (song_id,),
                )
            alias_id = new_id()
            connection.execute(
                """
                INSERT INTO song_names(
                    id, song_id, text, normalized_text, language, script, kind,
                    source_type, is_display
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'user', ?)
                """,
                (
                    alias_id,
                    song_id,
                    text,
                    normalized,
                    language,
                    script,
                    "primary" if use_display and kind == "alias" else kind,
                    int(use_display),
                ),
            )
        return alias_id

    @_serialized_write
    def add_credit(self, project_id: str, contributor: str, role: str) -> str:
        normalized = normalize_search_text(contributor)
        if not normalized or not role.strip():
            raise LibraryError("Contributor and role are required.")
        with (
            self.database.connection() as connection,
            transaction(connection, immediate=True),
        ):
            contributor_id = new_id()
            connection.execute(
                "INSERT INTO contributors(id, display_name, normalized_name) VALUES (?, ?, ?)",
                (contributor_id, contributor, normalized),
            )
            credit_id = new_id()
            connection.execute(
                """
                INSERT INTO project_credits(id, project_id, contributor_id, role, source_type)
                VALUES (?, ?, ?, ?, 'user')
                """,
                (credit_id, project_id, contributor_id, role.strip()),
            )
        return credit_id

    @_serialized_write
    def set_aliases(self, project_id: str, aliases: Iterable[str]) -> None:
        """Replace user-entered song aliases while retaining sourced metadata."""

        values = self._alias_values(aliases)
        with (
            self.database.connection() as connection,
            transaction(connection, immediate=True),
        ):
            self._set_aliases(connection, project_id, values)

    @_serialized_write
    def set_credits(self, project_id: str, credits: Iterable[tuple[str, str]]) -> None:
        """Replace user-entered project credits without merging people by name."""

        values = self._credit_values(credits)
        with (
            self.database.connection() as connection,
            transaction(connection, immediate=True),
        ):
            self._set_credits(connection, project_id, values)

    @_serialized_write
    def set_tags(self, project_id: str, tags: Iterable[str]) -> None:
        names = {normalize_search_text(tag): tag.strip() for tag in tags if tag.strip()}
        with (
            self.database.connection() as connection,
            transaction(connection, immediate=True),
        ):
            self._set_tags(connection, project_id, names)

    @_serialized_write
    def save_project_metadata(
        self,
        project_id: str,
        *,
        name: str,
        description: str,
        status_id: str | None,
        aliases: Iterable[str],
        credits: Iterable[tuple[str, str]],
        tags: Iterable[str],
    ) -> None:
        """Atomically save every editable field in the project inspector."""

        normalized_name = normalize_search_text(name)
        if not normalized_name:
            raise LibraryError("Project name cannot be empty.")
        alias_values = self._alias_values(aliases)
        credit_values = self._credit_values(credits)
        tag_values = {
            normalize_search_text(tag): tag.strip() for tag in tags if tag.strip()
        }
        with (
            self.database.connection() as connection,
            transaction(connection, immediate=True),
        ):
            resolved_status = self._resolve_status(connection, status_id)
            cursor = connection.execute(
                """
                UPDATE projects SET name = ?, normalized_name = ?, description = ?,
                    workflow_status_id = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                 WHERE id = ?
                """,
                (name, normalized_name, description, resolved_status, project_id),
            )
            if cursor.rowcount != 1:
                raise LibraryError("The selected project is no longer registered.")
            self._set_aliases(connection, project_id, alias_values)
            self._set_credits(connection, project_id, credit_values)
            self._set_tags(connection, project_id, tag_values)

    @staticmethod
    def _alias_values(aliases: Iterable[str]) -> list[tuple[str, str]]:
        values: list[tuple[str, str]] = []
        seen: set[str] = set()
        for alias in aliases:
            text = alias.strip()
            normalized = normalize_search_text(text)
            if text and text not in seen:
                seen.add(text)
                values.append((normalized, text))
        return values

    @staticmethod
    def _credit_values(
        credits: Iterable[tuple[str, str]],
    ) -> list[tuple[str, str, str]]:
        return [
            (role.strip(), name.strip(), normalize_search_text(name))
            for role, name in credits
            if role.strip() and name.strip()
        ]

    @staticmethod
    def _set_aliases(
        connection: sqlite3.Connection,
        project_id: str,
        values: list[tuple[str, str]],
    ) -> None:
        project = connection.execute(
            "SELECT song_id FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        if project is None:
            raise LibraryError("The selected project is no longer registered.")
        song_id = project["song_id"]
        if song_id is None and values:
            song_id = new_id()
            connection.execute("INSERT INTO songs(id) VALUES (?)", (song_id,))
            connection.execute(
                "UPDATE projects SET song_id = ? WHERE id = ?", (song_id, project_id)
            )
        if song_id is None:
            return

        rows = connection.execute(
            """
            SELECT id, text, normalized_text FROM song_names
             WHERE song_id = ? AND source_type = 'user'
             ORDER BY created_at, id
            """,
            (song_id,),
        ).fetchall()
        existing = {row["text"]: row for row in rows}
        desired = {text for _, text in values}
        for row in rows:
            if row["text"] not in desired:
                connection.execute("DELETE FROM song_names WHERE id = ?", (row["id"],))

        desired_ids: list[str] = []
        for normalized, text in values:
            row = existing.get(text)
            if row is not None:
                desired_ids.append(row["id"])
                if row["text"] != text:
                    connection.execute(
                        "UPDATE song_names SET text = ? WHERE id = ?", (text, row["id"])
                    )
                continue
            alias_id = new_id()
            desired_ids.append(alias_id)
            connection.execute(
                """
                INSERT INTO song_names(
                    id, song_id, text, normalized_text, kind, source_type
                ) VALUES (?, ?, ?, ?, 'alias', 'user')
                """,
                (alias_id, song_id, text, normalized),
            )
        has_display = connection.execute(
            "SELECT 1 FROM song_names WHERE song_id = ? AND is_display = 1",
            (song_id,),
        ).fetchone()
        if has_display is None and desired_ids:
            connection.execute(
                "UPDATE song_names SET is_display = 1, kind = 'primary' WHERE id = ?",
                (desired_ids[0],),
            )

    @staticmethod
    def _set_credits(
        connection: sqlite3.Connection,
        project_id: str,
        values: list[tuple[str, str, str]],
    ) -> None:
        if (
            connection.execute(
                "SELECT 1 FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
            is None
        ):
            raise LibraryError("The selected project is no longer registered.")
        existing_rows = connection.execute(
            """
            SELECT pc.id, pc.role, pc.contributor_id, c.display_name
              FROM project_credits pc
              JOIN contributors c ON c.id = pc.contributor_id
             WHERE pc.project_id = ? AND pc.source_type = 'user'
             ORDER BY pc.id
            """,
            (project_id,),
        ).fetchall()
        existing: dict[tuple[str, str], list[sqlite3.Row]] = {}
        for row in existing_rows:
            existing.setdefault((row["role"], row["display_name"]), []).append(row)
        for role, name, normalized in values:
            candidates = existing.get((role, name), [])
            if candidates:
                candidates.pop(0)
                continue
            contributor_id = new_id()
            connection.execute(
                """
                INSERT INTO contributors(id, display_name, normalized_name)
                VALUES (?, ?, ?)
                """,
                (contributor_id, name, normalized),
            )
            connection.execute(
                """
                INSERT INTO project_credits(
                    id, project_id, contributor_id, role, source_type
                ) VALUES (?, ?, ?, ?, 'user')
                """,
                (new_id(), project_id, contributor_id, role),
            )
        for candidates in existing.values():
            for row in candidates:
                connection.execute(
                    "DELETE FROM project_credits WHERE id = ?", (row["id"],)
                )

    @staticmethod
    def _set_tags(
        connection: sqlite3.Connection,
        project_id: str,
        names: dict[str, str],
    ) -> None:
        connection.execute(
            "DELETE FROM project_tags WHERE project_id = ?", (project_id,)
        )
        for normalized, name in names.items():
            row = connection.execute(
                "SELECT id FROM tags WHERE normalized_name = ?", (normalized,)
            ).fetchone()
            tag_id = row["id"] if row else new_id()
            if row is None:
                connection.execute(
                    "INSERT INTO tags(id, name, normalized_name) VALUES (?, ?, ?)",
                    (tag_id, name, normalized),
                )
            connection.execute(
                "INSERT INTO project_tags(project_id, tag_id) VALUES (?, ?)",
                (project_id, tag_id),
            )

    def open_file(self, file_id: str, application: str | Path | None = None) -> None:
        open_path(self._file_path(file_id), Path(application) if application else None)

    def reveal_file(self, file_id: str) -> None:
        reveal_path(self._file_path(file_id))

    @_serialized_write
    def remove_project(self, project_id: str) -> None:
        """Remove catalogue records without touching any indexed file bytes."""

        with (
            self.database.connection() as connection,
            transaction(connection, immediate=True),
        ):
            cursor = connection.execute(
                "DELETE FROM projects WHERE id = ?", (project_id,)
            )
            if cursor.rowcount != 1:
                raise LibraryError("The selected project is no longer registered.")

    def _file_path(self, file_id: str) -> Path:
        with self.database.connection(readonly=True) as connection:
            row = connection.execute(
                "SELECT locator FROM files WHERE id = ?", (file_id,)
            ).fetchone()
        if row is None:
            raise LibraryError("The selected file is no longer registered.")
        path = Path(row["locator"])
        if not path.is_file():
            raise LibraryError(f"File is missing or unavailable: {path}")
        return path

    def backup_to(self, destination: str | Path) -> Path:
        self._validate_output_path(destination)
        return self.database.backup_to(destination)

    def _validate_output_path(self, destination: str | Path) -> None:
        target = Path(destination).expanduser().resolve()
        protected = [self.database.path]
        protected.extend(
            Path(f"{self.database.path}{suffix}")
            for suffix in ("-wal", "-shm", ".lock")
        )
        with self.database.connection(readonly=True) as connection:
            protected.extend(
                Path(row[0]) for row in connection.execute("SELECT locator FROM files")
            )
        for path in protected:
            if target == path or (
                target.exists() and path.exists() and target.samefile(path)
            ):
                raise LibraryError(
                    "Choose a destination outside the live database and registered files."
                )

    def export_metadata(self, destination: str | Path) -> Path:
        """Write a portable JSON metadata snapshot without any project bytes."""

        self._validate_output_path(destination)
        destination_path = Path(destination).expanduser().resolve()
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = destination_path.with_name(
            f".{destination_path.name}.{new_id()}.tmp"
        )
        with (
            self.database.connection(readonly=True) as connection,
            transaction(connection),
        ):
            payload = {
                "format": "vocavault-metadata",
                "format_version": 1,
                "schema_version": connection.execute("PRAGMA user_version").fetchone()[
                    0
                ],
                "includes_project_files": False,
                "tables": {
                    table: [
                        dict(row)
                        for row in connection.execute(f"SELECT * FROM {table}")
                    ]
                    for table in _EXPORT_TABLES
                },
            }
        try:
            with temporary_path.open("w", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            os.replace(temporary_path, destination_path)
        except OSError:
            temporary_path.unlink(missing_ok=True)
            raise
        return destination_path

    @_serialized_write
    def restore_from(self, source: str | Path) -> None:
        self.database.restore_from(source)
        self.database.initialize()


__all__ = ["PROJECT_EXTENSIONS", "LibraryError", "LibraryService"]
