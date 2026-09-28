"""VocaDB API client and candidate data structures for optional song enrichment."""

from __future__ import annotations

import json
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Final

VOCADB_BASE_URL: Final = "https://vocadb.net/api"
DEFAULT_USER_AGENT: Final = "VocaVault/0.2.0 (+https://github.com/Pitiedwzr/VocaVault)"
DEFAULT_TIMEOUT: Final = 8.0


class VocaDbError(Exception):
    """Base exception for VocaDB API errors."""


class VocaDbNetworkError(VocaDbError):
    """Network connection, timeout, or DNS failure."""


class VocaDbRateLimitError(VocaDbError):
    """HTTP 429 Too Many Requests."""


class VocaDbNotFoundError(VocaDbError):
    """Song ID not found on VocaDB."""


@dataclass(frozen=True, slots=True)
class VocaDbCandidate:
    id: int
    name: str
    artist_string: str
    song_type: str = "Original"
    publish_date: str | None = None
    names: tuple[dict[str, str], ...] = ()
    artists: tuple[dict[str, Any], ...] = ()
    links: tuple[dict[str, str], ...] = ()
    raw_data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api_dict(cls, data: dict[str, Any]) -> VocaDbCandidate:
        song_id = int(data.get("id", 0))
        name = str(data.get("name", "")).strip()
        artist_string = str(data.get("artistString", "")).strip()
        song_type = str(data.get("songType", "Original"))
        publish_date = data.get("publishDate")

        names_list: list[dict[str, str]] = []
        for n in data.get("names", []):
            if isinstance(n, dict) and n.get("value"):
                names_list.append({
                    "value": str(n["value"]).strip(),
                    "language": str(n.get("language", "Unspecified")),
                })

        artists_list: list[dict[str, Any]] = []
        for a in data.get("artists", []):
            if isinstance(a, dict):
                art_info = a.get("artist") or {}
                art_name = a.get("name") or art_info.get("name") or ""
                roles = str(a.get("roles", ""))
                categories = str(a.get("categories", ""))
                if art_name:
                    artists_list.append({
                        "name": str(art_name).strip(),
                        "roles": roles,
                        "categories": categories,
                        "is_support": bool(a.get("isSupport", False)),
                    })

        links_list: list[dict[str, str]] = []
        for pv in data.get("pvs", []):
            if isinstance(pv, dict) and pv.get("url"):
                links_list.append({
                    "kind": str(pv.get("service", "media")).lower(),
                    "url": str(pv["url"]).strip(),
                    "label": str(pv.get("name") or pv.get("service") or "PV"),
                })
        for wl in data.get("webLinks", []):
            if isinstance(wl, dict) and wl.get("url"):
                links_list.append({
                    "kind": "web",
                    "url": str(wl["url"]).strip(),
                    "label": str(wl.get("description") or "Web Link"),
                })

        return cls(
            id=song_id,
            name=name,
            artist_string=artist_string,
            song_type=song_type,
            publish_date=publish_date,
            names=tuple(names_list),
            artists=tuple(artists_list),
            links=tuple(links_list),
            raw_data=data,
        )


class VocaDbClient:
    """Client for querying VocaDB API with local SQLite caching and offline safety."""

    def __init__(
        self,
        *,
        base_url: str = VOCADB_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        user_agent: str = DEFAULT_USER_AGENT,
        cache_connection: sqlite3.Connection | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.user_agent = user_agent
        self.cache_connection = cache_connection

    def search_songs(self, query: str, *, max_results: int = 10) -> list[VocaDbCandidate]:
        clean_query = query.strip()
        if not clean_query:
            return []

        cache_key = f"search:{clean_query}:{max_results}"
        params = {
            "query": clean_query,
            "nameMatchMode": "Auto",
            "fields": "Names,Artists,PVs,WebLinks",
            "maxResults": str(max_results),
            "preferAccurateMatches": "true",
        }
        url = f"{self.base_url}/songs?{urllib.parse.urlencode(params)}"

        cached = self._read_cache(cache_key)
        if cached is not None:
            items = cached.get("items", [])
            return [VocaDbCandidate.from_api_dict(item) for item in items]

        try:
            payload = self._get_json(url)
            self._write_cache(cache_key, "songs", clean_query, payload)
            items = payload.get("items", [])
            return [VocaDbCandidate.from_api_dict(item) for item in items]
        except VocaDbNetworkError:
            if cached is not None:
                items = cached.get("items", [])
                return [VocaDbCandidate.from_api_dict(item) for item in items]
            raise

    def get_song(self, song_id: int) -> VocaDbCandidate | None:
        cache_key = f"song:{song_id}"
        url = f"{self.base_url}/songs/{song_id}?fields=Names,Artists,PVs,WebLinks"

        cached = self._read_cache(cache_key)
        if cached is not None:
            return VocaDbCandidate.from_api_dict(cached)

        try:
            payload = self._get_json(url)
            self._write_cache(cache_key, f"songs/{song_id}", str(song_id), payload)
            return VocaDbCandidate.from_api_dict(payload)
        except VocaDbNotFoundError:
            return None
        except VocaDbNetworkError:
            if cached is not None:
                return VocaDbCandidate.from_api_dict(cached)
            raise

    def _get_json(self, url: str) -> dict[str, Any]:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = resp.read()
                return json.loads(data.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise VocaDbNotFoundError(f"VocaDB resource not found: {url}") from exc
            if exc.code == 429:
                raise VocaDbRateLimitError("VocaDB rate limit reached. Please wait a moment.") from exc
            raise VocaDbError(f"VocaDB HTTP error {exc.code}: {exc.reason}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise VocaDbNetworkError(f"Could not connect to VocaDB: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise VocaDbError(f"Invalid JSON received from VocaDB: {exc}") from exc

    def _read_cache(self, cache_key: str) -> dict[str, Any] | None:
        if self.cache_connection is None:
            return None
        try:
            row = self.cache_connection.execute(
                "SELECT response_json FROM api_cache WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
            if row is not None:
                return json.loads(row[0])
        except (sqlite3.Error, json.JSONDecodeError):
            pass
        return None

    def _write_cache(
        self, cache_key: str, endpoint: str, query_or_id: str, payload: dict[str, Any]
    ) -> None:
        if self.cache_connection is None:
            return
        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            self.cache_connection.execute(
                """
                INSERT OR REPLACE INTO api_cache(cache_key, endpoint, query_or_id, response_json, cached_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (cache_key, endpoint, query_or_id, json.dumps(payload), now_iso),
            )
            self.cache_connection.commit()
        except sqlite3.Error:
            pass
