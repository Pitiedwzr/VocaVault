from unittest.mock import patch

import pytest

from vocavault import database as database_module
from vocavault.library import LibraryError, LibraryService
from vocavault.models import ParseStatus, SignalState
from vocavault.parsers.ust import UstParser
from vocavault.parsers.vsqx import VsqxParser
from vocavault.vocadb import VocaDbClient, VocaDbNetworkError


def add(library, root, name):
    path = root / (name + ".svp")
    path.write_text('{"version":113,"time":{},"tracks":[]}')
    return library.import_paths([path])[0]


def test_unchecked_name_and_dismissed_alias_survive_refresh(tmp_path):
    library = LibraryService(tmp_path / "library.db")
    project = add(library, tmp_path, "original")["project_id"]
    candidate = {
        "id": 1,
        "name": "Remote title",
        "names": [{"value": "Dismissed alias"}],
    }
    library.apply_vocadb_enrichment(project, candidate)
    library.save_project_metadata(
        project,
        name="Manual title",
        description="",
        status_id=None,
        aliases=[],
        credits=[],
        tags=[],
    )
    library.apply_vocadb_enrichment(project, candidate, apply_name=False)
    with patch.object(library, "fetch_vocadb_candidate", return_value=candidate):
        library.refresh_vocadb(project)
    result = library.list_projects()[0]
    assert result["name"] == "Manual title"
    assert "Dismissed alias" not in result["aliases"]


def test_forced_refresh_fetches_new_data_and_reports_network_failure(tmp_path):
    library = LibraryService(tmp_path / "library.db")
    with library.database.connection() as connection:
        client = VocaDbClient(cache_connection=connection)
        with patch.object(client, "_get_json", return_value={"id": 1, "name": "Old"}):
            client.get_song(1)
        with patch.object(
            client, "_get_json", return_value={"id": 1, "name": "New"}
        ) as request:
            assert client.get_song(1, force_refresh=True).name == "New"
            request.assert_called_once()
        with patch.object(
            client, "_get_json", side_effect=VocaDbNetworkError("offline")
        ):
            with pytest.raises(VocaDbNetworkError):
                client.get_song(1, force_refresh=True)
            assert client.get_song(1).name == "New"


def test_grouping_preserves_both_song_metadata_sets_and_provenance(tmp_path):
    library = LibraryService(tmp_path / "library.db")
    source = add(library, tmp_path, "source")["project_id"]
    target = add(library, tmp_path, "target")["project_id"]
    library.set_aliases(source, ["Incoming alias"])
    library.set_aliases(target, ["Existing alias"])
    with library.database.connection() as c:
        song_id = c.execute(
            "SELECT song_id FROM projects WHERE id=?", (source,)
        ).fetchone()[0]
        c.execute(
            "INSERT INTO contributors(id, display_name, normalized_name) VALUES ('composer','Artist','artist')"
        )
        c.execute(
            "INSERT INTO song_credits(id,song_id,contributor_id,role,source_type,source_identifier) VALUES ('credit',?,'composer','composer','user','original')",
            (song_id,),
        )
        c.execute(
            "INSERT INTO song_links(id,song_id,kind,url,label) VALUES ('link',?,'media','https://example.com/original','Original')",
            (song_id,),
        )
        c.commit()
    library.group_projects(source, target)
    merged = library.list_projects()[0]
    assert {"Incoming alias", "Existing alias"} <= set(merged["aliases"])
    assert "Artist (composer)" in merged["credits"]
    assert any("https://example.com/original" in value for value in merged["links"])
    assert library.list_projects(query="Incoming alias")[0]["id"] == target


def test_grouping_conflicting_remote_songs_is_atomic(tmp_path):
    library = LibraryService(tmp_path / "library.db")
    a = add(library, tmp_path, "one")["project_id"]
    b = add(library, tmp_path, "two")["project_id"]
    library.apply_vocadb_enrichment(a, {"id": 1, "name": "One"})
    library.apply_vocadb_enrichment(b, {"id": 2, "name": "Two"})
    with pytest.raises(LibraryError, match="different VocaDB"):
        library.group_projects(a, b)
    assert len(library.list_projects()) == 2
    assert len(library.list_versions(a)) == 1
    assert len(library.list_versions(b)) == 1


