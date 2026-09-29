"""Bounded, read-only metadata extraction for VOCALOID 3 and 4 project files (.vsqx)."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Final

from vocavault.models import (
    DependencyReference,
    Detection,
    ParseResult,
    ParseStatus,
    SignalState,
    TempoSummary,
    TrackKind,
    TrackSummary,
)
from vocavault.parsers.base import (
    ParserError,
    ParserLimitError,
    ProjectParser,
    SourceChangedError,
)

from .vsqx_schema import CONTROLLERS, VSQ3_TAGS

_HEADER_MAGIC = re.compile(
    rb"<(?:vsq3|vsq4)[\s>]|vocaloid/schema/vsq[34]/", re.IGNORECASE
)
_RESOLUTION_DEFAULT: Final = 480

_HIRAGANA_KATAKANA = re.compile(r"[\u3040-\u30ff]")
_CJK_UNIFIED = re.compile(r"[\u4e00-\u9fff]")


def _detect_languages(text_sample: str) -> tuple[str, ...]:
    languages: list[str] = []
    if _HIRAGANA_KATAKANA.search(text_sample):
        languages.append("ja")
    elif _CJK_UNIFIED.search(text_sample):
        languages.append("zh")
    return tuple(languages)


def _strip_ns(tag: str) -> str:
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _find_child(elem: ET.Element, name: str) -> ET.Element | None:
    for child in elem:
        if _strip_ns(child.tag) == name:
            return child
    return None


def _find_children(elem: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in elem if _strip_ns(child.tag) == name]


def _find_text(elem: ET.Element, name: str, default: str = "") -> str:
    child = _find_child(elem, name)
    if child is not None and child.text is not None:
        return child.text.strip()
    return default


class VsqxParser(ProjectParser):
    parser_id = "builtin.vsqx"
    parser_version = "0.2.1"

    def detect(self, path: str | Path) -> Detection:
        source = Path(path)
        if source.suffix.casefold() != ".vsqx":
            return Detection(False, evidence="filename extension is not .vsqx")
        try:
            header = self._read_header(source)
        except (OSError, ParserError) as exc:
            return Detection(False, format_name="vsqx", evidence=str(exc))

        if not _HEADER_MAGIC.search(header):
            return Detection(
                False,
                format_name="vsqx",
                evidence=".vsqx extension without recognized VOCALOID XML header",
            )

        version_match = re.search(
            r"<version>\s*(?:<!\[CDATA\[)?\s*([0-9.]+)",
            header.decode("latin1", errors="replace"),
            re.IGNORECASE,
        )
        version = version_match.group(1) if version_match else None
        root_match = re.search(
            r"<(vsq[34])[\s>]", header.decode("latin1", errors="replace"), re.IGNORECASE
        )
        schema_tag = root_match.group(1).lower() if root_match else "vsqx"
        evidence = f"VOCALOID project file ({schema_tag})"
        if version:
            evidence += f" schema version {version}"
        return Detection(True, "vsqx", version or schema_tag, evidence)

    def parse(self, path: str | Path) -> ParseResult:
        source = Path(path)
        detection = self.detect(source)
        if not detection.supported:
            return ParseResult(
                parser_id=self.parser_id,
                parser_version=self.parser_version,
                status=ParseStatus.UNSUPPORTED,
                detection=detection,
                warnings=(
                    detection.evidence
                    or "file is not recognized as a supported VSQX project",
                ),
            )

        try:
            raw_data = self._read_stable(source)
        except OSError as exc:
            return self._failed(detection, f"could not read file: {exc}")
        except SourceChangedError as exc:
            return self._failed(detection, str(exc))
        except ParserLimitError as exc:
            return self._failed(detection, str(exc))

        try:
            root = ET.fromstring(
                raw_data, parser=ET.XMLParser(target=_BoundedTree(self.limits))
            )
        except (ET.ParseError, ValueError, ParserError) as exc:
            return self._failed(detection, f"malformed XML: {exc}")

        root_name = _strip_ns(root.tag).lower()
        if root_name not in ("vsq3", "vsq4"):
            return ParseResult(
                parser_id=self.parser_id,
                parser_version=self.parser_version,
                status=ParseStatus.UNSUPPORTED,
                detection=Detection(
                    False, "vsqx", None, f"unsupported root tag {root_name}"
                ),
                warnings=(f"unsupported root tag {root_name}",),
            )

        schema_version = _find_text(root, "version") or detection.format_version
        if root_name == "vsq3":
            for element in root.iter():
                tag = _strip_ns(element.tag)
                element.tag = VSQ3_TAGS.get(tag, tag)
        warnings: list[str] = []

        # Build voice lookup table from vVoiceTable
        voices: dict[tuple[str, str], tuple[str, str]] = {}
        vvoice_table = _find_child(root, "vVoiceTable")
        if vvoice_table is not None:
            for vvoice in _find_children(vvoice_table, "vVoice"):
                bs = _find_text(vvoice, "bs")
                pc = _find_text(vvoice, "pc")
                name = _find_text(vvoice, "name")
                vid = _find_text(vvoice, "id") or _find_text(vvoice, "compID")
                voices[(bs, pc)] = (name, vid)

        # Parse masterTrack (tempo, resolution)
        resolution = _RESOLUTION_DEFAULT
        master_track = _find_child(root, "masterTrack")
        tempo_events: list[dict[str, Any]] = []
        all_bpms: list[float] = []

        if master_track is not None:
            res_text = _find_text(master_track, "resolution")
            if res_text.isdigit():
                resolution = int(res_text)

            tempos = _find_children(master_track, "tempo")
            if len(tempos) > self.limits.max_tempo_entries:
                return self._failed(
                    detection,
                    f"tempo entries count exceeds limit of {self.limits.max_tempo_entries}",
                )

            for t_elem in tempos:
                pos_str = _find_text(t_elem, "t", "0")
                val_str = _find_text(t_elem, "v")
                try:
                    pos = int(pos_str)
                    bpm = round(int(val_str) / 100.0, 2)
                    if bpm <= 0:
                        raise ValueError("nonpositive tempo")
                    tempo_events.append({"position": pos, "bpm": bpm})
                    all_bpms.append(bpm)
                except ValueError:
                    warnings.append("Invalid tempo entry; value remains unknown.")
                    continue

        tempo_events.sort(key=lambda event: event["position"])
        initial_bpm = tempo_events[0]["bpm"] if tempo_events else None
        if not tempo_events:
            warnings.append("No valid tempo entries.")
        change_count = max(0, len(tempo_events) - 1)
        tempo_summary = TempoSummary(
            initial_bpm=initial_bpm,
            minimum_bpm=min(all_bpms) if all_bpms else initial_bpm,
            maximum_bpm=max(all_bpms) if all_bpms else initial_bpm,
            change_count=change_count,
            has_changes=change_count > 0,
            time_unit="ticks",
            resolution=resolution,
            map=tuple(tempo_events),
        )

        tracks: list[TrackSummary] = []
        references: list[DependencyReference] = []
        total_notes = 0

        # Parse vocal tracks: vsTrack
        vs_tracks = _find_children(root, "vsTrack")
        if len(vs_tracks) > self.limits.max_tracks:
            return self._failed(
                detection,
                f"track count exceeds limit of {self.limits.max_tracks}",
            )

        for track_index, vt in enumerate(vs_tracks):
            t_no_str = _find_text(vt, "tNo", str(track_index))
            try:
                t_no = int(t_no_str)
            except ValueError:
                t_no = track_index

            track_name = _find_text(vt, "name", f"Track {t_no + 1}")
            part_voices: list[tuple[str, str]] = []
            lyric_texts: list[str] = []
            track_note_count = 0
            track_lyric_count = 0

            pitch_detected = False
            vibrato_detected = False
            dynamics_detected = False

            pitch_count = 0
            vibrato_count = 0
            dynamics_count = 0

            for part in _find_children(vt, "vsPart"):
                for singer in _find_children(part, "singer"):
                    bs = _find_text(singer, "bs")
                    pc = _find_text(singer, "pc")
                    if (bs, pc) in voices and voices[(bs, pc)] not in part_voices:
                        part_voices.append(voices[(bs, pc)])

                # Check part-level style
                pstyle = _find_child(part, "pStyle")
                if pstyle is not None:
                    for v in _find_children(pstyle, "v"):
                        vid = v.attrib.get("id")
                        vtext = (v.text or "").strip()
                        if (
                            vid == "bendDep"
                            and vtext not in ("", "0", "8")
                            or vid in ("risePort", "fallPort")
                            and vtext not in ("", "0")
                        ):
                            pitch_detected = True

                notes = _find_children(part, "note")
                total_notes += len(notes)
                if total_notes > self.limits.max_notes:
                    return self._failed(
                        detection,
                        f"note count exceeds limit of {self.limits.max_notes}",
                    )

                for note in notes:
                    track_note_count += 1
                    lyric = _find_text(note, "y")
                    if lyric:
                        track_lyric_count += 1
                        lyric_texts.append(lyric)

                    nstyle = _find_child(note, "nStyle")
                    if nstyle is not None:
                        for v in _find_children(nstyle, "v"):
                            vid = v.attrib.get("id")
                            vval = (v.text or "").strip()
                            if vid in (
                                "bendDep",
                                "bendLen",
                                "risePort",
                                "fallPort",
                            ) and vval not in ("", "0"):
                                pitch_detected = True
                                pitch_count += 1
                            elif vid in ("vibLen", "vibType") and vval not in ("", "0"):
                                vibrato_detected = True
                                vibrato_count += 1
                            elif vid in ("accent", "decay") and vval not in ("", "50"):
                                dynamics_detected = True
                                dynamics_count += 1

                # Check controller / vibrato sequences
                for child in part:
                    c_tag = _strip_ns(child.tag)
                    if c_tag == "seq":
                        sid = child.attrib.get("id", "")
                        if "vib" in sid.lower():
                            vibrato_detected = True
                            vibrato_count += len(_find_children(child, "cc"))
                    elif c_tag == "cc":
                        value = _find_child(child, "v")
                        if value is not None and (value.text or "").strip():
                            signal = CONTROLLERS.get(value.attrib.get("id", ""))
                            if signal == "pitch":
                                pitch_detected = True
                                pitch_count += 1
                            elif signal == "dynamics":
                                dynamics_detected = True
                                dynamics_count += 1
                    elif c_tag in ("bre", "bri", "cle", "gen", "ope", "vol"):
                        dynamics_detected = True
                        dynamics_count += 1

            primary_voice_name = part_voices[0][0] if part_voices else None
            primary_voice_id = part_voices[0][1] if part_voices else None

            sample_text = " ".join([primary_voice_name or ""] + lyric_texts[:50])
            languages = _detect_languages(sample_text)

            tracks.append(
                TrackSummary(
                    index=t_no,
                    name=track_name,
                    kind=TrackKind.VOCAL,
                    voice_name=primary_voice_name,
                    voice_identifier=primary_voice_id,
                    languages=languages,
                    note_count=track_note_count,
                    lyric_count=track_lyric_count,
                    pitch=SignalState.DETECTED
                    if pitch_detected
                    else SignalState.NONE_DETECTED,
                    vibrato=SignalState.DETECTED
                    if vibrato_detected
                    else SignalState.NONE_DETECTED,
                    dynamics=SignalState.DETECTED
                    if dynamics_detected
                    else SignalState.NONE_DETECTED,
                    details={
                        "voices": [{"name": n, "id": i} for n, i in part_voices],
                        "signal_counts": {
                            "pitch": pitch_count,
                            "vibrato": vibrato_count,
                            "dynamics": dynamics_count,
                        },
                    },
                )
            )

        # Parse audio tracks: monoTrack, stTrack
        for container_name in ("monoTrack", "stTrack"):
            for at in _find_children(root, container_name):
                at_name = _find_text(at, "name", "Audio Track")
                wav_parts = (
                    at.findall(".//{*}wavPart")
                    if "}" in root.tag
                    else at.findall(".//wavPart")
                )
                if not wav_parts:
                    # Fallback child search
                    wav_parts = [c for c in at.iter() if _strip_ns(c.tag) == "wavPart"]

                for wp in wav_parts:
                    fp = _find_text(wp, "filePath")
                    if fp:
                        references.append(
                            DependencyReference(original=fp, kind="audio")
                        )

                if wav_parts:
                    tracks.append(
                        TrackSummary(
                            index=len(tracks),
                            name=at_name,
                            kind=TrackKind.AUDIO,
                            note_count=0,
                            lyric_count=0,
                        )
                    )

        return ParseResult(
            parser_id=self.parser_id,
            parser_version=self.parser_version,
            status=ParseStatus.PARTIAL if warnings else ParseStatus.PARSED,
            detection=Detection(True, "vsqx", schema_version, detection.evidence),
            capabilities=(
                "tempo",
                "tracks",
                "voices",
                "lyrics",
                "tuning",
                "references",
            ),
            tempo=tempo_summary,
            tracks=tuple(tracks),
            references=tuple(references),
            warnings=tuple(warnings),
            details={
                "schema": root_name,
                "version": schema_version,
                "vocal_tracks_count": len(vs_tracks),
                "total_notes": total_notes,
            },
        )

    def _read_header(self, path: Path) -> bytes:
        limit = min(self.limits.max_header_bytes, self.limits.max_file_bytes)
        with path.open("rb") as stream:
            return stream.read(limit)

    def _failed(self, detection: Detection, warning: str) -> ParseResult:
        return ParseResult(
            parser_id=self.parser_id,
            parser_version=self.parser_version,
            status=ParseStatus.FAILED,
            detection=detection,
            warnings=(warning,),
        )


class _BoundedTree(ET.TreeBuilder):
    """Reject DTDs and enforce structural limits while XML is built."""

    def __init__(self, limits):
        super().__init__()
        self.limits = limits
        self.depth = self.notes = self.curves = 0

    def doctype(self, name, pubid, system):
        raise ParserError("XML DTDs and entities are not supported")

    def start(self, tag, attrs):
        self.depth += 1
        if self.depth > self.limits.max_json_depth:
            raise ParserLimitError("XML nesting exceeds limit")
        local = _strip_ns(tag)
        self.notes += local == "note"
        self.curves += local in ("cc", "mCtrl")
        if self.notes > self.limits.max_notes:
            raise ParserLimitError("note count exceeds limit")
        if self.curves > self.limits.max_curve_values:
            raise ParserLimitError("controller count exceeds limit")
        return super().start(tag, attrs)

    def end(self, tag):
        self.depth -= 1
        return super().end(tag)
