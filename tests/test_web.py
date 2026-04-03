"""Tests for the MuseSleuth web interface."""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from musesleuth.db import create_schema, generate_metadata_id
from musesleuth.web import create_app

pytestmark = [pytest.mark.unit]


@pytest.fixture
def web_db(tmp_path: Path) -> Path:
    """Create a temporary DB with schema and sample data."""
    db_path = tmp_path / "web_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    create_schema(conn)

    mid = generate_metadata_id()
    conn.execute(
        """
        INSERT INTO tracks
            (metadata_id, title, artist, album, year, file_path, filename, full_path)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (mid, "Test Track", "Test Artist", "Test Album", "2000",
         str(tmp_path) + "\\", "test.mp3", str(tmp_path / "test.mp3")),
    )
    conn.execute(
        """
        INSERT INTO musical_features
            (metadata_id, bpm_final, bpm_confidence, key_name, key_mode, energy)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (mid, 128.0, 0.95, "C", "minor", 0.72),
    )
    conn.execute(
        """
        INSERT INTO playlist_signals
            (metadata_id, decade_bucket, bpm_bucket, camelot_key, energy_tier, popularity_tier)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (mid, "2000s", "fast", "5A", "high", "mainstream"),
    )
    conn.execute(
        """
        INSERT INTO jobs (metadata_id, stage, status)
        VALUES (?, ?, ?)
        """,
        (mid, "import", "done"),
    )
    conn.commit()
    conn.close()

    # Write a tiny valid audio-ish file for the stream endpoint
    (tmp_path / "test.mp3").write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 256)

    return db_path


@pytest.fixture
def web_db_mid(web_db: Path) -> str:
    """Return the metadata_id of the track in web_db."""
    conn = sqlite3.connect(str(web_db))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT metadata_id FROM tracks LIMIT 1").fetchone()
    mid = row["metadata_id"]
    conn.close()
    return mid


@pytest.fixture
def client(web_db: Path) -> TestClient:
    """Create a FastAPI TestClient bound to the test DB."""
    app = create_app(web_db)
    with TestClient(app) as c:
        yield c


class TestTracksList:
    """Tests for the track listing page."""

    def test_root_redirects_to_tracks(self, client: TestClient):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "Test Track" in resp.text

    def test_tracks_page_renders(self, client: TestClient):
        resp = client.get("/tracks")
        assert resp.status_code == 200
        assert "Test Track" in resp.text
        assert "Test Artist" in resp.text

    def test_search_filter(self, client: TestClient):
        resp = client.get("/tracks?q=Test")
        assert resp.status_code == 200
        assert "Test Track" in resp.text

    def test_search_no_results(self, client: TestClient):
        resp = client.get("/tracks?q=nonexistent_xyz")
        assert resp.status_code == 200
        assert "No tracks found" in resp.text

    def test_decade_filter(self, client: TestClient):
        resp = client.get("/tracks?decade=2000s")
        assert resp.status_code == 200
        assert "Test Track" in resp.text

    def test_bpm_bucket_filter(self, client: TestClient):
        resp = client.get("/tracks?bpm_bucket=fast")
        assert resp.status_code == 200
        assert "Test Track" in resp.text

    def test_energy_filter(self, client: TestClient):
        resp = client.get("/tracks?energy_tier=high")
        assert resp.status_code == 200
        assert "Test Track" in resp.text

    def test_camelot_key_filter(self, client: TestClient):
        resp = client.get("/tracks?camelot_key=5A")
        assert resp.status_code == 200
        assert "Test Track" in resp.text

    def test_htmx_partial(self, client: TestClient):
        resp = client.get("/tracks", headers={"HX-Request": "true"})
        assert resp.status_code == 200
        assert "Test Track" in resp.text
        # Partial should not contain full HTML skeleton
        assert "<!DOCTYPE" not in resp.text

    def test_sort_by_artist(self, client: TestClient):
        resp = client.get("/tracks?sort=artist&order=asc")
        assert resp.status_code == 200


class TestTrackDetail:
    """Tests for the single track detail page."""

    def test_detail_renders(self, client: TestClient, web_db_mid: str):
        resp = client.get(f"/tracks/{web_db_mid}")
        assert resp.status_code == 200
        assert "Test Track" in resp.text
        assert "Test Artist" in resp.text
        assert "128" in resp.text  # BPM
        assert "5A" in resp.text   # Camelot key

    def test_detail_not_found(self, client: TestClient):
        resp = client.get("/tracks/nonexistent_id")
        assert resp.status_code == 404


class TestAudioStream:
    """Tests for the audio streaming endpoint."""

    def test_stream_full(self, client: TestClient, web_db_mid: str):
        resp = client.get(f"/audio/{web_db_mid}")
        assert resp.status_code == 200
        assert "Accept-Ranges" in resp.headers
        assert len(resp.content) == 260  # 4 + 256 bytes

    def test_stream_range(self, client: TestClient, web_db_mid: str):
        resp = client.get(f"/audio/{web_db_mid}", headers={"Range": "bytes=0-3"})
        assert resp.status_code == 206
        assert resp.content == b"\xff\xfb\x90\x00"
        assert "Content-Range" in resp.headers

    def test_stream_not_found(self, client: TestClient):
        resp = client.get("/audio/nonexistent_id")
        assert resp.status_code == 404


class TestPipelineStatus:
    """Tests for the pipeline status page."""

    def test_status_renders(self, client: TestClient):
        resp = client.get("/status")
        assert resp.status_code == 200
        assert "Pipeline Status" in resp.text
        assert "import" in resp.text
