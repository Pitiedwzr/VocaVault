from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from vocavault.database import new_id, transaction
from vocavault.library import LibraryError, LibraryService


def test_import_is_idempotent_searchable_and_preserves_source(tmp_path: Path) -> None:
    source = tmp_path / "ＧＨＯＳＴ-rule.ust"
    source.write_bytes(b"[#VERSION]\r\nUST Version1.2\r\n")
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    library = LibraryService(tmp_path / "library.sqlite3")

    first = library.import_paths([source])
    second = library.import_paths([source])

    assert first[0]["created"] is True
    assert second[0]["created"] is False
    assert len(library.list_projects(query="ghost rule")) == 1
    assert library.list_projects()[0]["files"][0]["health"] == "unsupported"
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash


def test_manual_alias_tags_credits_status_and_backup_restore(tmp_path: Path) -> None:
    source = tmp_path / "project.vsqx"
    source.write_text("<vsq4/>", encoding="utf-8")
    library = LibraryService(tmp_path / "library.sqlite3")
    imported = library.import_paths([source])[0]
    project_id = imported["project_id"]

    library.add_alias(project_id, "幽霊船", language="Japanese")
    library.add_credit(project_id, "Producer", "tuner")
    library.set_tags(project_id, ["Cover", "WIP"])
    library.update_project(
        project_id, name="New name", description="Notes", status_id="Tuning"
    )
    backup = library.backup_to(tmp_path / "backup.sqlite3")
    export = library.export_metadata(tmp_path / "metadata.json")

    library.update_project(project_id, name="Changed")
    library.restore_from(backup)
    restored = library.list_projects(query="幽霊")

    assert restored[0]["name"] == "New name"
    assert restored[0]["status_name"] == "Tuning"
    assert restored[0]["tags"] == ["Cover", "WIP"]
    assert "Producer (tuner)" in restored[0]["credits"]
    exported = json.loads(export.read_text(encoding="utf-8"))
    assert exported["includes_project_files"] is False
    assert exported["tables"]["projects"][0]["name"] == "New name"

    library.set_aliases(project_id, ["Ghost Ship"])
    library.set_credits(project_id, [("mixer", "Another Person")])
    replaced = library.list_projects(query="ghost ship")[0]
    assert replaced["aliases"] == ["Ghost Ship"]
    assert replaced["credits"] == ["Another Person (mixer)"]
    assert library.list_projects(query="幽霊") == []


def test_refresh_marks_missing_without_removing_catalogue(tmp_path: Path) -> None:
    source = tmp_path / "missing.ccs"
    source.write_bytes(b"opaque")
    library = LibraryService(tmp_path / "library.sqlite3")
    file_id = library.import_paths([source])[0]["file_id"]
    source.unlink()

    assert library.refresh_file(file_id)["health"] == "missing"
    assert library.list_projects()[0]["files"][0]["health"] == "missing"


def test_relink_requires_matching_content_by_default(tmp_path: Path) -> None:
    source = tmp_path / "old.vpr"
    source.write_bytes(b"same")
    replacement = tmp_path / "new.vpr"
    replacement.write_bytes(b"same")
    wrong = tmp_path / "wrong.vpr"
    wrong.write_bytes(b"different")
    library = LibraryService(tmp_path / "library.sqlite3")
    file_id = library.import_paths([source])[0]["file_id"]

    try:
        library.relink_file(file_id, wrong)
    except LibraryError as error:
        assert "does not match" in str(error)
    else:
        raise AssertionError("different content should require an explicit override")

    source.unlink()
    result = library.relink_file(file_id, replacement)
    assert result["health"] == "unsupported"


def test_remove_project_never_deletes_indexed_file(tmp_path: Path) -> None:
    source = tmp_path / "keep.svp"
    source.write_text(
        '{"version":153,"time":{"tempo":[]},"tracks":[]}', encoding="utf-8"
    )
    library = LibraryService(tmp_path / "library.sqlite3")
    project_id = library.import_paths([source])[0]["project_id"]

    library.remove_project(project_id)

    assert source.is_file()
    assert library.list_projects() == []


