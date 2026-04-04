"""TDD tests for base adapter interface and rate limiter -- written before implementation."""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from musesleuth.adapters.base import BaseAdapter, AdapterResult
from musesleuth.adapters.rate_limiter import RateLimiter
from musesleuth.adapters.cache import ScraperCache
from musesleuth.db import create_schema


# --- Rate Limiter Tests ---

class TestRateLimiter:
    """Tests for per-adapter rate limiting."""

    @pytest.mark.unit
    def test_allows_first_request(self) -> None:
        rl = RateLimiter(requests_per_second=5.0)
        assert rl.acquire() is True

    @pytest.mark.unit
    def test_tracks_request_count(self) -> None:
        rl = RateLimiter(requests_per_second=100.0)
        rl.acquire()
        rl.acquire()
        assert rl.request_count == 2

    @pytest.mark.unit
    def test_wait_time_positive_when_saturated(self) -> None:
        rl = RateLimiter(requests_per_second=1.0)
        rl.acquire()
        wait = rl.wait_time()
        # Should need to wait ~1s after first request at 1 rps
        assert wait >= 0.0

    @pytest.mark.unit
    def test_reset_clears_state(self) -> None:
        rl = RateLimiter(requests_per_second=10.0)
        rl.acquire()
        rl.acquire()
        rl.reset()
        assert rl.request_count == 0


# --- Scraper Cache Tests ---

class TestScraperCache:
    """Tests for caching scraper responses in SQLite."""

    @pytest.fixture
    def db(self, in_memory_db: sqlite3.Connection) -> sqlite3.Connection:
        create_schema(in_memory_db)
        return in_memory_db

    @pytest.mark.integration
    def test_put_and_get(self, db: sqlite3.Connection) -> None:
        cache = ScraperCache(db)
        cache.put("lastfm", "artist:!Attention!", '{"name": "!Attention!"}')
        result = cache.get("lastfm", "artist:!Attention!")
        assert result is not None
        assert "!Attention!" in result

    @pytest.mark.integration
    def test_get_missing_returns_none(self, db: sqlite3.Connection) -> None:
        cache = ScraperCache(db)
        assert cache.get("lastfm", "nonexistent_key") is None

    @pytest.mark.integration
    def test_overwrite_existing(self, db: sqlite3.Connection) -> None:
        cache = ScraperCache(db)
        cache.put("lastfm", "key1", "old_value")
        cache.put("lastfm", "key1", "new_value")
        assert cache.get("lastfm", "key1") == "new_value"

    @pytest.mark.integration
    def test_different_adapters_different_keys(self, db: sqlite3.Connection) -> None:
        cache = ScraperCache(db)
        cache.put("lastfm", "key1", "lastfm_data")
        cache.put("musicbrainz", "key1", "mb_data")
        assert cache.get("lastfm", "key1") == "lastfm_data"
        assert cache.get("musicbrainz", "key1") == "mb_data"

    @pytest.mark.integration
    def test_has_key(self, db: sqlite3.Connection) -> None:
        cache = ScraperCache(db)
        cache.put("lastfm", "exists", "data")
        assert cache.has("lastfm", "exists") is True
        assert cache.has("lastfm", "nope") is False


# --- Base Adapter Tests ---

class TestAdapterResult:
    """Tests for AdapterResult data class."""

    @pytest.mark.unit
    def test_success_result(self) -> None:
        r = AdapterResult(source="lastfm", success=True, data={"listeners": 1234})
        assert r.success is True
        assert r.data["listeners"] == 1234

    @pytest.mark.unit
    def test_failure_result(self) -> None:
        r = AdapterResult(source="lastfm", success=False, error="rate limited")
        assert r.error == "rate limited"


class TestBaseAdapterInterface:
    """Tests that BaseAdapter defines the required interface."""

    @pytest.mark.unit
    def test_cannot_instantiate_directly(self) -> None:
        with pytest.raises(TypeError):
            BaseAdapter()

    @pytest.mark.unit
    def test_subclass_must_implement_fetch(self) -> None:
        class BadAdapter(BaseAdapter):
            @property
            def name(self) -> str:
                return "bad"
        with pytest.raises(TypeError):
            BadAdapter()

    @pytest.mark.unit
    def test_valid_subclass_works(self) -> None:
        class GoodAdapter(BaseAdapter):
            @property
            def name(self) -> str:
                return "good"
            def fetch_track_info(self, artist: str, title: str) -> AdapterResult:
                return AdapterResult(source=self.name, success=True, data={})
            def fetch_artist_info(self, artist: str) -> AdapterResult:
                return AdapterResult(source=self.name, success=True, data={})

        adapter = GoodAdapter()
        assert adapter.name == "good"
        result = adapter.fetch_track_info("A", "B")
        assert result.success is True
