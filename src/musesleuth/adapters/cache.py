"""Scraper response cache backed by the SQLite scraper_cache table."""
from __future__ import annotations

import sqlite3
from typing import Optional


class ScraperCache:
    """Cache for adapter responses keyed by (adapter_name, cache_key)."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get(self, adapter_name: str, cache_key: str) -> Optional[str]:
        """Get a cached payload. Returns None if not found."""
        row = self._conn.execute(
            "SELECT payload FROM scraper_cache WHERE adapter_name = ? AND cache_key = ?",
            (adapter_name, cache_key),
        ).fetchone()
        return row["payload"] if row else None

    def put(self, adapter_name: str, cache_key: str, payload: str) -> None:
        """Store or update a cached payload."""
        self._conn.execute(
            """
            INSERT INTO scraper_cache (adapter_name, cache_key, payload)
            VALUES (?, ?, ?)
            ON CONFLICT(adapter_name, cache_key)
            DO UPDATE SET payload = excluded.payload, fetched_at = datetime('now')
            """,
            (adapter_name, cache_key, payload),
        )
        self._conn.commit()

    def has(self, adapter_name: str, cache_key: str) -> bool:
        """Check if a cache entry exists."""
        row = self._conn.execute(
            "SELECT 1 FROM scraper_cache WHERE adapter_name = ? AND cache_key = ?",
            (adapter_name, cache_key),
        ).fetchone()
        return row is not None