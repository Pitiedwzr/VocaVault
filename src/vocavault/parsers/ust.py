"""Bounded, read-only metadata extraction for UTAU project files (.ust)."""

from __future__ import annotations

import math
import re
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

_HEADER_MAGIC = re.compile(rb"\[#(?:VERSION|SETTING|\d+)\]", re.IGNORECASE)
_NOTE_SEC_RE = re.compile(r"^\d+$")
_BLICK_RESOLUTION: Final = 480

_HIRAGANA_KATAKANA = re.compile(r"[\u3040-\u30ff]")
_CJK_UNIFIED = re.compile(r"[\u4e00-\u9fff]")


def _decode_ust(raw: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "utf-8", "cp932", "gb18030"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return raw.decode("latin1", errors="replace"), "latin1"


def _detect_languages(text_sample: str) -> tuple[str, ...]:
    languages: list[str] = []
    if _HIRAGANA_KATAKANA.search(text_sample):
        languages.append("ja")
    elif _CJK_UNIFIED.search(text_sample):
        languages.append("zh")
    return tuple(languages)


def _clean_voice_name(voice_dir: str) -> str:
    cleaned = voice_dir.strip()
    if cleaned.upper().startswith("%VOICE%"):
        cleaned = cleaned[7:].strip("/\\")
    return cleaned.replace("\\", "/").split("/")[-1] if cleaned else ""


class UstParser(ProjectParser):
    parser_id = "builtin.ust"
    parser_version = "0.2.1"

    def detect(self, path: str | Path) -> Detection:
        source = Path(path)
        if source.suffix.casefold() != ".ust":
            return Detection(False, evidence="filename extension is not .ust")
        try:
            header = self._read_header(source)
        except (OSError, ParserError) as exc:
            return Detection(False, format_name="ust", evidence=str(exc))

        if not _HEADER_MAGIC.search(header):
            return Detection(
                False,
                format_name="ust",
                evidence=".ust extension without recognized UTAU section headers",
            )

        text, _ = _decode_ust(header)
        version_match = re.search(
            r"(?:UstVersion|UST Version)\s*=?\s*([0-9.]+)", text, re.IGNORECASE
        )
        version = version_match.group(1) if version_match else None
        evidence = "UTAU project file with section headers"
        if version:
            evidence += f" and version {version}"
        return Detection(True, "ust", version, evidence)

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
                    or "file is not recognized as a supported UST project",
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

        text, encoding = _decode_ust(raw_data)
        lines = text.splitlines()

        sections: dict[str, dict[str, str]] = {}
        section_order: list[str] = []
        current_sec: str | None = None
        note_count = 0

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("[#") and stripped.endswith("]"):
                current_sec = stripped[2:-1]
                sections[current_sec] = {}
                section_order.append(current_sec)
                if _NOTE_SEC_RE.match(current_sec):
                    note_count += 1
                    if note_count > self.limits.max_notes:
                        return self._failed(
                            detection,
                            f"note count exceeds limit of {self.limits.max_notes}",
                        )
            elif current_sec is not None and "=" in stripped:
                key, _, value = stripped.partition("=")
                sections[current_sec][key.strip()] = value.strip()

        settings = sections.get("SETTING", {})
        version = (
            sections.get("VERSION", {}).get("UST Version")
            or settings.get("UstVersion")
            or detection.format_version
        )

        project_name = (
            settings.get("ProjectName") or settings.get("Project") or source.stem
        )
        voice_dir = settings.get("VoiceDir", "")
        out_file = settings.get("OutFile", "")
        cache_dir = settings.get("CacheDir", "")

        warnings: list[str] = []
        try:
            initial_bpm = float(settings.get("Tempo", ""))
            if not math.isfinite(initial_bpm) or initial_bpm <= 0:
                raise ValueError("invalid tempo")
        except ValueError:
            initial_bpm = None
            warnings.append("Initial tempo is missing or invalid.")

        tempo_events: list[dict[str, Any]] = (
            [{"position": 0, "bpm": initial_bpm}] if initial_bpm else []
        )
        current_position = 0
        all_bpms: list[float] = [initial_bpm] if initial_bpm else []

        note_keys = [k for k in section_order if _NOTE_SEC_RE.match(k)]
        vocal_notes = 0
        lyric_count = 0
        lyric_texts: list[str] = []

        pitch_detected = False
        vibrato_detected = False
        dynamics_detected = False

        pitch_count = 0
        vibrato_count = 0
        dynamics_count = 0

        for sec_name in note_keys:
            note = sections[sec_name]
            length_str = note.get("Length", "480")
            try:
                length = int(length_str)
            except ValueError:
                length = 480

            note_tempo_str = note.get("Tempo")
            if note_tempo_str:
                try:
                    note_bpm = float(note_tempo_str)
                    if not math.isfinite(note_bpm) or note_bpm <= 0:
                        raise ValueError("invalid tempo")
                    if len(tempo_events) >= self.limits.max_tempo_entries:
                        return self._failed(
                            detection, "tempo entries count exceeds limit"
                        )
                    tempo_events.append({"position": current_position, "bpm": note_bpm})
                    all_bpms.append(note_bpm)
                except ValueError:
                    pass

            lyric = note.get("Lyric", "").strip()
            is_rest = lyric.casefold() == "r"
            if not is_rest:
                vocal_notes += 1
                lyric_count += bool(lyric)
                lyric_texts.append(lyric)

            has_pitch = False
            pby = note.get("PBY", "")
            if (
                pby
                and any(val not in ("", "0", "0.0") for val in pby.split(","))
                or any(note.get(key) for key in ("Piches", "Pitches", "PitchBend"))
                or note.get("PBS")
                and ";" in note.get("PBS", "")
            ):
                has_pitch = True
            if has_pitch:
                pitch_detected = True
                pitch_count += 1

            vbr = note.get("VBR", "")
            if vbr:
                vbr_parts = vbr.split(",")
                try:
                    if float(vbr_parts[0]) > 0 and (
                        len(vbr_parts) > 2 and float(vbr_parts[2]) > 0
                    ):
                        vibrato_detected = True
                        vibrato_count += 1
                except (ValueError, IndexError):
                    vibrato_detected = True
                    vibrato_count += 1

            env = note.get("Envelope", "")
            if env or note.get("Intensity") and note.get("Intensity") != "100":
                dynamics_detected = True
                dynamics_count += 1

            current_position += length

        tempo_change_count = max(0, len(tempo_events) - 1)
        tempo_summary = TempoSummary(
            initial_bpm=initial_bpm,
            minimum_bpm=min(all_bpms) if all_bpms else initial_bpm,
            maximum_bpm=max(all_bpms) if all_bpms else initial_bpm,
            change_count=tempo_change_count,
            has_changes=tempo_change_count > 0,
            time_unit="ticks",
            resolution=_BLICK_RESOLUTION,
            map=tuple(tempo_events),
        )

        references: list[DependencyReference] = []
        if voice_dir:
            references.append(DependencyReference(original=voice_dir, kind="voicebank"))
        if out_file:
            references.append(DependencyReference(original=out_file, kind="audio"))

        clean_voice = _clean_voice_name(voice_dir)
        text_corpus = " ".join([clean_voice] + lyric_texts[:100])
        languages = _detect_languages(text_corpus)

        track_summary = TrackSummary(
            index=0,
            name=project_name,
            kind=TrackKind.VOCAL,
            voice_name=clean_voice or None,
            voice_identifier=voice_dir or None,
            languages=languages,
            note_count=vocal_notes,
            lyric_count=lyric_count,
            pitch=SignalState.DETECTED if pitch_detected else SignalState.NONE_DETECTED,
            vibrato=SignalState.DETECTED
            if vibrato_detected
            else SignalState.NONE_DETECTED,
            dynamics=SignalState.DETECTED
            if dynamics_detected
            else SignalState.NONE_DETECTED,
            details={
                "encoding": encoding,
                "total_sections": len(note_keys),
                "rest_notes": len(note_keys) - vocal_notes,
                "signal_counts": {
                    "pitch": pitch_count,
                    "vibrato": vibrato_count,
                    "dynamics": dynamics_count,
                },
            },
        )

        return ParseResult(
            parser_id=self.parser_id,
            parser_version=self.parser_version,
            status=ParseStatus.PARTIAL if warnings else ParseStatus.PARSED,
            detection=Detection(True, "ust", version, detection.evidence),
            capabilities=(
                "tempo",
                "tracks",
                "voices",
                "lyrics",
                "tuning",
                "references",
            ),
            tempo=tempo_summary,
            tracks=(track_summary,),
            references=tuple(references),
            warnings=tuple(warnings),
            details={
                "encoding": encoding,
                "project_name": project_name,
                "voice_dir": voice_dir,
                "out_file": out_file,
                "cache_dir": cache_dir,
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
