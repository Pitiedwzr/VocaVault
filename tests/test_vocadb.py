from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from vocavault.library import LibraryError, LibraryService
from vocavault.vocadb import (
    VocaDbCandidate,
    VocaDbClient,
    VocaDbNetworkError,
    VocaDbNotFoundError,
    VocaDbRateLimitError,
)

SAMPLE_VOCADB_PAYLOAD = {
    "id": 12345,
    "name": "ゴーストルール",
    "artistString": "DECO*27 feat. 初音ミク",
    "songType": "Original",
    "publishDate": "2016-01-08",
    "names": [
        {"value": "ゴーストルール", "language": "Japanese"},
        {"value": "Ghost Rule", "language": "English"},
    ],
    "artists": [
        {"name": "DECO*27", "roles": "Composer", "categories": "Producer"},
        {"name": "初音ミク", "roles": "Vocalist", "categories": "Vocalist"},
    ],
    "pvs": [
        {"service": "Youtube", "url": "https://www.youtube.com/watch?v=example", "name": "Official PV"}
    ],
    "webLinks": [
        {"url": "https://vocadb.net/S/12345", "description": "VocaDB Page"}
    ],
}


def test_candidate_data_model() -> None:
    candidate = VocaDbCandidate.from_api_dict(SAMPLE_VOCADB_PAYLOAD)
    assert candidate.id == 12345
    assert candidate.name == "ゴーストルール"
    assert candidate.artist_string == "DECO*27 feat. 初音ミク"
    assert len(candidate.names) == 2
    assert len(candidate.artists) == 2
    assert len(candidate.links) == 2


def test_client_search_and_caching(tmp_path: Path) -> None:
    library = LibraryService(tmp_path / "lib.sqlite3")
    with library.database.connection() as conn:
        client = VocaDbClient(cache_connection=conn)
        with mock.patch.object(client, "_get_json", return_value={"items": [SAMPLE_VOCADB_PAYLOAD]}):
            results = client.search_songs("Ghost Rule")
            assert len(results) == 1
            assert results[0].id == 12345

        # Second search should hit cache without calling _get_json
        with mock.patch.object(client, "_get_json", side_effect=AssertionError("Should hit cache")):
            cached_results = client.search_songs("Ghost Rule")
            assert len(cached_results) == 1
            assert cached_results[0].id == 12345


def test_client_error_handling() -> None:
    client = VocaDbClient()
    with mock.patch("urllib.request.urlopen", side_effect=OSError("Network unreachable")):
        with pytest.raises(VocaDbNetworkError):
            client.search_songs("Test")


def test_apply_vocadb_enrichment_workflow(tmp_path: Path) -> None:
    library = LibraryService(tmp_path / "lib.sqlite3")
    svp_file = tmp_path / "ghost.svp"
    svp_file.write_bytes(b'{"version": 113, "time": {}, "tracks": []}')
    imported = library.import_paths([svp_file])
    project_id = imported[0]["project_id"]

    # Add tuner credit to project
    library.set_credits(project_id, [("tuner", "Custom Tuner")])

    # Apply VocaDB enrichment
    library.apply_vocadb_enrichment(project_id, SAMPLE_VOCADB_PAYLOAD)

    project = library.list_projects()[0]
    assert project["name"] == "ゴーストルール"
    assert "Ghost Rule" in project["aliases"]
    assert "ゴーストルール" in project["aliases"]

    # Tuner credit must still exist! Song enrichment must not overwrite project credits.
    assert "Custom Tuner (tuner)" in project["credits"]
    assert "DECO*27 (Composer)" in project["credits"]
    assert "初音ミク (Vocalist)" in project["credits"]
    assert any("https://www.youtube.com/watch?v=example" in link for link in project["links"])

    # New alias should be searchable
    assert len(library.list_projects(query="Ghost Rule")) == 1
    # Credits should be searchable
    assert len(library.list_projects(query="DECO*27")) == 1
    assert len(library.list_projects(query="初音ミク")) == 1

    # Unlink VocaDB
    library.unlink_vocadb(project_id)
    # Metadata should remain intact
    unlinked_project = library.list_projects()[0]
    assert unlinked_project["name"] == "ゴーストルール"
    assert "Ghost Rule" in unlinked_project["aliases"]
    assert "Custom Tuner (tuner)" in unlinked_project["credits"]
    assert "DECO*27 (Composer)" in unlinked_project["credits"]


def test_vocadb_enrichment_and_refresh_respects_manual_override(tmp_path: Path) -> None:
    library = LibraryService(tmp_path / "lib_override.sqlite3")
    svp_file = tmp_path / "ghost.svp"
    svp_file.write_bytes(b'{"version": 113, "time": {}, "tracks": []}')
    imported = library.import_paths([svp_file])
    project_id = imported[0]["project_id"]

    # Apply VocaDB enrichment initially
    library.apply_vocadb_enrichment(project_id, SAMPLE_VOCADB_PAYLOAD)
    assert library.list_projects()[0]["name"] == "ゴーストルール"

    # User manually edits and saves project name in inspector
    library.save_project_metadata(
        project_id,
        name="Custom Ghost Rule Remix",
        description="",
        status_id=None,
        aliases=["Ghost Rule"],
        credits=[("tuner", "Custom Tuner")],
        tags=[],
    )
    assert library.list_projects()[0]["name"] == "Custom Ghost Rule Remix"

    # Refreshing from VocaDB must NOT overwrite the manual edit
    with mock.patch.object(library, "fetch_vocadb_candidate", return_value=SAMPLE_VOCADB_PAYLOAD):
        library.refresh_vocadb(project_id)
    refreshed_project = library.list_projects()[0]
    assert refreshed_project["name"] == "Custom Ghost Rule Remix"
    # But other enriched data remains present
    assert "Ghost Rule" in refreshed_project["aliases"]
    assert "DECO*27 (Composer)" in refreshed_project["credits"]

