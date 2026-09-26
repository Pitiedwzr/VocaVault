"""Common contracts and safe file-reading helpers for project parsers."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from vocavault.models import Detection, ParseResult


@dataclass(frozen=True, slots=True)
class ParserLimits:
    """Resource limits applied before and during parsing.

    The defaults are deliberately above the representative corpus while still
    preventing a project file from consuming unbounded memory or CPU time.
    """

    max_file_bytes: int = 64 * 1024 * 1024
    max_header_bytes: int = 64 * 1024
    max_json_depth: int = 128
    max_trailing_nul_bytes: int = 4096
    max_tempo_entries: int = 100_000
    max_tracks: int = 512
    max_groups: int = 10_000
    max_notes: int = 2_000_000
    max_curve_values: int = 5_000_000


DEFAULT_LIMITS: Final = ParserLimits()


class ParserError(Exception):
    """Base class for controlled parser failures."""


class ParserLimitError(ParserError):
    """Raised when an input exceeds a configured resource limit."""


class SourceChangedError(ParserError):
    """Raised when the path changes while its bytes are being read."""


class ProjectParser(ABC):
    """Read-only interface implemented by built-in project-file adapters."""

    parser_id: str
    parser_version: str

    def __init__(self, limits: ParserLimits | None = None) -> None:
        self.limits = limits or DEFAULT_LIMITS

    @abstractmethod
    def detect(self, path: str | Path) -> Detection:
        """Inspect a bounded header and return format/version evidence."""

    @abstractmethod
    def parse(self, path: str | Path) -> ParseResult:
        """Parse a file without modifying it or following external references."""

    def _read_stable(self, path: str | Path, *, byte_limit: int | None = None) -> bytes:
        """Read one stable snapshot and reject files larger than ``byte_limit``."""

        source = Path(path)
        limit = self.limits.max_file_bytes if byte_limit is None else byte_limit
        before = source.stat()
        if not source.is_file():
            raise OSError(f"not a regular file: {source}")
        if before.st_size > limit:
            raise ParserLimitError(
                f"file size {before.st_size} bytes exceeds the {limit}-byte limit"
            )

        with source.open("rb") as stream:
            opened_before = os.fstat(stream.fileno())
            data = stream.read(limit + 1)
            opened_after = os.fstat(stream.fileno())

        if len(data) > limit:
            raise ParserLimitError(f"file exceeds the {limit}-byte read limit")

        after = source.stat()
        snapshots = (before, opened_before, opened_after, after)
        identity = {
            (item.st_size, item.st_mtime_ns, getattr(item, "st_ino", None))
            for item in snapshots
        }
        if len(identity) != 1 or len(data) != after.st_size:
            raise SourceChangedError("source changed while it was being read")
        return data