def test_refresh_reparses_when_parser_version_changes(tmp_path: Path) -> None:
    source = tmp_path / "versioned.svp"
    source.write_text(
        json.dumps(
            {
                "version": 153,
                "time": {"tempo": [{"position": 0, "bpm": 120}]},
                "tracks": [],
            }
        ),
        encoding="utf-8",
    )
    library = LibraryService(tmp_path / "library.sqlite3")
    file_id = library.import_paths([source])[0]["file_id"]
    with library.database.connection() as connection:
        connection.execute(
            "UPDATE parse_observations SET parser_version = 'legacy' WHERE file_id = ?",
            (file_id,),
        )

    result = library.refresh_file(file_id)

    assert result["parse_status"] == "parsed"
    with library.database.connection(readonly=True) as connection:
        observations = connection.execute(
            "SELECT parser_version, is_stale FROM parse_observations WHERE file_id = ?",
            (file_id,),
        ).fetchall()
    assert {(row["parser_version"], row["is_stale"]) for row in observations} == {
        ("legacy", 1),
        ("0.1.0", 0),
    }


def test_engine_voice_filters_must_match_the_same_file(tmp_path: Path) -> None:
    svp = tmp_path / "lead.svp"
    svp.write_text(
        json.dumps(
            {
                "version": 153,
                "time": {"tempo": [{"position": 0, "bpm": 120}]},
                "tracks": [
                    {
                        "name": "Lead",
                        "mainGroup": {"notes": [], "parameters": {}},
                        "mainRef": {
                            "isInstrumental": False,
                            "database": {"name": "Miku", "language": "Japanese"},
                        },
                        "groups": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    ust = tmp_path / "harmony.ust"
    ust.write_text("[#VERSION]\nUST Version1.2\n", encoding="utf-8")
    library = LibraryService(tmp_path / "library.sqlite3")
    svp_import = library.import_paths([svp])[0]
    ust_import = library.import_paths([ust])[0]

    with (
        library.database.connection() as connection,
        transaction(connection, immediate=True),
    ):
        connection.execute(
            "UPDATE projects SET preferred_version_id = NULL WHERE id = ?",
            (ust_import["project_id"],),
        )
        connection.execute(
            "UPDATE versions SET project_id = ?, sort_order = 1 WHERE id = ?",
            (svp_import["project_id"], ust_import["version_id"]),
        )
        connection.execute(
            "DELETE FROM projects WHERE id = ?", (ust_import["project_id"],)
        )
        observation = connection.execute(
            "SELECT id FROM parse_observations WHERE file_id = ?",
            (ust_import["file_id"],),
        ).fetchone()
        connection.execute(
            """
            INSERT INTO file_tracks(
                id, observation_id, track_index, track_kind, engine,
                voice_name, normalized_voice_name
            ) VALUES (?, ?, 0, 'vocal', 'ust', 'Teto', 'teto')
            """,
            (new_id(), observation["id"]),
        )

    assert library.list_projects(engine="svp", voice="Teto") == []
    assert len(library.list_projects(engine="ust", voice="Teto")) == 1
    assert library.list_projects(voice="Teto", version_scope="preferred") == []


def test_export_and_backup_cannot_overwrite_catalogued_bytes(tmp_path: Path) -> None:
    source = tmp_path / "project.ust"
    source.write_bytes(b"source bytes")
    library = LibraryService(tmp_path / "library.sqlite3")
    library.import_paths([source])
    for operation in (library.export_metadata, library.backup_to):
        for target in (source, library.database.path):
            with pytest.raises(LibraryError, match="destination"):
                operation(target)
    assert source.read_bytes() == b"source bytes"
    assert len(library.list_projects()) == 1


def test_inspector_save_preserves_identity_and_rolls_back(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "project.ust"
    source.write_bytes(b"opaque")
    library = LibraryService(tmp_path / "library.sqlite3")
    project_id = library.import_paths([source])[0]["project_id"]
    alias_id = library.add_alias(project_id, "幽霊", language="ja", script="Jpan")
    library.add_alias(project_id, "GHOST")
    library.add_alias(project_id, "ghost")
    library.add_credit(project_id, "Same Name", "tuner")
    library.add_credit(project_id, "Same Name", "tuner")
    with library.database.connection(readonly=True) as connection:
        original_credits = [
            tuple(row) for row in connection.execute("SELECT * FROM project_credits")
        ]
        original_alias = dict(
            connection.execute(
                "SELECT * FROM song_names WHERE id = ?", (alias_id,)
            ).fetchone()
        )
    arguments = {
        "name": "Edited",
        "description": "Notes",
        "status_id": "Draft",
        "aliases": ["幽霊", "GHOST", "ghost"],
        "credits": [("tuner", "Same Name"), ("tuner", "Same Name")],
        "tags": ["test"],
    }
    library.save_project_metadata(project_id, **arguments)
    with library.database.connection(readonly=True) as connection:
        assert [
            tuple(row) for row in connection.execute("SELECT * FROM project_credits")
        ] == original_credits
        assert (
            dict(
                connection.execute(
                    "SELECT * FROM song_names WHERE id = ?", (alias_id,)
                ).fetchone()
            )
            == original_alias
        )
    assert len(library.list_projects()[0]["aliases"]) == 3

    def fail_tags(*args):
        raise LibraryError("simulated save failure")

    monkeypatch.setattr(library, "_set_tags", fail_tags)
    arguments.update(name="Must roll back", aliases=["replacement"])
    with pytest.raises(LibraryError, match="simulated"):
        library.save_project_metadata(project_id, **arguments)
    project = library.list_projects()[0]
    assert project["name"] == "Edited"
    assert "幽霊" in project["aliases"]


def test_import_reports_missing_input_and_can_cancel_between_files(
    tmp_path: Path,
) -> None:
    sources = [tmp_path / f"file{index}.ust" for index in range(3)]
    for source in sources:
        source.write_bytes(b"opaque")
    library = LibraryService(tmp_path / "library.sqlite3")
    progress = []
    outcomes = library.import_paths(
        [tmp_path / "absent.ust", *sources],
        cancelled=lambda: bool(progress),
        progress=lambda done, total, path: progress.append((done, total, path)),
    )
    assert "error" in outcomes[0]
    assert outcomes[1]["created"] is True
    assert progress[0][:2] == (1, 3)
    assert len(library.list_projects()) == 1


def test_unreadable_file_is_unavailable_and_recovers(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "restricted.ust"
    source.write_bytes(b"opaque")
    library = LibraryService(tmp_path / "library.sqlite3")
    file_id = library.import_paths([source])[0]["file_id"]
    original_stat = Path.stat

    def denied_stat(path, *args, **kwargs):
        if path == source:
            raise PermissionError("access denied")
        return original_stat(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "stat", denied_stat)
        assert library.refresh_file(file_id)["health"] == "unavailable"
        assert (
            library.list_projects(health="unavailable")[0]["files"][0]["id"] == file_id
        )
    assert library.refresh_file(file_id)["health"] == "unsupported"


def test_equal_bytes_stay_separate_and_search_explains_alias(tmp_path: Path) -> None:
    first, second = tmp_path / "first.ust", tmp_path / "second.ust"
    for source in (first, second):
        source.write_bytes(b"same bytes")
    library = LibraryService(tmp_path / "library.sqlite3")
    imports = library.import_paths([first, second])
    assert len({item["project_id"] for item in imports}) == 2
    project_id = imports[0]["project_id"]
    library.add_alias(project_id, "Ghost Rule")
    library.add_credit(project_id, "初音ミク", "singer")
    with library.database.connection() as connection:
        contributor_id = connection.execute(
            "SELECT contributor_id FROM project_credits"
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO contributor_names(id, contributor_id, text, normalized_text) VALUES (?, ?, ?, ?)",
            (new_id(), contributor_id, "Hatsune Miku", "hatsune miku"),
        )
    assert library.list_projects(query="ghost-rule")[0]["match_field"] == "song alias"
    assert library.list_projects(query="miku")[0]["match_field"] == "contributor alias"
    assert library.list_projects(query="%") == []
