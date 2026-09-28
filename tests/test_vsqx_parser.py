from __future__ import annotations

from pathlib import Path

import pytest

from vocavault.models import ParseStatus, SignalState, TrackKind
from vocavault.parsers.base import ParserLimits
from vocavault.parsers.vsqx import VsqxParser


def test_vsqx_detect_valid_header(tmp_path: Path) -> None:
    vsqx_file = tmp_path / "song.vsqx"
    vsqx_file.write_text(
        '<vsq4 xmlns="http://www.yamaha.co.jp/vocaloid/schema/vsq4/"><version><![CDATA[4.0.0.1]]></version></vsq4>',
        encoding="utf-8",
    )
    detection = VsqxParser().detect(vsqx_file)
    assert detection.supported is True
    assert detection.format_name == "vsqx"
    assert detection.format_version == "4.0.0.1"


def test_vsqx_detect_invalid_file(tmp_path: Path) -> None:
    wrong_ext = tmp_path / "song.xml"
    wrong_ext.write_text("<vsq4></vsq4>", encoding="utf-8")
    assert VsqxParser().detect(wrong_ext).supported is False

    invalid_content = tmp_path / "bad.vsqx"
    invalid_content.write_bytes(b"NOT XML AT ALL")
    detection = VsqxParser().detect(invalid_content)
    assert detection.supported is False


def test_vsqx_parse_synthetic_project(tmp_path: Path) -> None:
    vsqx_content = """<?xml version="1.0" encoding="UTF-8"?>
<vsq4 xmlns="http://www.yamaha.co.jp/vocaloid/schema/vsq4/">
    <version><![CDATA[4.0.0.3]]></version>
    <vVoiceTable>
        <vVoice>
            <bs>4</bs>
            <pc>0</pc>
            <id>HATSUNE_MIKU_V4X</id>
            <name>Miku_V4X_Original</name>
        </vVoice>
    </vVoiceTable>
    <masterTrack>
        <resolution>480</resolution>
        <tempo><t>0</t><v>12500</v></tempo>
        <tempo><t>1920</t><v>13500</v></tempo>
    </masterTrack>
    <vsTrack>
        <tNo>0</tNo>
        <name>Lead Vocal</name>
        <vsPart>
            <t>0</t>
            <playTime>7680</playTime>
            <name>Part 1</name>
            <singer><t>0</t><bs>4</bs><pc>0</pc></singer>
            <note>
                <t>0</t>
                <dur>480</dur>
                <n>60</n>
                <v>64</v>
                <y><![CDATA[み]]></y>
                <p><![CDATA[m i]]></p>
                <nStyle>
                    <v id="bendDep">10</v>
                    <v id="bendLen">20</v>
                    <v id="risePort">0</v>
                    <v id="fallPort">0</v>
                    <v id="vibLen">30</v>
                    <v id="vibType">1</v>
                    <v id="accent">65</v>
                    <v id="decay">50</v>
                </nStyle>
            </note>
            <note>
                <t>480</t>
                <dur>480</dur>
                <n>62</n>
                <v>64</v>
                <y><![CDATA[く]]></y>
                <p><![CDATA[k M]]></p>
            </note>
        </vsPart>
    </vsTrack>
    <monoTrack>
        <name>Backing Track</name>
        <wavPart>
            <filePath>inst.wav</filePath>
        </wavPart>
    </monoTrack>
</vsq4>
"""
    vsqx_file = tmp_path / "miku.vsqx"
    vsqx_file.write_text(vsqx_content, encoding="utf-8")

    result = VsqxParser().parse(vsqx_file)
    assert result.status == ParseStatus.PARSED
    assert result.detection.supported is True
    assert result.detection.format_version == "4.0.0.3"

    assert result.tempo.initial_bpm == 125.0
    assert result.tempo.maximum_bpm == 135.0
    assert result.tempo.has_changes is True
    assert result.tempo.change_count == 1

    assert len(result.tracks) == 2
    vocal = result.tracks[0]
    assert vocal.kind == TrackKind.VOCAL
    assert vocal.name == "Lead Vocal"
    assert vocal.voice_name == "Miku_V4X_Original"
    assert vocal.voice_identifier == "HATSUNE_MIKU_V4X"
    assert vocal.note_count == 2
    assert vocal.lyric_count == 2
    assert vocal.pitch == SignalState.DETECTED
    assert vocal.vibrato == SignalState.DETECTED
    assert vocal.dynamics == SignalState.DETECTED
    assert "ja" in vocal.languages

    audio = result.tracks[1]
    assert audio.kind == TrackKind.AUDIO
    assert audio.name == "Backing Track"

    assert len(result.references) == 1
    assert result.references[0].original == "inst.wav"
    assert result.references[0].kind == "audio"


def test_vsqx_malformed_xml_fails_cleanly(tmp_path: Path) -> None:
    bad_vsqx = tmp_path / "broken.vsqx"
    bad_vsqx.write_text("<vsq4><open_without_close>", encoding="utf-8")
    result = VsqxParser().parse(bad_vsqx)
    assert result.status == ParseStatus.FAILED
    assert any("malformed XML" in w for w in result.warnings)


def test_vsqx_enforces_limits(tmp_path: Path) -> None:
    notes_xml = "".join(f"<note><t>{i * 480}</t><dur>480</dur><n>60</n><y>a</y></note>" for i in range(30))
    vsqx_content = f"""<vsq4 xmlns="http://www.yamaha.co.jp/vocaloid/schema/vsq4/">
        <masterTrack><tempo><t>0</t><v>12000</v></tempo></masterTrack>
        <vsTrack><vsPart>{notes_xml}</vsPart></vsTrack>
    </vsq4>"""
    vsqx_file = tmp_path / "large.vsqx"
    vsqx_file.write_text(vsqx_content, encoding="utf-8")

    parser = VsqxParser(limits=ParserLimits(max_notes=20))
    result = parser.parse(vsqx_file)
    assert result.status == ParseStatus.FAILED
    assert any("note count exceeds" in w for w in result.warnings)
