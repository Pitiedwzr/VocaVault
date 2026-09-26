"""Read-only file inspection and safe desktop integration."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


class SourceChangedError(OSError):
    """Raised when a file changes while it is being inspected."""


@dataclass(frozen=True, slots=True)
class FileFingerprint:
    size: int
    modified_ns: int
    sha256: str


def fingerprint(path: Path, *, chunk_size: int = 1024 * 1024) -> FileFingerprint:
    """Hash stable file bytes and fail instead of recording a mixed revision."""

    path = path.resolve(strict=True)
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise SourceChangedError(f"File changed while reading: {path}")
    return FileFingerprint(after.st_size, after.st_mtime_ns, digest.hexdigest())


def open_path(path: Path, application: Path | None = None) -> None:
    """Open a file using an explicit argument list, never a shell command string."""

    path = path.resolve(strict=True)
    if application is not None:
        subprocess.Popen(
            [str(application.resolve(strict=True)), str(path)], shell=False
        )
        return
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)], shell=False)
    else:
        subprocess.Popen(["xdg-open", str(path)], shell=False)


def reveal_path(path: Path) -> None:
    """Reveal a file in the native file manager."""

    path = path.resolve(strict=True)
    if sys.platform == "win32":
        subprocess.Popen(["explorer.exe", f"/select,{path}"], shell=False)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)], shell=False)
    else:
        subprocess.Popen(["xdg-open", str(path.parent)], shell=False)
