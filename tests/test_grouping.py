from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from vocavault.database import new_id, transaction
from vocavault.library import LibraryError, LibraryService


def test_create_and_manage_versions(tmp_path: Path) -> None:
    library = LibraryService(tmp_path / "grouping.sqlite3")
    f1 = tmp_path / "lead.svp"
    f1.write_bytes(b'{"version": 113, "time": {}, "tracks": []}')
    imported = library.import_paths([f1])
    project_id = imported[0]["project_id"]
    file_id = imported[0]["file_id"]

    # Initial import created default version
    project = library.list_projects()[0]
    assert len(project["versions"]) == 1
    assert project["versions"][0]["is_preferred"] is True

    # Create new version
    v2 = library.create_version(project_id, "Harmony Revision", notes="Lead & harmony combined", terms="CC-BY")
    assert v2["label"] == "Harmony Revision"
    assert v2["distribution_terms"] == "CC-BY"

    # List versions
    versions = library.list_versions(project_id)
    assert len(versions) == 2
    assert versions[1]["id"] == v2["id"]
    assert versions[1]["distribution_terms"] == "CC-BY"

    # Update version
    updated_v2 = library.update_version(v2["id"], label="Updated Harmony", notes="Revised notes", distribution_terms="CC-BY-SA")
    assert updated_v2["label"] == "Updated Harmony"
    assert updated_v2["distribution_terms"] == "CC-BY-SA"

    # Set v2 as preferred version
    library.set_preferred_version(project_id, v2["id"])
    projs = library.list_projects()
    assert projs[0]["preferred_version_id"] == v2["id"]

    # Attempt to set preferred version to a non-existent or foreign version should fail
    with pytest.raises(LibraryError):
        library.set_preferred_version(project_id, "non-existent-id")


def test_move_file_to_version_with_default_file_safety(tmp_path: Path) -> None:
    library = LibraryService(tmp_path / "grouping.sqlite3")
    f1 = tmp_path / "v1.svp"
    f1.write_bytes(b'{"version": 113, "time": {}, "tracks": []}')
    imported = library.import_paths([f1])
    project_id = imported[0]["project_id"]
    file_id = imported[0]["file_id"]
    v1_id = imported[0]["version_id"]

    v2 = library.create_version(project_id, "v2")
    v2_id = v2["id"]

    # f1 is currently the default_file_id of v1. Move f1 to v2!
    library.move_file_to_version(file_id, v2_id)

    # Verify f1 is now in v2
    with library.database.connection() as conn:
        new_v = conn.execute("SELECT version_id FROM files WHERE id = ?", (file_id,)).fetchone()[0]
        assert new_v == v2_id
        # v2 should now have f1 as default file
        v2_default = conn.execute("SELECT default_file_id FROM versions WHERE id = ?", (v2_id,)).fetchone()[0]
        assert v2_default == file_id


def test_delete_version_with_preferred_reassignment(tmp_path: Path) -> None:
    library = LibraryService(tmp_path / "grouping.sqlite3")
    f1 = tmp_path / "v1.svp"
    f1.write_bytes(b'{"version": 113, "time": {}, "tracks": []}')
    imported = library.import_paths([f1])
    project_id = imported[0]["project_id"]
    v1_id = imported[0]["version_id"]

    # Trying to delete the only version fails
    with pytest.raises(LibraryError, match="only version"):
        library.delete_version(v1_id)

    v2 = library.create_version(project_id, "v2")
    v2_id = v2["id"]
    library.set_preferred_version(project_id, v2_id)

    # Deleting preferred version v2 reassigns preferred version to v1
    library.delete_version(v2_id)
    projs = library.list_projects()
    assert projs[0]["preferred_version_id"] == v1_id
    assert len(library.list_versions(project_id)) == 1


