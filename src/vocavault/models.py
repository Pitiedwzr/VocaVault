"""Shared, UI-independent data contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class ParseStatus(StrEnum):
    NOT_PARSED = "not_parsed"
    PARSED = "parsed"
    PARTIAL = "partial"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"
    STALE = "stale"


class ManagementMode(StrEnum):
    INDEXED = "indexed"
    MANAGED = "managed"


class TrackKind(StrEnum):
    VOCAL = "vocal"
    AUDIO = "audio"
    UNKNOWN = "unknown"


class SignalState(StrEnum):
    UNKNOWN = "unknown"
    NONE_DETECTED = "none_detected"
    DETECTED = "detected"


@dataclass(frozen=True, slots=True)
class Detection:
    supported: bool
    format_name: str | None = None
    format_version: str | None = None
    evidence: str | None = None


@dataclass(frozen=True, slots=True)
class TempoSummary:
    initial_bpm: float | None = None
    minimum_bpm: float | None = None
    maximum_bpm: float | None = None
    change_count: int = 0
    has_changes: bool = False
    time_unit: str | None = None
    resolution: int | None = None
    map: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class TrackSummary:
    index: int
    name: str | None
    kind: TrackKind
    voice_name: str | None = None
    voice_identifier: str | None = None
    languages: tuple[str, ...] = ()
    note_count: int = 0
    lyric_count: int = 0
    pitch: SignalState = SignalState.UNKNOWN
    vibrato: SignalState = SignalState.UNKNOWN
    dynamics: SignalState = SignalState.UNKNOWN
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DependencyReference:
    original: str
    kind: str
    resolution_base: str | None = None


@dataclass(frozen=True, slots=True)
class ParseResult:
    parser_id: str
    parser_version: str
    status: ParseStatus
    detection: Detection
    capabilities: tuple[str, ...] = ()
    tempo: TempoSummary = field(default_factory=TempoSummary)
    tracks: tuple[TrackSummary, ...] = ()
    references: tuple[DependencyReference, ...] = ()
    warnings: tuple[str, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class IndexedFile:
    id: str
    project_id: str
    version_id: str
    path: Path
    role: str
    format_name: str | None
    format_version: str | None
    size: int
    modified_ns: int
    sha256: str
    parse_status: ParseStatus
    health: str


@dataclass(frozen=True, slots=True)
class ImportOutcome:
    file: IndexedFile
    created: bool
    warnings: tuple[str, ...] = ()
