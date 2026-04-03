"""TDD tests for MusicBrainz adapter -- written before implementation."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from musesleuth.adapters.musicbrainz import MusicBrainzAdapter
from musesleuth.adapters.base import AdapterResult
from musesleuth.adapters.cache import ScraperCache
from musesleuth.db import create_schema


FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def db(in_memory_db: sqlite3.Connection) -> sqlite3.Connection:
    create_schema(in_memory_db)
    return in_memory_db


@pytest.fixture
def adapter(db: sqlite3.Connection) -> MusicBrainzAdapter:
    cache = ScraperCache(db)
    return MusicBrainzAdapter(cache=cache)


@pytest.fixture
def recording_fixture() -> dict:
    return json.loads((FIXTURES_DIR / "musicbrainz_recording.json").read_text())


class TestMusicBrainzLookup:
    """Tests for MusicBrainz recording lookup."""

    @pytest.mark.adapter
    def test_returns_adapter_result(self, adapter: MusicBrainzAdapter, recording_fixture: dict) -> None:
        with patch.object(adapter, "_api_request", return_value=recording_fixture):
            result = adapter.fetch_track_info("!Attention!", "Into The Inner Space")
        assert isinstance(result, AdapterResult)
        assert result.success is True
        assert result.source == "musicbrainz"

    @pytest.mark.adapter
    def test_extracts_recording_id(self, adapter: MusicBrainzAdapter, recording_fixture: dict) -> None:
        with patch.object(adapter, "_api_request", return_value=recording_fixture):
            result = adapter.fetch_track_info("!Attention!", "Into The Inner Space")
        assert result.data.get("recording_id") == "rec-1111-2222-3333-444444444444"

    @pytest.mark.adapter
    def test_extracts_isrcs(self, adapter: MusicBrainzAdapter, recording_fixture: dict) -> None:
        with patch.object(adapter, "_api_request", return_value=recording_fixture):
            result = adapter.fetch_track_info("!Attention!", "Into The Inner Space")
        assert "isrcs" in result.data
        assert "DEXXX0000001" in result.data["isrcs"]

    @pytest.mark.adapter
    def test_extracts_release_info(self, adapter: MusicBrainzAdapter, recording_fixture: dict) -> None:
        with patch.object(adapter, "_api_request", return_value=recording_fixture):
            result = adapter.fetch_track_info("!Attention!", "Into The Inner Space")
        assert result.data.get("release_title") == "Dream Dance Vol. 18"
        assert result.data.get("release_country") == "DE"

    @pytest.mark.adapter
    def test_extracts_tags(self, adapter: MusicBrainzAdapter, recording_fixture: dict) -> None:
        with patch.object(adapter, "_api_request", return_value=recording_fixture):
            result = adapter.fetch_track_info("!Attention!", "Into The Inner Space")
        assert "tags" in result.data
        assert "trance" in result.data["tags"]

    @pytest.mark.adapter
    def test_extracts_artist_country(self, adapter: MusicBrainzAdapter, recording_fixture: dict) -> None:
        with patch.object(adapter, "_api_request", return_value=recording_fixture):
            result = adapter.fetch_track_info("!Attention!", "Into The Inner Space")
        assert result.data.get("artist_country") == "DE"

    @pytest.mark.adapter
    def test_handles_api_error(self, adapter: MusicBrainzAdapter) -> None:
        with patch.object(adapter, "_api_request", side_effect=Exception("503 rate limit")):
            result = adapter.fetch_track_info("X", "Y")
        assert result.success is False

    @pytest.mark.adapter
    def test_uses_cache(self, adapter: MusicBrainzAdapter, recording_fixture: dict) -> None:
        with patch.object(adapter, "_api_request", return_value=recording_fixture) as mock:
            adapter.fetch_track_info("!Attention!", "Into The Inner Space")
            adapter.fetch_track_info("!Attention!", "Into The Inner Space")
        assert mock.call_count == 1


class TestMusicBrainzArtist:
    """Tests for MusicBrainz artist lookup."""

    @pytest.mark.adapter
    def test_returns_adapter_result(self, adapter: MusicBrainzAdapter) -> None:
        artist_data = {
            "id": "art-aaaa-bbbb-cccc-dddddddddddd",
            "name": "!Attention!",
            "type": "Group",
            "country": "DE",
            "life-span": {"begin": "1996", "ended": True, "end": "2002"},
            "tags": [{"name": "trance", "count": 3}],
        }
        with patch.object(adapter, "_api_request", return_value=artist_data):
            result = adapter.fetch_artist_info("!Attention!")
        assert result.success is True
        assert result.data.get("country") == "DE"
        assert result.data.get("active_years") is not None
