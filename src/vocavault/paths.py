"""Application-owned path selection."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def application_data_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "VocaVault"


def default_database_path() -> Path:
    return application_data_dir() / "library.sqlite3"
