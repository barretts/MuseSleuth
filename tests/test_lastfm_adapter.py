"""TDD tests for Last.fm scraper adapter -- written before implementation."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from musesleuth.adapters.lastfm import LastFMAdapter
from musesleuth.adapters.base import AdapterResult
from musesleuth.adapters.cache import ScraperCache
from musesleuth.db import create_schema


FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def db(in_memory_db: sqlite3.Connection) -> sqlite3.Connection:
    create_schema(in_memory_db)
    return in_memory_db


@pytest.fixture
def adapter(db: sqlite3.Connection) -> LastFMAdapter:
    cache = ScraperCache(db)
    return LastFMAdapter(cache=cache, api_key="test_key")


@pytest.fixture
def track_fixture() -> dict:
    return json.loads((FIXTURES_DIR / "lastfm_track.json").read_text())


@pytest.fixture
def artist_fixture() -> dict:
    return json.loads((FIXTURES_DIR / "lastfm_artist.json").read_text())


class TestLastFMAdapterTrack:
    """Tests for Last.fm track info fetching."""

    @pytest.mark.adapter
    def test_returns_adapter_result(self, adapter: LastFMAdapter, track_fixture: dict) -> None:
        with patch.object(adapter, "_api_request", return_value=track_fixture):
            result = adapter.fetch_track_info("!Attention!", "Into The Inner Space")
        assert isinstance(result, AdapterResult)
        assert result.success is True
        assert result.source == "lastfm"

    @pytest.mark.adapter
    def test_extracts_listeners(self, adapter: LastFMAdapter, track_fixture: dict) -> None:
        with patch.object(adapter, "_api_request", return_value=track_fixture):
            result = adapter.fetch_track_info("!Attention!", "Into The Inner Space")
        assert result.data["listeners"] == 1234

    @pytest.mark.adapter
    def test_extracts_playcount(self, adapter: LastFMAdapter, track_fixture: dict) -> None:
        with patch.object(adapter, "_api_request", return_value=track_fixture):
            result = adapter.fetch_track_info("!Attention!", "Into The Inner Space")
        assert result.data["play_count"] == 5678

    @pytest.mark.adapter
    def test_extracts_tags(self, adapter: LastFMAdapter, track_fixture: dict) -> None:
        with patch.object(adapter, "_api_request", return_value=track_fixture):
            result = adapter.fetch_track_info("!Attention!", "Into The Inner Space")
        assert "tags" in result.data
        assert "trance" in result.data["tags"]

    @pytest.mark.adapter
    def test_handles_api_error(self, adapter: LastFMAdapter) -> None:
        with patch.object(adapter, "_api_request", side_effect=Exception("API down")):
            result = adapter.fetch_track_info("X", "Y")
        assert result.success is False
        assert result.error is not None

    @pytest.mark.adapter
    def test_uses_cache_on_second_call(self, adapter: LastFMAdapter, track_fixture: dict) -> None:
        with patch.object(adapter, "_api_request", return_value=track_fixture) as mock:
            adapter.fetch_track_info("!Attention!", "Into The Inner Space")
            adapter.fetch_track_info("!Attention!", "Into The Inner Space")
        assert mock.call_count == 1  # second call served from cache


class TestLastFMAdapterArtist:
    """Tests for Last.fm artist info fetching."""

    @pytest.mark.adapter
    def test_returns_adapter_result(self, adapter: LastFMAdapter, artist_fixture: dict) -> None:
        with patch.object(adapter, "_api_request", return_value=artist_fixture):
            result = adapter.fetch_artist_info("!Attention!")
        assert isinstance(result, AdapterResult)
        assert result.success is True

    @pytest.mark.adapter
    def test_extracts_artist_stats(self, adapter: LastFMAdapter, artist_fixture: dict) -> None:
        with patch.object(adapter, "_api_request", return_value=artist_fixture):
            result = adapter.fetch_artist_info("!Attention!")
        assert result.data["listeners"] == 2500
        assert result.data["play_count"] == 15000

    @pytest.mark.adapter
    def test_extracts_similar_artists(self, adapter: LastFMAdapter, artist_fixture: dict) -> None:
        with patch.object(adapter, "_api_request", return_value=artist_fixture):
            result = adapter.fetch_artist_info("!Attention!")
        assert "similar_artists" in result.data
        assert len(result.data["similar_artists"]) == 2

    @pytest.mark.adapter
    def test_extracts_genres(self, adapter: LastFMAdapter, artist_fixture: dict) -> None:
        with patch.object(adapter, "_api_request", return_value=artist_fixture):
            result = adapter.fetch_artist_info("!Attention!")
        assert "genres" in result.data
        assert "trance" in result.data["genres"]
