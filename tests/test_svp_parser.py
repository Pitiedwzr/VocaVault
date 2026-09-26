from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from vocavault.models import ParseStatus, SignalState, TrackKind
from vocavault.parsers import ParserLimits, SvpParser

ROOT = Path(__file__).parents[1]
EXAMPLES = ROOT / "example"


def _write_svp(path: Path, data: dict[str, object], *, trailing_nuls: int = 0) -> Path:
    payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
    path.write_bytes(payload + (b"\0" * trailing_nuls))
    return path


def _project(*, version: int = 134) -> dict[str, object]:
    return {
        "version": version,
        "time": {
            "meter": [{"index": 0, "numerator": 4, "denominator": 4}],
            "tempo": [
                {"position": 0, "bpm": 120},
                {"position": 705_600_000, "bpm": 150},
            ],
        },
        "library": [
            {
                "uuid": "harmony",
                "name": "Harmony",
                "notes": [{"lyrics": "la"}],
                "parameters": {
                    "pitchDelta": {"points": [0, 0]},
                    "vibratoEnv": {"points": []},
                },
            }
        ],
        "tracks": [
            {
                "name": "Main",
                "mainGroup": {
                    "uuid": "main",
                    "notes": [{"lyrics": "你"}, {"lyrics": ""}],
                    "parameters": {
                        "pitchDelta": {"points": [0, 0, 10, 4.5]},
                        "vibratoEnv": {"points": []},
                        "loudness": {"points": [0, 0, 10, -2]},
                    },
                },
                "mainRef": {
                    "groupID": "main",
                    "isInstrumental": False,
                    "database": {
                        "name": "An Xiao",
                        "language": "mandarin",
                        "languageOverride": "japanese",
                        "backendType": "SVR2AI",
                        "version": "110",
                    },
                },
                "groups": [
                    {
                        "groupID": "harmony",
                        "isInstrumental": False,
                        "database": {
                            "name": "Ryo AI",
                            "language": "japanese",
                        },
                    }
                ],
            }
        ],
        "renderConfig": {},
    }


def test_detect_and_parse_bounded_svp_metadata(tmp_path: Path) -> None:
    path = _write_svp(tmp_path / "unicode-燈.svp", _project(), trailing_nuls=1)
    parser = SvpParser()

    detection = parser.detect(path)
    result = parser.parse(path)

    assert detection.supported
    assert detection.format_name == "svp"
    assert detection.format_version == "134"
    assert result.status is ParseStatus.PARSED
    assert result.tempo.initial_bpm == 120
    assert result.tempo.minimum_bpm == 120
    assert result.tempo.maximum_bpm == 150
    assert result.tempo.change_count == 1
    assert result.tempo.time_unit == "blick"
    assert result.tempo.resolution == 705_600_000
    assert result.details["trailing_nul_bytes"] == 1
    assert result.warnings == ("ignored 1 trailing NUL byte(s)",)

    track = result.tracks[0]
    assert track.kind is TrackKind.VOCAL
    assert track.voice_name == "An Xiao"
    assert track.languages == ("mandarin", "japanese")
    assert track.note_count == 3
    assert track.lyric_count == 2
    assert track.pitch is SignalState.DETECTED
    assert track.vibrato is SignalState.NONE_DETECTED
    assert track.dynamics is SignalState.DETECTED
    assert {voice["name"] for voice in track.details["voices"]} == {
        "An Xiao",
        "Ryo AI",
    }


@pytest.mark.parametrize("version", [113, 134, 153])
def test_supported_schema_versions(tmp_path: Path, version: int) -> None:
    path = _write_svp(tmp_path / f"v{version}.svp", _project(version=version))
    result = SvpParser().parse(path)
    assert result.status is ParseStatus.PARSED
    assert result.detection.format_version == str(version)


def test_unsupported_version_is_cataloguable(tmp_path: Path) -> None:
    path = _write_svp(tmp_path / "future.svp", _project(version=999))
    result = SvpParser().parse(path)
    assert result.status is ParseStatus.UNSUPPORTED
    assert result.detection.format_name == "svp"
    assert result.detection.format_version == "999"


def test_malformed_svp_fails_without_raising(tmp_path: Path) -> None:
    path = tmp_path / "broken.svp"
    path.write_text('{"version": 134, "tracks": [', encoding="utf-8")
    result = SvpParser().parse(path)
    assert result.status is ParseStatus.FAILED
    assert result.warnings


