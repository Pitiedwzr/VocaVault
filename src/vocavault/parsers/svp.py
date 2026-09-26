"""Bounded, read-only metadata extraction for Synthesizer V Studio projects."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from itertools import pairwise
from pathlib import Path
from typing import Any, Final

from vocavault.models import (
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

_SUPPORTED_VERSIONS: Final = frozenset({"113", "134", "153"})
_BLICK_RESOLUTION: Final = 705_600_000
_KNOWN_ROOT_KEY = re.compile(
    rb'"(?:time|tracks|renderConfig|library)"\s*:', re.IGNORECASE
)
_VERSION_KEY = re.compile(rb'"version"\s*:\s*(?:"([^"]+)"|(-?\d+(?:\.\d+)?))')
_DYNAMIC_CURVES: Final = frozenset(
    {"loudness", "tension", "breathiness", "voicing", "gender", "toneShift"}
)


class SvpParser(ProjectParser):
    parser_id = "builtin.svp"
    parser_version = "0.1.0"

    def detect(self, path: str | Path) -> Detection:
        source = Path(path)
        if source.suffix.casefold() != ".svp":
            return Detection(False, evidence="filename extension is not .svp")
        try:
            header = self._read_header(source)
        except (OSError, ParserError) as exc:
            return Detection(False, format_name="svp", evidence=str(exc))

        candidate = header.lstrip()
        if not candidate.startswith(b"{") or not _KNOWN_ROOT_KEY.search(candidate):
            return Detection(
                False,
                format_name="svp",
                evidence=".svp extension without a recognized Synthesizer V JSON header",
            )

        match = _VERSION_KEY.search(candidate)
        version = (
            _normalise_version(match.group(1) or match.group(2)) if match else None
        )
        supported = version is None or version in _SUPPORTED_VERSIONS
        evidence = "JSON object with Synthesizer V root fields"
        if version is not None:
            evidence += f" and schema version {version}"
        return Detection(supported, "svp", version, evidence)

    def parse(self, path: str | Path) -> ParseResult:
        source = Path(path)
        initial_detection = self.detect(source)
        if source.suffix.casefold() != ".svp":
            return self._result(
                ParseStatus.UNSUPPORTED,
                initial_detection,
                warnings=(
                    initial_detection.evidence or "file is not recognized as SVP",
                ),
            )

        try:
            raw = self._read_stable(source)
            payload, trailing_nuls = self._prepare_payload(raw)
            text = payload.decode("utf-8", errors="strict")
            _validate_json_depth(text, self.limits.max_json_depth)
            root = json.loads(text)
        except (
            OSError,
            UnicodeError,
            ValueError,
            json.JSONDecodeError,
            ParserError,
            RecursionError,
        ) as exc:
            return self._result(
                ParseStatus.FAILED,
                initial_detection,
                warnings=(f"SVP parse failed: {exc}",),
            )

        if not isinstance(root, Mapping) or not _looks_like_svp(root):
            detection = Detection(
                False,
                "svp",
                evidence="JSON root does not contain recognized Synthesizer V fields",
            )
            return self._result(
                ParseStatus.UNSUPPORTED,
                detection,
                warnings=(detection.evidence or "unrecognized SVP structure",),
            )

        version = _normalise_version(root.get("version"))
        detection = Detection(
            version in _SUPPORTED_VERSIONS,
            "svp",
            version,
            f"Synthesizer V JSON schema version {version or 'unknown'}",
        )
        if version not in _SUPPORTED_VERSIONS:
            return self._result(
                ParseStatus.UNSUPPORTED,
                detection,
                warnings=(f"unsupported SVP schema version: {version or 'missing'}",),
                details={"trailing_nul_bytes": trailing_nuls},
            )

        warnings: list[str] = []
        partial = False
        if trailing_nuls:
            warnings.append(f"ignored {trailing_nuls} trailing NUL byte(s)")

        try:
            tempo, tempo_partial = self._extract_tempo(root, warnings)
            tracks, tracks_partial = self._extract_tracks(root, warnings)
            partial = tempo_partial or tracks_partial
        except ParserLimitError as exc:
            return self._result(
                ParseStatus.FAILED,
                detection,
                warnings=(*warnings, f"SVP resource limit exceeded: {exc}"),
                details={"trailing_nul_bytes": trailing_nuls},
            )

        return self._result(
            ParseStatus.PARTIAL if partial else ParseStatus.PARSED,
            detection,
            tempo=tempo,
            tracks=tracks,
            warnings=tuple(warnings),
            details={
                "schema_version": version,
                "source_size": len(raw),
                "trailing_nul_bytes": trailing_nuls,
            },
        )

    def _read_header(self, source: Path) -> bytes:
        before = source.stat()
        if before.st_size > self.limits.max_file_bytes:
            raise ParserLimitError(
                f"file size {before.st_size} bytes exceeds the "
                f"{self.limits.max_file_bytes}-byte limit"
            )
        with source.open("rb") as stream:
            header = stream.read(self.limits.max_header_bytes)
        after = source.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise SourceChangedError("source changed while its header was being read")
        return header

    def _prepare_payload(self, raw: bytes) -> tuple[bytes, int]:
        without_nuls = raw.rstrip(b"\x00")
        trailing_nuls = len(raw) - len(without_nuls)
        if trailing_nuls > self.limits.max_trailing_nul_bytes:
            raise ParserLimitError(
                f"trailing NUL padding ({trailing_nuls} bytes) exceeds the "
                f"{self.limits.max_trailing_nul_bytes}-byte limit"
            )
        return without_nuls, trailing_nuls

    def _extract_tempo(
        self, root: Mapping[str, Any], warnings: list[str]
    ) -> tuple[TempoSummary, bool]:
        time = root.get("time")
        if not isinstance(time, Mapping):
            warnings.append("missing or invalid time object")
            return TempoSummary(), True
        raw_tempo = time.get("tempo")
        if not _is_sequence(raw_tempo):
            warnings.append("missing or invalid tempo map")
            return TempoSummary(), True
        if len(raw_tempo) > self.limits.max_tempo_entries:
            raise ParserLimitError(
                f"tempo entry count {len(raw_tempo)} exceeds "
                f"{self.limits.max_tempo_entries}"
            )

        entries: list[dict[str, Any]] = []
        partial = False
        for index, item in enumerate(raw_tempo):
            if not isinstance(item, Mapping):
                warnings.append(f"ignored invalid tempo entry {index}")
                partial = True
                continue
            bpm = _finite_number(item.get("bpm"))
            position = _finite_number(item.get("position"))
            if bpm is None or bpm <= 0 or position is None:
                warnings.append(f"ignored invalid tempo entry {index}")
                partial = True
                continue
            entries.append(
                {
                    "position": int(position) if position.is_integer() else position,
                    "bpm": bpm,
                }
            )

        if not entries:
            warnings.append("no valid tempo entries")
            return TempoSummary(time_unit="blick", resolution=_BLICK_RESOLUTION), True

        entries.sort(key=lambda item: item["position"])
        bpms = [entry["bpm"] for entry in entries]
        initial_candidates = [entry for entry in entries if entry["position"] <= 0]
        initial = (initial_candidates[-1] if initial_candidates else entries[0])["bpm"]
        changes = sum(
            not math.isclose(previous, current, rel_tol=0.0, abs_tol=1e-12)
            for previous, current in pairwise(bpms)
        )
        return (
            TempoSummary(
                initial_bpm=initial,
                minimum_bpm=min(bpms),
                maximum_bpm=max(bpms),
                change_count=changes,
                has_changes=bool(changes),
                time_unit="blick",
                resolution=_BLICK_RESOLUTION,
                map=tuple(entries),
            ),
            partial,
        )

    def _extract_tracks(
        self, root: Mapping[str, Any], warnings: list[str]
    ) -> tuple[tuple[TrackSummary, ...], bool]:
        raw_tracks = root.get("tracks")
        if not _is_sequence(raw_tracks):
            warnings.append("missing or invalid tracks array")
            return (), True
        if len(raw_tracks) > self.limits.max_tracks:
            raise ParserLimitError(
                f"track count {len(raw_tracks)} exceeds {self.limits.max_tracks}"
            )

        raw_library = root.get("library", ())
        if raw_library is None:
            raw_library = ()
        if not _is_sequence(raw_library):
            warnings.append(
                "invalid group library; referenced parts cannot be resolved"
            )
            raw_library = ()
        if len(raw_library) > self.limits.max_groups:
            raise ParserLimitError(
                f"group count {len(raw_library)} exceeds {self.limits.max_groups}"
            )
        library = {
            item.get("uuid"): item
            for item in raw_library
            if isinstance(item, Mapping) and isinstance(item.get("uuid"), str)
        }

        summaries: list[TrackSummary] = []
        partial = False
        totals = {"notes": 0, "curve_values": 0}
        for index, track in enumerate(raw_tracks):
            if not isinstance(track, Mapping):
                warnings.append(f"ignored invalid track {index}")
                partial = True
                continue
            summary, track_partial = self._extract_track(
                index, track, library, warnings, totals
            )
            summaries.append(summary)
            partial = partial or track_partial
        return tuple(summaries), partial

    def _extract_track(
        self,
        index: int,
        track: Mapping[str, Any],
        library: Mapping[str, Mapping[str, Any]],
        warnings: list[str],
        totals: dict[str, int],
    ) -> tuple[TrackSummary, bool]:
        name = _nonempty_text(track.get("name"))
        main_group = track.get("mainGroup")
        main_ref = track.get("mainRef")
        parts: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
        partial = False
        missing_groups: list[str] = []
        if isinstance(main_group, Mapping):
            parts.append(
                (main_group, main_ref if isinstance(main_ref, Mapping) else {})
            )
        else:
            warnings.append(f"track {index} has no valid main group")
            partial = True

        extra_refs = track.get("groups", ())
        if not _is_sequence(extra_refs):
            warnings.append(f"track {index} has an invalid group-reference array")
            extra_refs = ()
            partial = True
        if len(extra_refs) > self.limits.max_groups:
            raise ParserLimitError(
                f"track {index} group count {len(extra_refs)} exceeds "
                f"{self.limits.max_groups}"
            )
        for ref in extra_refs:
            if not isinstance(ref, Mapping):
                partial = True
                continue
            group_id = ref.get("groupID")
            group = library.get(group_id) if isinstance(group_id, str) else None
            if group is None:
                missing_groups.append(str(group_id or "<missing>"))
                partial = True
                continue
            parts.append((group, ref))
        if missing_groups:
            warnings.append(
                f"track {index} references missing group(s): {', '.join(missing_groups)}"
            )

        note_count = 0
        lyric_count = 0
        languages: list[str] = []
        seen_languages: set[str] = set()
        voices: list[dict[str, Any]] = []
        pitch_known = vibrato_known = dynamics_known = False
        pitch_count = vibrato_count = dynamics_count = 0
        curve_points = {"pitch": 0, "vibrato": 0, "dynamics": 0}

        for group, ref in parts:
            notes = group.get("notes", ())
            if not _is_sequence(notes):
                notes = ()
                warnings.append(f"track {index} contains a group with invalid notes")
                partial = True
            totals["notes"] += len(notes)
            if totals["notes"] > self.limits.max_notes:
                raise ParserLimitError(f"note count exceeds {self.limits.max_notes}")

            is_instrumental = ref.get("isInstrumental") is True
            if not is_instrumental:
                note_count += len(notes)
                lyric_count += sum(
                    bool(_nonempty_text(note.get("lyrics")))
                    for note in notes
                    if isinstance(note, Mapping)
                )

            database = ref.get("database") if isinstance(ref, Mapping) else None
            if not is_instrumental and isinstance(database, Mapping):
                voice = _voice_details(database)
                if voice and voice not in voices:
                    voices.append(voice)
                for key in ("language", "languageOverride"):
                    language = _nonempty_text(database.get(key))
                    if language and language.casefold() not in seen_languages:
                        seen_languages.add(language.casefold())
                        languages.append(language)

            parameters = group.get("parameters")
            if isinstance(parameters, Mapping):
                for curve_name, curve in parameters.items():
                    point_count, nonzero_count, value_count = _curve_counts(curve)
                    totals["curve_values"] += value_count
                    if totals["curve_values"] > self.limits.max_curve_values:
                        raise ParserLimitError(
                            f"curve value count exceeds {self.limits.max_curve_values}"
                        )
                    if is_instrumental:
                        continue
                    if curve_name == "pitchDelta":
                        pitch_known = True
                        pitch_count += nonzero_count
                        curve_points["pitch"] += point_count
                    elif curve_name == "vibratoEnv":
                        vibrato_known = True
                        vibrato_count += nonzero_count
                        curve_points["vibrato"] += point_count
                    elif curve_name in _DYNAMIC_CURVES:
                        dynamics_known = True
                        dynamics_count += nonzero_count
                        curve_points["dynamics"] += point_count

            vocal_modes = group.get("vocalModes")
            if isinstance(vocal_modes, Mapping):
                if not is_instrumental:
                    dynamics_known = True
                for curve in vocal_modes.values():
                    point_count, nonzero_count, value_count = _curve_counts(curve)
                    totals["curve_values"] += value_count
                    if totals["curve_values"] > self.limits.max_curve_values:
                        raise ParserLimitError(
                            f"curve value count exceeds {self.limits.max_curve_values}"
                        )
                    if not is_instrumental:
                        dynamics_count += nonzero_count
                        curve_points["dynamics"] += point_count

            system_pitch = (
                ref.get("systemPitchDelta") if isinstance(ref, Mapping) else None
            )
            if system_pitch is not None:
                point_count, nonzero_count, value_count = _curve_counts(system_pitch)
                totals["curve_values"] += value_count
                if totals["curve_values"] > self.limits.max_curve_values:
                    raise ParserLimitError(
                        f"curve value count exceeds {self.limits.max_curve_values}"
                    )
                if not is_instrumental:
                    pitch_known = True
                    pitch_count += nonzero_count
                    curve_points["pitch"] += point_count

            for note in notes if not is_instrumental else ():
                if not isinstance(note, Mapping):
                    continue
                for field_name in ("attributes", "systemAttributes"):
                    attributes = note.get(field_name)
                    if not isinstance(attributes, Mapping):
                        continue
                    pitch_keys = ("tF0Offset", "dF0Left", "dF0Right")
                    vibrato_keys = ("dF0Vbr",)
                    pitch_known = pitch_known or any(
                        key in attributes for key in pitch_keys
                    )
                    vibrato_known = vibrato_known or any(
                        key in attributes for key in vibrato_keys
                    )
                    pitch_count += _nonzero_attributes(attributes, pitch_keys)
                    vibrato_count += _nonzero_attributes(attributes, vibrato_keys)

        main_ref_map = main_ref if isinstance(main_ref, Mapping) else {}
        instrumental = main_ref_map.get("isInstrumental")
        if instrumental is True:
            kind = TrackKind.AUDIO
        elif instrumental is False or note_count:
            kind = TrackKind.VOCAL
        else:
            kind = TrackKind.UNKNOWN

        primary_database = main_ref_map.get("database")
        primary_voice = (
            _voice_details(primary_database)
            if instrumental is not True and isinstance(primary_database, Mapping)
            else {}
        )
        details: dict[str, Any] = {
            "part_count": len(parts),
            "voices": voices,
            "lyric_coverage": (lyric_count / note_count) if note_count else None,
            "signal_counts": {
                "pitch": pitch_count,
                "vibrato": vibrato_count,
                "dynamics": dynamics_count,
            },
            "signal_control_points": curve_points,
        }
        if missing_groups:
            details["missing_group_ids"] = missing_groups
        return (
            TrackSummary(
                index=index,
                name=name,
                kind=kind,
                voice_name=primary_voice.get("name"),
                voice_identifier=primary_voice.get("identifier"),
                languages=tuple(languages),
                note_count=note_count,
                lyric_count=lyric_count,
                pitch=_signal_state(pitch_known, pitch_count),
                vibrato=_signal_state(vibrato_known, vibrato_count),
                dynamics=_signal_state(dynamics_known, dynamics_count),
                details=details,
            ),
            partial,
        )

    def _result(
        self,
        status: ParseStatus,
        detection: Detection,
        *,
        tempo: TempoSummary | None = None,
        tracks: tuple[TrackSummary, ...] = (),
        warnings: tuple[str, ...] = (),
        details: dict[str, Any] | None = None,
    ) -> ParseResult:
        return ParseResult(
            parser_id=self.parser_id,
            parser_version=self.parser_version,
            status=status,
            detection=detection,
            capabilities=(
                "tempo",
                "tracks",
                "voices",
                "languages",
                "lyrics",
                "tuning_signals",
            ),
            tempo=tempo or TempoSummary(),
            tracks=tracks,
            warnings=warnings,
            details=details or {},
        )


def _looks_like_svp(root: Mapping[str, Any]) -> bool:
    return bool({"time", "tracks", "renderConfig", "library"}.intersection(root))


def _normalise_version(value: object) -> str | None:
    if isinstance(value, bytes):
        try:
            value = value.decode("ascii")
        except UnicodeDecodeError:
            return None
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (int, float, str)):
        text = str(value).strip()
        return text or None
    return None


def _validate_json_depth(text: str, maximum: int) -> None:
    depth = 0
    in_string = False
    escaped = False
    for character in text:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            if depth > maximum:
                raise ParserLimitError(f"JSON nesting exceeds {maximum} levels")
        elif character in "]}":
            depth -= 1


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _is_sequence(value: object) -> bool:
    return isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    )


def _nonempty_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _voice_details(database: Mapping[str, Any]) -> dict[str, Any]:
    details: dict[str, Any] = {}
    name = _nonempty_text(database.get("name"))
    if name:
        details["name"] = name
    identifier = next(
        (
            _nonempty_text(database.get(key))
            for key in ("id", "uuid", "identifier")
            if _nonempty_text(database.get(key))
        ),
        None,
    )
    if identifier:
        details["identifier"] = identifier
    for key in (
        "language",
        "languageOverride",
        "phoneset",
        "phonesetOverride",
        "backendType",
        "version",
    ):
        value = _nonempty_text(database.get(key))
        if value:
            details[key] = value
    return details


def _curve_counts(curve: object) -> tuple[int, int, int]:
    if not isinstance(curve, Mapping):
        return 0, 0, 0
    points = curve.get("points")
    if not _is_sequence(points):
        return 0, 0, 0
    if not points:
        return 0, 0, 0

    if any(isinstance(point, Mapping) or _is_sequence(point) for point in points):
        values: list[object] = []
        value_count = 0
        for point in points:
            if isinstance(point, Mapping):
                values.append(point.get("value", point.get("y")))
                value_count += len(point)
            elif _is_sequence(point) and len(point) > 1:
                values.append(point[1])
                value_count += len(point)
            else:
                value_count += 1
        return len(points), sum(_is_nonzero(value) for value in values), value_count

    values = points[1::2]
    return len(values), sum(_is_nonzero(value) for value in values), len(points)


def _is_nonzero(value: object) -> bool:
    number = _finite_number(value)
    return number is not None and not math.isclose(
        number, 0.0, rel_tol=0.0, abs_tol=1e-12
    )


def _nonzero_attributes(attributes: Mapping[str, Any], keys: Iterable[str]) -> int:
    return sum(_is_nonzero(attributes.get(key)) for key in keys)


def _signal_state(known: bool, evidence_count: int) -> SignalState:
    if evidence_count:
        return SignalState.DETECTED
    return SignalState.NONE_DETECTED if known else SignalState.UNKNOWN
