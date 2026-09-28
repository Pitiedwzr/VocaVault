from __future__ import annotations

from pathlib import Path

import pytest

from vocavault.database import new_id, transaction
from vocavault.library import LibraryService


@pytest.fixture
def populated_library(tmp_path: Path) -> LibraryService:
    library = LibraryService(tmp_path / "search_test.sqlite3")

    proj1_file = tmp_path / "ghost_ship.svp"
    proj1_file.write_bytes(b'{"version": 113, "time": {}, "tracks": []}')
    library.import_paths([proj1_file])

    proj2_file = tmp_path / "ghost_rule.svp"
    proj2_file.write_bytes(b'{"version": 113, "time": {}, "tracks": []}')
    library.import_paths([proj2_file])

    projects = library.list_projects()
    proj1 = next(p for p in projects if "ghost_ship" in p["files"][0]["path"])
    proj2 = next(p for p in projects if "ghost_rule" in p["files"][0]["path"])

    library.update_project(proj1["id"], name="ゴーゴー幽霊船")
    library.set_aliases(proj1["id"], ["Go Go Ghost Ship", "幽霊船"])

    library.update_project(proj2["id"], name="ゴーストルール")
    library.set_aliases(proj2["id"], ["Ghost Rule"])

    # Create project 3 with contributor Hatsune Miku / 初音ミク
    proj3_file = tmp_path / "song3.svp"
    proj3_file.write_bytes(b'{"version": 113, "time": {}, "tracks": []}')
    library.import_paths([proj3_file])
    proj3 = next(p for p in library.list_projects() if "song3" in p["files"][0]["path"])
    library.set_credits(proj3["id"], [("vocal", "初音ミク")])
    with library.database.connection() as conn, transaction(conn):
        contributor_id = conn.execute(
            "SELECT contributor_id FROM project_credits WHERE project_id = ?",
            (proj3["id"],),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO contributor_names(id, contributor_id, text, normalized_text) VALUES (?, ?, ?, ?)",
            (new_id(), contributor_id, "Hatsune Miku", "hatsune miku"),
        )

    library.rebuild_search_index()
    return library


def test_search_ghost_finds_both_by_alias(populated_library: LibraryService) -> None:
    results = populated_library.list_projects(query="ghost")
    names = {r["name"] for r in results}
    assert "ゴーストルール" in names
    assert "ゴーゴー幽霊船" in names


def test_search_typo_fuzzy_retrieval(populated_library: LibraryService) -> None:
    results = populated_library.list_projects(query="gosht rul")
    assert len(results) >= 1
    assert results[0]["name"] == "ゴーストルール"
    assert results[0]["match_field"] == "song alias"


def test_search_short_cjk_substring_path(populated_library: LibraryService) -> None:
    results = populated_library.list_projects(query="幽霊")
    names = {r["name"] for r in results}
    assert "ゴーゴー幽霊船" in names


def test_search_normalized_unicode_and_punctuation(populated_library: LibraryService) -> None:
    results_fullwidth = populated_library.list_projects(query="ＧＨＯＳＴ")
    assert any(r["name"] == "ゴーストルール" for r in results_fullwidth)

    results_hyphen = populated_library.list_projects(query="ghost-rule")
    assert any(r["name"] == "ゴーストルール" for r in results_hyphen)
    assert results_hyphen[0]["name"] == "ゴーストルール"


def test_search_miku_contributor_alias_explanation(populated_library: LibraryService) -> None:
    results = populated_library.list_projects(query="miku")
    assert len(results) >= 1
    assert results[0]["match_field"] in ("contributor alias", "credit")


def test_search_filters_bound_to_same_file(tmp_path: Path) -> None:
    library = LibraryService(tmp_path / "filter_test.sqlite3")
    # Project with 2 files: an SVP file without Teto, and a UST file with Teto
    svp_file = tmp_path / "project.svp"
    svp_file.write_bytes(b'{"version": 113, "time": {}, "tracks": []}')
    imported = library.import_paths([svp_file])
    project_id = imported[0]["project_id"]

    ust_file = tmp_path / "harmony.ust"
    ust_file.write_text(
        "[#SETTING]\nTempo=120\nVoiceDir=%VOICE%Teto\n[#0000]\nLength=480\nLyric=a\n",
        encoding="utf-8",
    )
    imported_ust = library.import_paths([ust_file])
    ust_file_id = imported_ust[0]["file_id"]
    ust_proj_id = imported_ust[0]["project_id"]

    # Reassign ust_file to first project
    with library.database.connection() as conn, transaction(conn):
        v1_id = conn.execute("SELECT id FROM versions WHERE project_id = ?", (project_id,)).fetchone()[0]
        conn.execute("UPDATE versions SET default_file_id = NULL WHERE default_file_id = ?", (ust_file_id,))
        conn.execute("UPDATE files SET version_id = ? WHERE id = ?", (v1_id, ust_file_id))
        conn.execute("DELETE FROM projects WHERE id = ?", (ust_proj_id,))

    library.rebuild_search_index()

    # Query with Engine=SVP and Voice=Teto -> Neither file satisfies BOTH!
    results = library.list_projects(engine="svp", voice="Teto", version_scope="all")
    assert results == []


def test_search_alias_rebuild_lifecycle(populated_library: LibraryService) -> None:
    projects = populated_library.list_projects()
    proj = next(p for p in projects if p["name"] == "ゴーストルール")

    # Add new alias
    populated_library.set_aliases(proj["id"], ["Ghost Rule", "New Special Alias"])
    populated_library.rebuild_search_index()
    assert len(populated_library.list_projects(query="Special Alias")) == 1

    # Remove alias
    populated_library.set_aliases(proj["id"], ["Ghost Rule"])
    populated_library.rebuild_search_index()
    assert populated_library.list_projects(query="Special Alias") == []


def test_search_box_literal_punctuation(populated_library: LibraryService) -> None:
    assert populated_library.list_projects(query="%") == []
    assert populated_library.list_projects(query="*") == []
    assert populated_library.list_projects(query="?") == []
    assert populated_library.list_projects(query="\"") == []
    assert populated_library.list_projects(query="'''") == []
