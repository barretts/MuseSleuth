"""Tests for Spotify playlist import: adapter, match logic, and API endpoints."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from musesleuth.db import create_schema, generate_metadata_id
from musesleuth.web import create_app
from musesleuth.web.routes.spotify_import import _auto_match, _extract_playlist_id, _normalize

pytestmark = [pytest.mark.unit]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db(tmp_path: Path) -> Path:
    """Create a temp DB with schema and a handful of seed tracks."""
    db_path = tmp_path / "test_spotify_import.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    create_schema(conn)

    seeds = [
        ("Test Track", "Test Artist", "Test Album"),
        ("Another Song", "Another Band", "Another Album"),
        ("Cafe Del Mar", "Energy 52", "Cafe Del Mar"),
    ]
    for title, artist, album in seeds:
        mid = generate_metadata_id()
        conn.execute(
            """
            INSERT INTO tracks (metadata_id, title, artist, album, file_path, filename, full_path)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (mid, title, artist, album, "C:\\t\\", f"{title}.mp3", f"C:\\t\\{mid}.mp3"),
        )
    conn.commit()
    conn.close()
    return db_path


def _db_conn(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Unit: URL parsing
# ---------------------------------------------------------------------------

class TestExtractPlaylistId:
    def test_full_url(self):
        url = "https://open.spotify.com/playlist/37i9dQZF1DX1rVvRgjX59F?si=abc"
        assert _extract_playlist_id(url) == "37i9dQZF1DX1rVvRgjX59F"

    def test_bare_id(self):
        assert _extract_playlist_id("37i9dQZF1DX1rVvRgjX59F") == "37i9dQZF1DX1rVvRgjX59F"

    def test_spotify_uri(self):
        assert _extract_playlist_id("spotify:playlist:37i9dQZF1DX1rVvRgjX59F") == "37i9dQZF1DX1rVvRgjX59F"

    def test_invalid_returns_none(self):
        assert _extract_playlist_id("not-a-valid-id!@#") is None

    def test_whitespace_stripped(self):
        url = "  https://open.spotify.com/playlist/ABC123  "
        assert _extract_playlist_id(url) == "ABC123"


# ---------------------------------------------------------------------------
# Unit: normalize helper
# ---------------------------------------------------------------------------

class TestNormalize:
    def test_lowercases(self):
        assert _normalize("Hello World") == "hello world"

    def test_strips_accents(self):
        assert _normalize("Cafe\u0301") == "cafe"

    def test_collapses_whitespace(self):
        assert _normalize("  foo   bar  ") == "foo bar"

    def test_none_returns_empty(self):
        assert _normalize(None) == ""


# ---------------------------------------------------------------------------
# Unit: auto-match logic
# ---------------------------------------------------------------------------

class TestAutoMatch:
    @pytest.fixture
    def db(self, tmp_path: Path):
        db_path = _make_db(tmp_path)
        conn = _db_conn(db_path)
        yield conn
        conn.close()

    def test_exact_match(self, db):
        match, confidence = _auto_match(db, "Test Track", "Test Artist")
        assert match is not None
        assert match["title"] == "Test Track"
        assert confidence == "exact"

    def test_exact_match_case_insensitive(self, db):
        match, confidence = _auto_match(db, "TEST TRACK", "TEST ARTIST")
        assert match is not None
        assert confidence == "exact"

    def test_fuzzy_match_partial(self, db):
        match, confidence = _auto_match(db, "Cafe Del", "Energy 52")
        assert match is not None
        assert confidence == "fuzzy"

    def test_no_match_returns_none(self, db):
        match, confidence = _auto_match(db, "Completely Unknown Song XYZZY", "Nobody Artist")
        assert match is None
        assert confidence == "none"

    def test_title_only_fuzzy(self, db):
        match, confidence = _auto_match(db, "Another Song", "Completely Wrong Artist XYZZY")
        assert match is not None
        assert confidence == "fuzzy"


# ---------------------------------------------------------------------------
# Unit: SpotifyAdapter.get_playlist_tracks pagination
# ---------------------------------------------------------------------------

class TestGetPlaylistTracksPagination:
    def _make_adapter(self):
        from musesleuth.adapters.spotify import SpotifyAdapter
        cache = MagicMock()
        cache.get.return_value = None
        return SpotifyAdapter(cache=cache, client_id="id", client_secret="secret")

    def _make_page(self, titles: list[str], next_url: str | None) -> dict[str, Any]:
        items = [
            {
                "track": {
                    "id": f"sp_{t}",
                    "name": t,
                    "artists": [{"name": "Artist"}],
                    "album": {"name": "Album"},
                    "duration_ms": 180000,
                }
            }
            for t in titles
        ]
        return {"items": items, "next": next_url}

    def test_single_page(self):
        adapter = self._make_adapter()
        page = self._make_page(["Track A", "Track B"], next_url=None)
        with patch.object(adapter, "_get_access_token", return_value="tok"), \
             patch.object(adapter, "_api_get", return_value=page):
            result = adapter.get_playlist_tracks("PLAYLIST_ID")
        assert result is not None
        assert len(result) == 2
        assert result[0]["title"] == "Track A"
        assert result[1]["title"] == "Track B"

    def test_pagination_follows_next(self):
        adapter = self._make_adapter()
        page1 = self._make_page(["Track A"], next_url="https://api.spotify.com/v1/next_page")
        page2 = self._make_page(["Track B"], next_url=None)

        call_count = 0

        def fake_api_get(endpoint_or_url, params=None):
            nonlocal call_count
            call_count += 1
            return page1 if call_count == 1 else None

        with patch.object(adapter, "_get_access_token", return_value="tok"), \
             patch.object(adapter, "_api_get", side_effect=fake_api_get), \
             patch.object(adapter, "_api_get_full", return_value=page2):
            result = adapter.get_playlist_tracks("PLAYLIST_ID")

        assert result is not None
        assert len(result) == 2
        assert result[0]["title"] == "Track A"
        assert result[1]["title"] == "Track B"

    def test_returns_none_on_api_failure(self):
        adapter = self._make_adapter()
        with patch.object(adapter, "_get_access_token", return_value="tok"), \
             patch.object(adapter, "_api_get", return_value=None):
            result = adapter.get_playlist_tracks("PLAYLIST_ID")
        assert result is None

    def test_skips_null_track_items(self):
        adapter = self._make_adapter()
        page = {"items": [{"track": None}, {"track": {"id": "x", "name": "Real Track",
                           "artists": [{"name": "A"}], "album": {"name": "B"},
                           "duration_ms": 100}}], "next": None}
        with patch.object(adapter, "_get_access_token", return_value="tok"), \
             patch.object(adapter, "_api_get", return_value=page):
            result = adapter.get_playlist_tracks("PLAYLIST_ID")
        assert result is not None
        assert len(result) == 1
        assert result[0]["title"] == "Real Track"


# ---------------------------------------------------------------------------
# Integration: API endpoints via TestClient
# ---------------------------------------------------------------------------

@pytest.fixture
def import_db_path(tmp_path: Path) -> Path:
    return _make_db(tmp_path)


@pytest.fixture
def api_client(import_db_path: Path) -> TestClient:
    app = create_app(import_db_path)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def api_client_with_adapter(import_db_path: Path) -> TestClient:
    """Client with a mock SpotifyAdapter injected into app state after lifespan starts."""
    app = create_app(import_db_path)

    mock_adapter = MagicMock()
    mock_adapter.get_playlist_info.return_value = {
        "id": "TESTPL",
        "name": "My Spotify Playlist",
        "description": "",
        "total_tracks": 2,
    }
    mock_adapter.get_playlist_tracks.return_value = [
        {"spotify_track_id": "sp1", "title": "Test Track",
         "artist": "Test Artist", "album": "Test Album", "duration_ms": 180000},
        {"spotify_track_id": "sp2", "title": "Unknown Song XYZ",
         "artist": "Unknown Artist XYZ", "album": None, "duration_ms": 200000},
    ]

    with TestClient(app) as c:
        # Inject after lifespan runs so it overrides the None set by build_app
        app.state.spotify_adapter = mock_adapter
        yield c


class TestPreviewEndpoint:
    def test_no_adapter_returns_503(self, api_client: TestClient):
        resp = api_client.post(
            "/api/playlists/import-spotify/preview",
            json={"playlist_url": "https://open.spotify.com/playlist/ABC123"},
        )
        assert resp.status_code == 503
        assert "credentials" in resp.json()["detail"].lower()

    def test_missing_url_returns_400(self, api_client_with_adapter: TestClient):
        resp = api_client_with_adapter.post(
            "/api/playlists/import-spotify/preview",
            json={},
        )
        assert resp.status_code == 400

    def test_invalid_url_returns_400(self, api_client_with_adapter: TestClient):
        resp = api_client_with_adapter.post(
            "/api/playlists/import-spotify/preview",
            json={"playlist_url": "not-a-spotify-url!@#"},
        )
        assert resp.status_code == 400

    def test_successful_preview(self, api_client_with_adapter: TestClient):
        resp = api_client_with_adapter.post(
            "/api/playlists/import-spotify/preview",
            json={"playlist_url": "https://open.spotify.com/playlist/TESTPL"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["spotify_playlist_name"] == "My Spotify Playlist"
        assert len(data["tracks"]) == 2

    def test_exact_match_detected(self, api_client_with_adapter: TestClient):
        resp = api_client_with_adapter.post(
            "/api/playlists/import-spotify/preview",
            json={"playlist_url": "https://open.spotify.com/playlist/TESTPL"},
        )
        assert resp.status_code == 200
        tracks = resp.json()["tracks"]
        matched = next(t for t in tracks if t["spotify_title"] == "Test Track")
        assert matched["match"] is not None
        assert matched["match_confidence"] in ("exact", "fuzzy")

    def test_unmatched_track_has_none_match(self, api_client_with_adapter: TestClient):
        resp = api_client_with_adapter.post(
            "/api/playlists/import-spotify/preview",
            json={"playlist_url": "https://open.spotify.com/playlist/TESTPL"},
        )
        assert resp.status_code == 200
        tracks = resp.json()["tracks"]
        unmatched = next(t for t in tracks if "Unknown Song XYZ" in (t["spotify_title"] or ""))
        assert unmatched["match"] is None
        assert unmatched["match_confidence"] == "none"


class TestCreateEndpoint:
    def _get_matched_id(self, api_client_with_adapter: TestClient) -> str:
        resp = api_client_with_adapter.post(
            "/api/playlists/import-spotify/preview",
            json={"playlist_url": "https://open.spotify.com/playlist/TESTPL"},
        )
        tracks = resp.json()["tracks"]
        matched = next(t for t in tracks if t["match"] is not None)
        return matched["match"]["metadata_id"]

    def test_create_playlist(self, api_client_with_adapter: TestClient):
        mid = self._get_matched_id(api_client_with_adapter)
        resp = api_client_with_adapter.post(
            "/api/playlists/import-spotify/create",
            json={
                "name": "Imported Playlist",
                "tracks": [{"spotify_index": 0, "metadata_id": mid}],
            },
        )
        assert resp.status_code == 200
        assert "playlist_id" in resp.json()

    def test_created_playlist_is_fetchable(self, api_client_with_adapter: TestClient):
        mid = self._get_matched_id(api_client_with_adapter)
        create_resp = api_client_with_adapter.post(
            "/api/playlists/import-spotify/create",
            json={
                "name": "Imported Playlist",
                "tracks": [{"spotify_index": 0, "metadata_id": mid}],
            },
        )
        playlist_id = create_resp.json()["playlist_id"]
        detail_resp = api_client_with_adapter.get(f"/api/playlists/{playlist_id}")
        assert detail_resp.status_code == 200
        data = detail_resp.json()
        assert data["playlist"]["name"] == "Imported Playlist"
        assert data["playlist"]["strategy"] == "spotify_import"
        assert len(data["tracks"]) == 1

    def test_no_tracks_returns_400(self, api_client_with_adapter: TestClient):
        resp = api_client_with_adapter.post(
            "/api/playlists/import-spotify/create",
            json={"name": "Empty", "tracks": []},
        )
        assert resp.status_code == 400

    def test_missing_name_returns_400(self, api_client_with_adapter: TestClient):
        resp = api_client_with_adapter.post(
            "/api/playlists/import-spotify/create",
            json={"tracks": [{"spotify_index": 0, "metadata_id": "someid"}]},
        )
        assert resp.status_code == 400

    def test_null_metadata_ids_skipped(self, api_client_with_adapter: TestClient):
        mid = self._get_matched_id(api_client_with_adapter)
        resp = api_client_with_adapter.post(
            "/api/playlists/import-spotify/create",
            json={
                "name": "Partial Import",
                "tracks": [
                    {"spotify_index": 0, "metadata_id": mid},
                    {"spotify_index": 1, "metadata_id": None},
                ],
            },
        )
        assert resp.status_code == 200
        playlist_id = resp.json()["playlist_id"]
        detail = api_client_with_adapter.get(f"/api/playlists/{playlist_id}").json()
        assert len(detail["tracks"]) == 1
