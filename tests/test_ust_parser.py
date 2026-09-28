from __future__ import annotations

from pathlib import Path

import pytest

from vocavault.models import ParseStatus, SignalState, TrackKind
from vocavault.parsers.base import ParserLimits
from vocavault.parsers.ust import UstParser


def test_ust_detect_valid_header(tmp_path: Path) -> None:
    ust_file = tmp_path / "song.ust"
    ust_file.write_text(
        "[#VERSION]\nUST Version1.2\n[#SETTING]\nTempo=120.00\nTracks=1\n[#0000]\nLength=480\nLyric=a\n",
        encoding="utf-8",
    )
    detection = UstParser().detect(ust_file)
    assert detection.supported is True
    assert detection.format_name == "ust"
    assert detection.format_version == "1.2"


def test_ust_detect_invalid_file(tmp_path: Path) -> None:
    wrong_ext = tmp_path / "song.txt"
    wrong_ext.write_text("[#VERSION]\nUST Version1.2\n", encoding="utf-8")
    assert UstParser().detect(wrong_ext).supported is False

    invalid_content = tmp_path / "bad.ust"
    invalid_content.write_bytes(b"NOT A UST FILE AT ALL")
    detection = UstParser().detect(invalid_content)
    assert detection.supported is False


def test_ust_parse_synthetic_project(tmp_path: Path) -> None:
    ust_content = (
        "[#VERSION]\n"
        "UST Version1.2\n"
        "[#SETTING]\n"
        "Tempo=130.00\n"
        "Tracks=1\n"
        "ProjectName=Synthetic Song\n"
        "VoiceDir=%VOICE%Kasane_Teto\n"
        "OutFile=output.wav\n"
        "[#0000]\n"
        "Length=480\n"
        "Lyric=R\n"
        "NoteNum=60\n"
        "[#0001]\n"
        "Length=480\n"
        "Lyric=か\n"
        "NoteNum=62\n"
        "PBY=0,10,20,0\n"
        "VBR=100,50,60,20,20,0,0\n"
        "Envelope=0,5,35,0,100,100,0\n"
        "[#0002]\n"
        "Length=480\n"
        "Lyric=さ\n"
        "NoteNum=64\n"
        "Tempo=140.00\n"
        "[#TRACKEND]\n"
    )
    ust_file = tmp_path / "test.ust"
    ust_file.write_text(ust_content, encoding="utf-8")

    result = UstParser().parse(ust_file)
    assert result.status == ParseStatus.PARSED
    assert result.detection.supported is True
    assert result.detection.format_version == "1.2"
    assert result.tempo.initial_bpm == 130.0
    assert result.tempo.has_changes is True
    assert result.tempo.change_count == 1
    assert result.tempo.maximum_bpm == 140.0

    assert len(result.tracks) == 1
    track = result.tracks[0]
    assert track.kind == TrackKind.VOCAL
    assert track.voice_name == "Kasane_Teto"
    assert track.note_count == 2
    assert track.lyric_count == 2
    assert track.pitch == SignalState.DETECTED
    assert track.vibrato == SignalState.DETECTED
    assert track.dynamics == SignalState.DETECTED
    assert "ja" in track.languages

    ref_kinds = {r.kind: r.original for r in result.references}
    assert ref_kinds["voicebank"] == "%VOICE%Kasane_Teto"
    assert ref_kinds["audio"] == "output.wav"


def test_ust_parse_cp932_encoding(tmp_path: Path) -> None:
    ust_content = (
        "[#SETTING]\r\n"
        "UstVersion=1.19\r\n"
        "Tempo=160.00\r\n"
        "ProjectName=テスト曲\r\n"
        "VoiceDir=%VOICE%初音ミク\r\n"
        "[#0000]\r\n"
        "Length=480\r\n"
        "Lyric=あ\r\n"
        "NoteNum=60\r\n"
    )
    ust_file = tmp_path / "cp932.ust"
    ust_file.write_bytes(ust_content.encode("cp932"))

    result = UstParser().parse(ust_file)
    assert result.status == ParseStatus.PARSED
    assert result.tracks[0].name == "テスト曲"
    assert result.tracks[0].voice_name == "初音ミク"
    assert result.tracks[0].note_count == 1
    assert result.tracks[0].pitch == SignalState.NONE_DETECTED
    assert result.tracks[0].vibrato == SignalState.NONE_DETECTED


def test_ust_parser_enforces_note_limits(tmp_path: Path) -> None:
    notes = "\n".join(f"[#{i:04d}]\nLength=480\nLyric=la\nNoteNum=60" for i in range(25))
    ust_content = f"[#SETTING]\nTempo=120\n{notes}\n"
    ust_file = tmp_path / "large.ust"
    ust_file.write_text(ust_content, encoding="utf-8")

    parser = UstParser(limits=ParserLimits(max_notes=20))
    result = parser.parse(ust_file)
    assert result.status == ParseStatus.FAILED
    assert any("note count exceeds" in w for w in result.warnings)
