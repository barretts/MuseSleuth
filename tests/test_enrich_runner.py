"""TDD tests for enrich stage runner -- written before implementation."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from musesleuth.db import create_schema, generate_metadata_id
from musesleuth.enrich_runner import run_enrich_for_track, EnrichResult
from musesleuth.adapters.base import AdapterResult


def _seed_track(conn: sqlite3.Connection, mid: str, title: str, artist: str) -> None:
    conn.execute(
        "INSERT INTO tracks (metadata_id, title, artist, file_path, filename, full_path) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (mid, title, artist, "C:\\test\\", "song.mp3", f"C:\\test\\{mid}.mp3"),
    )
    conn.execute(
        "INSERT INTO jobs (metadata_id, stage, status) VALUES (?, 'enrich', 'pending')",
        (mid,),
    )
    conn.commit()


@pytest.fixture
def db(in_memory_db: sqlite3.Connection) -> sqlite3.Connection:
    create_schema(in_memory_db)
    return in_memory_db


def _mock_lastfm_track():
    return AdapterResult(source="lastfm", success=True, data={
        "listeners": 1234, "play_count": 5678,
        "tags": ["trance", "electronic", "dance"],
    })


def _mock_lastfm_artist():
    return AdapterResult(source="lastfm", success=True, data={
        "listeners": 2500, "play_count": 15000,
        "similar_artists": ["DJ Miko", "Dune"],
        "genres": ["trance", "eurodance"],
    })


def _mock_mb_track():
    return AdapterResult(source="musicbrainz", success=True, data={
        "recording_id": "rec-1111", "isrcs": ["DEXXX0000001"],
        "release_title": "Dream Dance Vol. 18", "release_country": "DE",
        "tags": ["trance", "electronic"], "artist_country": "DE",
    })


class TestRunEnrichForTrack:
    """Tests for the enrich stage orchestration."""

    @pytest.mark.integration
    def test_returns_enrich_result(self, db: sqlite3.Connection) -> None:
        mid = generate_metadata_id()
        _seed_track(db, mid, "Test Song", "Test Artist")

        with _mock_adapters():
            result = run_enrich_for_track(db, mid)

        assert isinstance(result, EnrichResult)
        assert result.success is True

    @pytest.mark.integration
    def test_stores_track_stats(self, db: sqlite3.Connection) -> None:
        mid = generate_metadata_id()
        _seed_track(db, mid, "Test Song", "Test Artist")

        with _mock_adapters():
            run_enrich_for_track(db, mid)

        rows = db.execute(
            "SELECT source, listener_count, play_count FROM track_stats WHERE metadata_id = ?",
            (mid,),
        ).fetchall()
        assert len(rows) >= 1
        sources = {r["source"] for r in rows}
        assert "lastfm" in sources

    @pytest.mark.integration
    def test_stores_artist_stats(self, db: sqlite3.Connection) -> None:
        mid = generate_metadata_id()
        _seed_track(db, mid, "Test Song", "Test Artist")

        with _mock_adapters():
            run_enrich_for_track(db, mid)

        rows = db.execute(
            "SELECT source, listeners, play_count, similar_artists FROM artist_stats WHERE metadata_id = ?",
            (mid,),
        ).fetchall()
        assert len(rows) >= 1

    @pytest.mark.integration
    def test_stores_genres_tags(self, db: sqlite3.Connection) -> None:
        mid = generate_metadata_id()
        _seed_track(db, mid, "Test Song", "Test Artist")

        with _mock_adapters():
            run_enrich_for_track(db, mid)

        rows = db.execute(
            "SELECT tag_type, tag_value, source FROM genres_tags WHERE metadata_id = ?",
            (mid,),
        ).fetchall()
        assert len(rows) >= 1
        tag_values = {r["tag_value"] for r in rows}
        assert "trance" in tag_values

    @pytest.mark.integration
    def test_stores_external_ids_from_mb(self, db: sqlite3.Connection) -> None:
        mid = generate_metadata_id()
        _seed_track(db, mid, "Test Song", "Test Artist")

        with _mock_adapters():
            run_enrich_for_track(db, mid)

        rows = db.execute(
            "SELECT source, external_id FROM external_ids WHERE metadata_id = ?",
            (mid,),
        ).fetchall()
        sources = {r["source"] for r in rows}
        assert "musicbrainz" in sources or "isrc" in sources

    @pytest.mark.integration
    def test_handles_adapter_failures_gracefully(self, db: sqlite3.Connection) -> None:
        mid = generate_metadata_id()
        _seed_track(db, mid, "Test Song", "Test Artist")

        with _mock_adapters(lastfm_fail=True):
            result = run_enrich_for_track(db, mid)

        # Should still succeed partially
        assert result.success is True
        assert result.adapters_failed >= 1

    @pytest.mark.integration
    def test_reports_adapter_count(self, db: sqlite3.Connection) -> None:
        mid = generate_metadata_id()
        _seed_track(db, mid, "Test Song", "Test Artist")

        with _mock_adapters():
            result = run_enrich_for_track(db, mid)

        assert result.adapters_succeeded >= 1


class _mock_adapters:
    """Context manager that mocks adapter methods."""

    def __init__(self, lastfm_fail=False):
        self.lastfm_fail = lastfm_fail

    def __enter__(self):
        if self.lastfm_fail:
            lfm_track = AdapterResult(source="lastfm", success=False, error="API down")
            lfm_artist = AdapterResult(source="lastfm", success=False, error="API down")
        else:
            lfm_track = _mock_lastfm_track()
            lfm_artist = _mock_lastfm_artist()

        self._patches = [
            patch("musesleuth.enrich_runner._fetch_lastfm_track", return_value=lfm_track),
            patch("musesleuth.enrich_runner._fetch_lastfm_artist", return_value=lfm_artist),
            patch("musesleuth.enrich_runner._fetch_mb_track", return_value=_mock_mb_track()),
        ]
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *args):
        for p in self._patches:
            p.stop()
