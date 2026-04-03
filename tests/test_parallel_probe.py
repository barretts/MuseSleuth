"""Tests for parallel probe functionality."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from musesleuth.db import create_schema, generate_metadata_id
from musesleuth.pipeline import PipelineOrchestrator


def _seed_track(conn: sqlite3.Connection, metadata_id: str, full_path: str) -> None:
    """Seed a track and its probe job."""
    conn.execute(
        "INSERT INTO tracks (metadata_id, file_path, filename, full_path) VALUES (?, ?, ?, ?)",
        (metadata_id, str(Path(full_path).parent), Path(full_path).name, full_path),
    )
    conn.execute(
        "INSERT INTO jobs (metadata_id, stage, status) VALUES (?, 'probe', 'pending')",
        (metadata_id,),
    )
    conn.commit()


@pytest.fixture
def db(in_memory_db: sqlite3.Connection) -> sqlite3.Connection:
    """Create a test database with schema."""
    create_schema(in_memory_db)
    return in_memory_db


class TestParallelProbe:
    """Tests for parallel probe processing."""

    @pytest.mark.integration
    def test_process_stage_parallel_handles_multiple_jobs(
        self, db: sqlite3.Connection, tmp_path: Path
    ) -> None:
        """Test that parallel processing handles multiple jobs correctly."""
        # Create fake MP3 files
        for i in range(3):
            audio = tmp_path / f"song{i}.mp3"
            frame = b"\xff\xfb\x90\x00" + b"\x00" * 413
            audio.write_bytes(frame * 10)

        # Seed tracks
        for i in range(3):
            mid = generate_metadata_id()
            _seed_track(db, mid, str(tmp_path / f"song{i}.mp3"))

        # Create a temporary database file for parallel workers
        db_file = tmp_path / "test.db"
        from musesleuth.db import get_connection
        test_conn = get_connection(db_file)
        create_schema(test_conn)
        
        # Copy data to the file-based database
        for row in db.execute("SELECT * FROM tracks").fetchall():
            test_conn.execute(
                "INSERT INTO tracks (metadata_id, file_path, filename, full_path) VALUES (?, ?, ?, ?)",
                (row["metadata_id"], row["file_path"], row["filename"], row["full_path"]),
            )
        for row in db.execute("SELECT * FROM jobs").fetchall():
            test_conn.execute(
                "INSERT INTO jobs (metadata_id, stage, status) VALUES (?, ?, ?)",
                (row["metadata_id"], row["stage"], row["status"]),
            )
        test_conn.commit()

        orch = PipelineOrchestrator(test_conn, worker_id="test")

        # Mock ffprobe to avoid needing the actual binary
        with patch("musesleuth.probe_runner._run_ffprobe") as mock_ffprobe:
            mock_ffprobe.return_value = {
"streams": [{"codec_type": "audio", "codec_name": "mp3",
"sample_rate": "44100", "channels": 2, "bit_rate": "320000",
"duration": "100.0"}],
"format": {"duration": "100.0", "size": "1234", "bit_rate": "128000", "tags": {}},
            }

# Process in parallel with 2 workers
            total = orch.process_stage_parallel("probe", max_workers=2, db_path=str(db_file))

        assert total == 3

        # Verify all jobs are marked as done
        done_count = test_conn.execute(
"SELECT COUNT(*) as cnt FROM jobs WHERE stage = 'probe' AND status = 'done'"
        ).fetchone()["cnt"]
        assert done_count == 3
        test_conn.close()

    @pytest.mark.integration
    def test_process_stage_parallel_handles_file_not_found(
        self, db: sqlite3.Connection, tmp_path: Path
    ) -> None:
        """Test that parallel processing properly handles missing files."""
        # Seed tracks with non-existent files
        for i in range(2):
            mid = generate_metadata_id()
            _seed_track(db, mid, str(tmp_path / f"missing{i}.mp3"))

        # Create a temporary database file for parallel workers
        db_file = tmp_path / "test.db"
        from musesleuth.db import get_connection
        test_conn = get_connection(db_file)
        create_schema(test_conn)
        
        # Copy data to the file-based database
        for row in db.execute("SELECT * FROM tracks").fetchall():
            test_conn.execute(
                "INSERT INTO tracks (metadata_id, file_path, filename, full_path) VALUES (?, ?, ?, ?)",
                (row["metadata_id"], row["file_path"], row["filename"], row["full_path"]),
            )
        for row in db.execute("SELECT * FROM jobs").fetchall():
            test_conn.execute(
                "INSERT INTO jobs (metadata_id, stage, status) VALUES (?, ?, ?)",
                (row["metadata_id"], row["stage"], row["status"]),
            )
        test_conn.commit()

        orch = PipelineOrchestrator(test_conn, worker_id="test")

        # Process in parallel
        total = orch.process_stage_parallel("probe", max_workers=2, db_path=str(db_file))

        assert total == 2

        # Verify all jobs are marked as failed
        failed_count = test_conn.execute(
            "SELECT COUNT(*) as cnt FROM jobs WHERE stage = 'probe' AND status = 'failed'"
        ).fetchone()["cnt"]
        assert failed_count == 2

        # Verify error messages are stored
        errors = test_conn.execute(
            "SELECT last_error FROM jobs WHERE stage = 'probe' AND status = 'failed'"
        ).fetchall()
        for row in errors:
            assert "File not found" in row["last_error"]
        test_conn.close()

    @pytest.mark.integration
    def test_process_stage_parallel_falls_back_to_sequential(
        self, db: sqlite3.Connection, tmp_path: Path
    ) -> None:
        """Test that parallel processing falls back to sequential when db_path is None."""
        audio = tmp_path / "song.mp3"
        frame = b"\xff\xfb\x90\x00" + b"\x00" * 413
        audio.write_bytes(frame * 10)

        mid = generate_metadata_id()
        _seed_track(db, mid, str(audio))

        orch = PipelineOrchestrator(db, worker_id="test")

        with patch("musesleuth.probe_runner._run_ffprobe") as mock_ffprobe:
            mock_ffprobe.return_value = {
                "streams": [{"codec_type": "audio", "codec_name": "mp3",
                             "sample_rate": "44100", "channels": 2, "duration": "100.0"}],
                "format": {"duration": "100.0", "size": "1234", "bit_rate": "128000", "tags": {}},
            }

            # Process with None db_path should fall back to sequential
            total = orch.process_stage_parallel("probe", max_workers=2, db_path=None)

        assert total == 1

        # Verify job is marked as done
        done_count = db.execute(
            "SELECT COUNT(*) as cnt FROM jobs WHERE stage = 'probe' AND status = 'done'"
        ).fetchone()["cnt"]
        assert done_count == 1