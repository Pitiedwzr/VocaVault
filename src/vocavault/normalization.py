"""Search normalization that never changes displayed metadata."""

from __future__ import annotations

import re
import unicodedata

_SPACE_RE = re.compile(r"\s+")
# Treat common separators consistently while preserving meaningful letters and symbols.
_SEPARATOR_RE = re.compile(r"[-‐‑‒–—―_・･·]+")


def normalize_search_text(value: str) -> str:
    """Return a stable comparison form using NFKC and Unicode case folding."""

    value = unicodedata.normalize("NFKC", value).casefold()
    value = _SEPARATOR_RE.sub(" ", value)
    return _SPACE_RE.sub(" ", value).strip()


def escape_like(value: str) -> str:
    """Escape a value used with a parameterized SQLite LIKE expression."""

    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