def test_index_updates_after_edits_without_manual_rebuild(tmp_path):
    library = LibraryService(tmp_path / "library.db")
    item = add(library, tmp_path, "initial")
    library.set_aliases(item["project_id"], ["Ghost Rule"])
    assert library.list_projects(query="gosht rul")
    library.set_aliases(item["project_id"], ["Completely different"])
    assert library.list_projects(query="gosht rul") == []
    library.update_version(item["version_id"], label="Final mastering revision")
    assert library.list_projects(query="mastering")[0]["match_field"] == "version"
    # FTS is really consulted, rather than merely maintained.
    statements = []
    connect = library.database.connect

    def traced(**kwargs):
        connection = connect(**kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    with patch.object(library.database, "connect", side_effect=traced):
        assert library.list_projects(query="mastering")
    assert any("search_fts" in sql and " MATCH " in sql for sql in statements)


def test_vsq3_and_vsq4_equivalent_read_only_observations(tmp_path):
    schemas = {
        "vsq3": (
            "posTick",
            "bpm",
            "vsTrackNo",
            "trackName",
            "musicalPart",
            "lyric",
            "mCtrl",
            "attr",
            "PIT",
            "DYN",
            "vBS",
            "vPC",
            "vVoiceName",
            "compID",
        ),
        "vsq4": (
            "t",
            "v",
            "tNo",
            "name",
            "vsPart",
            "y",
            "cc",
            "v",
            "P",
            "D",
            "bs",
            "pc",
            "name",
            "id",
        ),
    }
    for schema, names in schemas.items():
        (
            tick,
            bpm,
            number,
            title,
            part,
            lyric,
            ctrl,
            attr,
            pitch,
            dyn,
            bs,
            pc,
            voice,
            vid,
        ) = names
        text = f'''<{schema} xmlns="http://www.yamaha.co.jp/vocaloid/schema/{schema}/">
          <vVoiceTable><vVoice><{bs}>0</{bs}><{pc}>0</{pc}><{voice}>Singer</{voice}><{vid}>voice-id</{vid}></vVoice></vVoiceTable>
          <masterTrack><resolution>480</resolution><tempo><{tick}>0</{tick}><{bpm}>15000</{bpm}></tempo></masterTrack>
          <vsTrack><{number}>0</{number}><{title}>Lead</{title}><{part}>
          <singer><{bs}>0</{bs}><{pc}>0</{pc}></singer><note><{lyric}>a</{lyric}></note>
          <{ctrl}><{tick}>0</{tick}><{attr} id="{pitch}">1024</{attr}></{ctrl}>
          <{ctrl}><{tick}>0</{tick}><{attr} id="{dyn}">90</{attr}></{ctrl}>
          </{part}></vsTrack></{schema}>'''
        path = tmp_path / (schema + ".vsqx")
        path.write_text(text)
        before = path.read_bytes()
        result = VsqxParser().parse(path)
        assert result.status == ParseStatus.PARSED
        assert result.tempo.initial_bpm == 150
        assert result.tracks[0].name == "Lead"
        assert result.tracks[0].note_count == result.tracks[0].lyric_count == 1
        assert result.tracks[0].voice_name == "Singer"
        assert result.tracks[0].voice_identifier == "voice-id"
        assert (
            result.tracks[0].pitch == result.tracks[0].dynamics == SignalState.DETECTED
        )
        assert path.read_bytes() == before


@pytest.mark.parametrize(
    "xml",
    [
        '<!DOCTYPE vsq4 [<!ENTITY test "value">]><vsq4>&test;</vsq4>',
        "<vsq4>" + "<deep>" * 130 + "</deep>" * 130 + "</vsq4>",
    ],
)
def test_vsqx_rejects_entities_and_excessive_nesting(tmp_path, xml):
    path = tmp_path / "bounded.vsqx"
    path.write_text(xml)
    assert VsqxParser().parse(path).status == ParseStatus.FAILED


def test_ust_unknown_tempo_and_missing_lyrics_are_not_fabricated(tmp_path):
    path = tmp_path / "unknown.ust"
    path.write_text(
        "[#SETTING]\nTempo=nan\n[#0000]\nLength=480\nLyric=\nPiches=0,5,0\nVBR=0,180,35\n"
    )
    result = UstParser().parse(path)
    assert result.tempo.initial_bpm is None
    assert result.status == ParseStatus.PARTIAL
    assert result.tracks[0].note_count == 1
    assert result.tracks[0].lyric_count == 0
    assert result.tracks[0].pitch == SignalState.DETECTED
    assert result.tracks[0].vibrato == SignalState.NONE_DETECTED


def test_upgrade_v3_and_restore_invalidate_derived_search_cache(tmp_path):
    path = tmp_path / "old.db"
    with (
        patch.object(database_module, "_MIGRATIONS", database_module._MIGRATIONS[:3]),
        patch.object(database_module, "LATEST_SCHEMA_VERSION", 3),
    ):
        database_module.Database(path).initialize()
    lib = LibraryService(path)
    assert list(tmp_path.glob("old.pre-migration-v3-*.db"))
    item = add(lib, tmp_path, "old vocabulary")
    assert lib.list_projects(query="old vocabulary")
    backup = tmp_path / "backup.db"
    lib.backup_to(backup)
    lib.update_project(item["project_id"], name="new vocabulary")
    assert lib.list_projects(query="new vocabulary")
    lib.restore_from(backup)
    assert lib.list_projects(query="old vocabulary")[0]["name"] == "old vocabulary"