def test_limits_are_enforced(tmp_path: Path) -> None:
    path = _write_svp(tmp_path / "too-many-tracks.svp", _project())
    result = SvpParser(ParserLimits(max_tracks=0)).parse(path)
    assert result.status is ParseStatus.FAILED
    assert "resource limit" in result.warnings[-1]


def test_curve_limit_is_cumulative_across_curves(tmp_path: Path) -> None:
    project = _project()
    track = project["tracks"][0]
    track["groups"] = []
    track["mainGroup"]["parameters"] = {
        "pitchDelta": {"points": [0, 0, 10, 1]},
        "loudness": {"points": [0, 0, 10, 1]},
    }
    path = _write_svp(tmp_path / "curve-budget.svp", project)

    result = SvpParser(ParserLimits(max_curve_values=6)).parse(path)

    assert result.status is ParseStatus.FAILED
    assert "curve value count exceeds 6" in result.warnings[-1]


def test_malformed_nested_curve_shape_cannot_bypass_budget(tmp_path: Path) -> None:
    project = _project()
    track = project["tracks"][0]
    track["groups"] = []
    track["mainGroup"]["parameters"] = {
        "pitchDelta": {"points": [[0, 1, 2, 3, 4, 5, 6]]},
    }
    path = _write_svp(tmp_path / "nested-curve-budget.svp", project)

    result = SvpParser(ParserLimits(max_curve_values=6)).parse(path)

    assert result.status is ParseStatus.FAILED
    assert "curve value count exceeds 6" in result.warnings[-1]


def test_curve_limit_includes_vocal_modes_and_system_pitch(tmp_path: Path) -> None:
    project = _project()
    track = project["tracks"][0]
    track["groups"] = []
    track["mainGroup"]["parameters"] = {}
    track["mainGroup"]["vocalModes"] = {
        "Soft": {"points": [0, 0, 10, 1]},
    }
    track["mainRef"]["systemPitchDelta"] = {"points": [0, 0, 10, 1]}
    path = _write_svp(tmp_path / "all-curve-sources.svp", project)

    result = SvpParser(ParserLimits(max_curve_values=6)).parse(path)

    assert result.status is ParseStatus.FAILED
    assert "curve value count exceeds 6" in result.warnings[-1]


def test_tempo_entry_limit_is_enforced_before_iteration(tmp_path: Path) -> None:
    project = _project()
    project["time"]["tempo"] = [None, None, None]
    path = _write_svp(tmp_path / "tempo-budget.svp", project)

    result = SvpParser(ParserLimits(max_tempo_entries=2)).parse(path)

    assert result.status is ParseStatus.FAILED
    assert "tempo entry count 3 exceeds 2" in result.warnings[-1]


def test_audio_track_does_not_report_vocal_notes_or_tuning(tmp_path: Path) -> None:
    project = _project()
    track = project["tracks"][0]
    track["groups"] = []
    track["mainRef"]["isInstrumental"] = True
    path = _write_svp(tmp_path / "audio-track.svp", project)

    result = SvpParser().parse(path)

    track_result = result.tracks[0]
    assert track_result.kind is TrackKind.AUDIO
    assert track_result.note_count == 0
    assert track_result.lyric_count == 0
    assert track_result.voice_name is None
    assert track_result.languages == ()
    assert track_result.pitch is SignalState.UNKNOWN
    assert track_result.vibrato is SignalState.UNKNOWN
    assert track_result.dynamics is SignalState.UNKNOWN


def test_note_system_attributes_are_tuning_evidence(tmp_path: Path) -> None:
    project = _project()
    track = project["tracks"][0]
    track["mainGroup"]["notes"] = [
        {
            "lyrics": "la",
            "attributes": {},
            "systemAttributes": {
                "tF0Offset": 0.5,
                "dF0Left": 0,
                "dF0Right": -1.25,
                "dF0Vbr": 2,
            },
        }
    ]
    track["mainGroup"]["parameters"] = {}
    track["groups"] = []
    path = _write_svp(tmp_path / "system-attributes.svp", project)

    result = SvpParser().parse(path)

    assert result.status is ParseStatus.PARSED
    assert result.tracks[0].pitch is SignalState.DETECTED
    assert result.tracks[0].vibrato is SignalState.DETECTED
    assert result.tracks[0].details["signal_counts"]["pitch"] == 2
    assert result.tracks[0].details["signal_counts"]["vibrato"] == 1