def test_move_version_to_another_project(tmp_path: Path) -> None:
    library = LibraryService(tmp_path / "grouping.sqlite3")
    f1 = tmp_path / "lead.svp"
    f1.write_bytes(b'{"version": 113, "time": {}, "tracks": []}')
    p1 = library.import_paths([f1])[0]["project_id"]

    f2 = tmp_path / "harmony.ust"
    f2.write_text("[#SETTING]\nTempo=120\n[#0000]\nLength=480\nLyric=a\n", encoding="utf-8")
    p2 = library.import_paths([f2])[0]["project_id"]

    # Create v2 in p1 and move it to p2
    v_extra = library.create_version(p1, "Extra Version", terms="Free to use")
    library.move_version_to_project(v_extra["id"], p2)

    p2_versions = library.list_versions(p2)
    assert len(p2_versions) == 2
    assert any(v["id"] == v_extra["id"] for v in p2_versions)
    # Ensure no sort_order collision
    sort_orders = [v["sort_order"] for v in p2_versions]
    assert len(sort_orders) == len(set(sort_orders))


def test_group_projects_merges_versions_and_preserves_metadata(tmp_path: Path) -> None:
    library = LibraryService(tmp_path / "grouping.sqlite3")
    f1 = tmp_path / "lead.svp"
    f1.write_bytes(b'{"version": 113, "time": {}, "tracks": []}')
    p1_imported = library.import_paths([f1])
    p1_id = p1_imported[0]["project_id"]

    f2 = tmp_path / "harmony.ust"
    f2.write_text("[#SETTING]\nTempo=120\n[#0000]\nLength=480\nLyric=a\n", encoding="utf-8")
    p2_imported = library.import_paths([f2])
    p2_id = p2_imported[0]["project_id"]
    p2_v_id = p2_imported[0]["version_id"]

    library.update_version(p2_v_id, distribution_terms="Permission required for commercial use", notes="Special mix")
    library.set_tags(p1_id, ["Original", "Rock"])
    library.set_tags(p2_id, ["Harmony", "Rock"])
    library.set_credits(p1_id, [("composer", "Artist 1")])
    library.set_credits(p2_id, [("tuner", "Artist 2")])

    # Merge p2 into p1
    library.group_projects(p2_id, p1_id)

    # p2 should no longer exist as a separate project
    projects = library.list_projects(version_scope="all")
    assert len(projects) == 1
    merged = projects[0]
    assert merged["id"] == p1_id
    assert len(merged["files"]) == 2
    tags = set(merged["tags"])
    assert "Original" in tags and "Harmony" in tags and "Rock" in tags

    # Credits preserved
    credits = {c["name"]: c["role"] for c in merged["credit_records"]}
    assert credits.get("Artist 1") == "composer"
    assert credits.get("Artist 2") == "tuner"

    # Version terms preserved
    versions = library.list_versions(p1_id)
    assert len(versions) == 2
    p2_ver_in_p1 = next(v for v in versions if v["id"] == p2_v_id)
    assert p2_ver_in_p1["distribution_terms"] == "Permission required for commercial use"
    assert p2_ver_in_p1["notes"] == "Special mix"


def test_open_project_resolves_preferred_version_and_default_file(tmp_path: Path) -> None:
    library = LibraryService(tmp_path / "grouping.sqlite3")
    f1 = tmp_path / "main.svp"
    f1.write_bytes(b'{"version": 113, "time": {}, "tracks": []}')
    imported = library.import_paths([f1])
    project_id = imported[0]["project_id"]
    file_id = imported[0]["file_id"]

    with mock.patch.object(library, "open_file") as mock_open:
        library.open_project(project_id)
        mock_open.assert_called_once_with(file_id, None)


def test_refresh_preserves_overrides_and_distribution_terms(tmp_path: Path) -> None:
    library = LibraryService(tmp_path / "grouping.sqlite3")
    f1 = tmp_path / "main.svp"
    f1.write_bytes(b'{"version": 113, "time": {}, "tracks": []}')
    imported = library.import_paths([f1])
    project_id = imported[0]["project_id"]
    file_id = imported[0]["file_id"]
    version_id = imported[0]["version_id"]

    library.update_version(version_id, distribution_terms="Strictly Non-Commercial")
    library.set_credits(project_id, [("tuner", "Master Tuner")])

    # Refresh the file
    library.refresh_file(file_id)

    # Check that terms and credits are unchanged
    proj = library.list_projects()[0]
    assert proj["files"][0]["version_terms"] == "Strictly Non-Commercial"
    assert any(c["name"] == "Master Tuner" for c in proj["credit_records"])
