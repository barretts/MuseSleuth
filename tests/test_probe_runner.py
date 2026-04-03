"""TDD tests for probe stage runner -- written before implementation."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from musesleuth.db import create_schema, generate_metadata_id
from musesleuth.probe_runner import run_probe_for_track, ProbeResult
from musesleuth.sidecar import SidecarData, write_sidecar, read_sidecar


def _seed_track(conn: sqlite3.Connection, metadata_id: str, full_path: str) -> None:
    conn.execute(
        "INSERT INTO tracks (metadata_id, file_path, filename, full_path) VALUES (?, ?, ?, ?)",
        (metadata_id, str(Path(full_path).parent), Path(full_path).name, full_path),
    )
    conn.execute(
        "INSERT INTO jobs (metadata_id, stage, status) VALUES (?, 'probe', 'pending')",
        (metadata_id,),
    )
    conn.commit()


def _make_fake_mp3(path: Path) -> Path:
    """Create a minimal file that looks like an MP3."""
    frame = b"\xff\xfb\x90\x00" + b"\x00" * 413
    path.write_bytes(frame * 10)
    return path


@pytest.fixture
def db(in_memory_db: sqlite3.Connection) -> sqlite3.Connection:
    create_schema(in_memory_db)
    return in_memory_db


class TestProbeResult:
    """Tests for the ProbeResult data class."""

    @pytest.mark.unit
    def test_success_result_has_technical(self) -> None:
        r = ProbeResult(success=True, metadata_id="test123")
        assert r.success is True

    @pytest.mark.unit
    def test_failure_result_has_error(self) -> None:
        r = ProbeResult(success=False, metadata_id="test123", error="file gone")
        assert r.error == "file gone"


class TestRunProbeForTrack:
    """Tests for the orchestrated probe of a single track."""

    @pytest.mark.integration
    def test_returns_probe_result(self, db: sqlite3.Connection, tmp_path: Path) -> None:
        mid = generate_metadata_id()
        audio = _make_fake_mp3(tmp_path / "song.mp3")
        _seed_track(db, mid, str(audio))

        with patch("musesleuth.probe_runner._run_ffprobe") as mock_ffprobe:
            mock_ffprobe.return_value = {
                "streams": [{"codec_type": "audio", "codec_name": "mp3",
                             "sample_rate": "44100", "channels": 2, "bit_rate": "320000",
                             "duration": "214.0"}],
                "format": {"duration": "214.0", "size": "8567890", "bit_rate": "320000",
                           "tags": {"title": "Test"}},
            }
            result = run_probe_for_track(db, mid, str(audio))

        assert isinstance(result, ProbeResult)
        assert result.success is True

    @pytest.mark.integration
    def test_stores_technical_features(self, db: sqlite3.Connection, tmp_path: Path) -> None:
        mid = generate_metadata_id()
        audio = _make_fake_mp3(tmp_path / "song.mp3")
        _seed_track(db, mid, str(audio))

        with patch("musesleuth.probe_runner._run_ffprobe") as mock_ffprobe:
            mock_ffprobe.return_value = {
                "streams": [{"codec_type": "audio", "codec_name": "mp3",
                             "sample_rate": "44100", "channels": 2, "bit_rate": "320000",
                             "duration": "214.0"}],
                "format": {"duration": "214.0", "size": "8567890", "bit_rate": "320000",
                           "tags": {}},
            }
            run_probe_for_track(db, mid, str(audio))

        row = db.execute(
            "SELECT codec, bitrate, sample_rate, channels, duration_ms FROM technical_features WHERE metadata_id = ?",
            (mid,),
        ).fetchone()
        assert row is not None
        assert row["codec"] == "mp3"
        assert row["bitrate"] == 320000
        assert row["sample_rate"] == 44100
        assert row["channels"] == 2
        assert row["duration_ms"] == 214000

    @pytest.mark.integration
    def test_stores_raw_ffprobe(self, db: sqlite3.Connection, tmp_path: Path) -> None:
        mid = generate_metadata_id()
        audio = _make_fake_mp3(tmp_path / "song.mp3")
        _seed_track(db, mid, str(audio))

        ffprobe_data = {
            "streams": [{"codec_type": "audio", "codec_name": "mp3",
                         "sample_rate": "44100", "channels": 2, "duration": "100.0"}],
            "format": {"duration": "100.0", "size": "1234", "bit_rate": "128000", "tags": {}},
        }
        with patch("musesleuth.probe_runner._run_ffprobe") as mock_ffprobe:
            mock_ffprobe.return_value = ffprobe_data
            run_probe_for_track(db, mid, str(audio))

        row = db.execute(
            "SELECT raw_ffprobe FROM technical_features WHERE metadata_id = ?", (mid,)
        ).fetchone()
        assert row is not None
        assert row["raw_ffprobe"] is not None
        parsed = json.loads(row["raw_ffprobe"])
        assert "streams" in parsed

    @pytest.mark.integration
    def test_computes_partial_hash(self, db: sqlite3.Connection, tmp_path: Path) -> None:
        mid = generate_metadata_id()
        audio = _make_fake_mp3(tmp_path / "song.mp3")
        _seed_track(db, mid, str(audio))

        with patch("musesleuth.probe_runner._run_ffprobe") as mock_ffprobe:
            mock_ffprobe.return_value = {"streams": [], "format": {}}
            run_probe_for_track(db, mid, str(audio))

        row = db.execute(
            "SELECT hash_partial FROM track_sidecars WHERE metadata_id = ?", (mid,)
        ).fetchone()
        assert row is not None
        assert row["hash_partial"] is not None
        assert len(row["hash_partial"]) == 64

    @pytest.mark.integration
    def test_stores_tag_snapshot(self, db: sqlite3.Connection, tmp_path: Path) -> None:
        mid = generate_metadata_id()
        audio = _make_fake_mp3(tmp_path / "song.mp3")
        _seed_track(db, mid, str(audio))

        with patch("musesleuth.probe_runner._run_ffprobe") as mock_ffprobe:
            mock_ffprobe.return_value = {"streams": [], "format": {}}
            run_probe_for_track(db, mid, str(audio))

        row = db.execute(
            "SELECT tags_json FROM tag_snapshot_raw WHERE metadata_id = ?", (mid,)
        ).fetchone()
        assert row is not None
        assert row["tags_json"] is not None

    @pytest.mark.integration
    def test_updates_sidecar_hash(self, db: sqlite3.Connection, tmp_path: Path) -> None:
        mid = generate_metadata_id()
        audio = _make_fake_mp3(tmp_path / "song.mp3")
        _seed_track(db, mid, str(audio))

        # Write initial sidecar
        sc_data = SidecarData(metadata_id=mid, path=str(audio), size=0, mtime=0.0)
        write_sidecar(audio, sc_data)

        with patch("musesleuth.probe_runner._run_ffprobe") as mock_ffprobe:
            mock_ffprobe.return_value = {"streams": [], "format": {}}
            run_probe_for_track(db, mid, str(audio))

        updated_sc = read_sidecar(audio)
        assert updated_sc is not None
        assert updated_sc.hash_partial is not None
        assert len(updated_sc.hash_partial) == 64

    @pytest.mark.integration
    def test_handles_missing_file(self, db: sqlite3.Connection, tmp_path: Path) -> None:
        mid = generate_metadata_id()
        missing = str(tmp_path / "gone.mp3")
        _seed_track(db, mid, missing)

        result = run_probe_for_track(db, mid, missing)
        assert result.success is False
        assert result.error is not None