def test_huge_numeric_fields_are_contained(tmp_path: Path) -> None:
    project = _project()
    project["time"]["tempo"][0]["bpm"] = 10**400
    track = project["tracks"][0]
    track["mainGroup"]["notes"][0]["systemAttributes"] = {
        "tF0Offset": 10**400,
        "dF0Vbr": -(10**400),
    }
    path = _write_svp(tmp_path / "huge-numbers.svp", project)

    result = SvpParser().parse(path)

    assert result.status is ParseStatus.PARTIAL
    assert result.tempo.initial_bpm == 150
    assert result.tracks[0].pitch is SignalState.DETECTED
    assert result.tracks[0].vibrato is SignalState.NONE_DETECTED
    assert any("invalid tempo entry" in warning for warning in result.warnings)


def test_oversized_json_integer_parse_failure_is_contained(tmp_path: Path) -> None:
    path = tmp_path / "huge-integer.svp"
    path.write_text(
        '{"version":134,"time":{"tempo":[{"position":0,"bpm":'
        + ("9" * 5_000)
        + '}]},"tracks":[],"renderConfig":{}}',
        encoding="utf-8",
    )

    result = SvpParser().parse(path)

    assert result.status is ParseStatus.FAILED
    assert "SVP parse failed" in result.warnings[0]


def test_malformed_field_shapes_are_partial_not_exceptions(tmp_path: Path) -> None:
    project = _project()
    project["time"]["tempo"] = [None, {"position": [], "bpm": {}}]
    project["library"] = {"not": "an array"}
    track = project["tracks"][0]
    track["groups"] = {"not": "an array"}
    track["mainGroup"]["notes"] = {"not": "an array"}
    track["mainGroup"]["parameters"] = {
        "pitchDelta": {"points": {"not": "an array"}},
        "vibratoEnv": [],
    }
    path = _write_svp(tmp_path / "malformed-fields.svp", project)

    result = SvpParser().parse(path)

    assert result.status is ParseStatus.PARTIAL
    assert result.tracks[0].note_count == 0
    assert result.warnings


def test_changed_during_read_is_a_contained_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write_svp(tmp_path / "changing.svp", _project())
    real_fstat = os.fstat
    calls = 0

    def changing_fstat(descriptor: int) -> os.stat_result | SimpleNamespace:
        nonlocal calls
        calls += 1
        result = real_fstat(descriptor)
        if calls == 2:
            return SimpleNamespace(
                st_size=result.st_size,
                st_mtime_ns=result.st_mtime_ns + 1,
                st_ino=result.st_ino,
            )
        return result

    monkeypatch.setattr("vocavault.parsers.base.os.fstat", changing_fstat)

    result = SvpParser().parse(path)

    assert result.status is ParseStatus.FAILED
    assert "source changed while it was being read" in result.warnings[0]


def test_non_svp_json_is_not_accepted(tmp_path: Path) -> None:
    path = _write_svp(tmp_path / "arbitrary.svp", {"version": 134, "items": []})
    result = SvpParser().parse(path)
    assert result.status is ParseStatus.UNSUPPORTED


@pytest.mark.skipif(
    not (EXAMPLES / "ARTPOP by Yugifu1_edited.svp").is_file(),
    reason="private example corpus is not present",
)
def test_parser_does_not_modify_representative_source() -> None:
    path = EXAMPLES / "ARTPOP by Yugifu1_edited.svp"
    before = hashlib.sha256(path.read_bytes()).digest()
    before_stat = path.stat()

    result = SvpParser().parse(path)

    after = hashlib.sha256(path.read_bytes()).digest()
    after_stat = path.stat()
    assert result.status is ParseStatus.PARSED
    assert result.detection.format_version == "153"
    assert len(result.tracks) == 5
    assert result.tracks[0].voice_name == "Kevin"
    assert result.tracks[0].languages == ("english",)
    assert before == after
    assert before_stat.st_mtime_ns == after_stat.st_mtime_ns


@pytest.mark.skipif(
    not EXAMPLES.is_dir() or not any(EXAMPLES.rglob("*.svp")),
    reason="private example corpus is not present",
)
def test_all_representative_svp_versions_parse() -> None:
    files = sorted(EXAMPLES.rglob("*.svp"))
    results = [SvpParser().parse(path) for path in files]
    assert files
    assert {result.detection.format_version for result in results} == {
        "113",
        "134",
        "153",
    }
    assert all(result.status is ParseStatus.PARSED for result in results)
