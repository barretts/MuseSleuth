"""Validate that process_stage_parallel actually works with ThreadPoolExecutor,
and that the timbre stage produces correct results on real audio.
"""
from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from musesleuth.db import create_schema, get_connection
from musesleuth.job_queue import (
    STAGES,
    JobStatus,
    backfill_missing_jobs,
    claim_job,
    refresh_stage,
    retry_failed_jobs,
)
from musesleuth.pipeline import PipelineOrchestrator


def _setup_db(db_path: Path, n_tracks: int = 20, wav_path: str = "C:\\test\\track.wav") -> sqlite3.Connection:
    """Create a real on-disk DB with N dummy tracks and seed jobs."""
    conn = get_connection(db_path)
    create_schema(conn)
    for i in range(n_tracks):
        mid = f"test-{i:04d}"
        fp = wav_path if wav_path != "C:\\test\\track.wav" else f"C:\\test\\track_{i}.wav"
        conn.execute(
            """
            INSERT OR IGNORE INTO tracks
                (metadata_id, file_path, filename, full_path)
            VALUES (?, ?, ?, ?)
            """,
            (mid, "C:\\test\\", f"track_{i}.wav", fp),
        )
    conn.commit()
    backfill_missing_jobs(conn, stages=["timbre"])
    return conn


def _mock_timbre_success(conn, metadata_id, file_path):
    """Fake timbre that always succeeds quickly."""
    time.sleep(0.01)
    return True


def _mock_timbre_mixed(conn, metadata_id, file_path):
    """Fake timbre: first 5 tracks fail, rest succeed."""
    time.sleep(0.01)
    idx = int(metadata_id.split("-")[1])
    if idx < 5:
        return False
    return True


class TestParallelPipeline:
    """Tests for process_stage_parallel with ThreadPoolExecutor."""

    def test_all_jobs_complete(self, tmp_path):
        """All 20 jobs should finish and be marked done."""
        db_path = tmp_path / "test.db"
        conn = _setup_db(db_path, n_tracks=20)

        orch = PipelineOrchestrator(conn, worker_id="test")

        with patch(
            "musesleuth.timbre.run_timbre_for_track", side_effect=_mock_timbre_success
        ):
            total = orch.process_stage_parallel(
                "timbre", max_workers=4, db_path=str(db_path)
            )

        assert total == 20

        # All should be done
        done = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE stage = 'timbre' AND status = ?",
            (JobStatus.DONE,),
        ).fetchone()[0]
        assert done == 20

        pending = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE stage = 'timbre' AND status = ?",
            (JobStatus.PENDING,),
        ).fetchone()[0]
        assert pending == 0

        conn.close()

    def test_concurrency_faster_than_sequential(self, tmp_path):
        """4 workers should be measurably faster than sequential for sleep-based work."""
        db_path = tmp_path / "test.db"
        conn = _setup_db(db_path, n_tracks=16)

        def _slow_timbre(conn, metadata_id, file_path):
            time.sleep(0.05)
            return True

        orch = PipelineOrchestrator(conn, worker_id="test")

        with patch(
            "musesleuth.timbre.run_timbre_for_track", side_effect=_slow_timbre
        ):
            t0 = time.monotonic()
            total = orch.process_stage_parallel(
                "timbre", max_workers=4, db_path=str(db_path)
            )
            elapsed = time.monotonic() - t0

        assert total == 16
        # Sequential would take 16 * 0.05 = 0.8s minimum
        # With 4 workers should be ~0.2s + overhead, definitely < 0.6s
        assert elapsed < 0.6, f"Took {elapsed:.2f}s -- not parallel!"

        conn.close()

    def test_failures_recorded(self, tmp_path):
        """Jobs that raise should be marked failed, not done."""
        db_path = tmp_path / "test.db"
        conn = _setup_db(db_path, n_tracks=10)

        orch = PipelineOrchestrator(conn, worker_id="test")

        with patch(
            "musesleuth.timbre.run_timbre_for_track", side_effect=_mock_timbre_mixed
        ):
            total = orch.process_stage_parallel(
                "timbre", max_workers=4, db_path=str(db_path)
            )

        assert total == 10

        done = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE stage = 'timbre' AND status = ?",
            (JobStatus.DONE,),
        ).fetchone()[0]
        failed = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE stage = 'timbre' AND status = ?",
            (JobStatus.FAILED,),
        ).fetchone()[0]

        assert done == 5
        assert failed == 5

        conn.close()

    def test_zero_pending_returns_immediately(self, tmp_path):
        """No pending jobs -> returns 0 instantly."""
        db_path = tmp_path / "test.db"
        conn = _setup_db(db_path, n_tracks=5)

        # Mark all timbre jobs as done
        conn.execute(
            "UPDATE jobs SET status = ? WHERE stage = 'timbre'",
            (JobStatus.DONE,),
        )
        conn.commit()

        orch = PipelineOrchestrator(conn, worker_id="test")
        total = orch.process_stage_parallel(
            "timbre", max_workers=4, db_path=str(db_path)
        )
        assert total == 0

        conn.close()

    def test_refresh_resets_running_and_failed(self, tmp_path):
        """refresh_stage should reset done+running jobs, retry_failed_jobs resets failed."""
        db_path = tmp_path / "test.db"
        conn = _setup_db(db_path, n_tracks=6)

        # Simulate mixed states
        ids = conn.execute(
            "SELECT id FROM jobs WHERE stage = 'timbre' ORDER BY id"
        ).fetchall()
        conn.execute(
            "UPDATE jobs SET status = ? WHERE id = ?", (JobStatus.DONE, ids[0]["id"])
        )
        conn.execute(
            "UPDATE jobs SET status = ? WHERE id = ?", (JobStatus.RUNNING, ids[1]["id"])
        )
        conn.execute(
            "UPDATE jobs SET status = ?, last_error = 'boom' WHERE id = ?",
            (JobStatus.FAILED, ids[2]["id"]),
        )
        conn.commit()

        refreshed = refresh_stage(conn, "timbre")
        retried = retry_failed_jobs(conn, stage="timbre")

        # refresh_stage resets done + running
        assert refreshed == 2
        # retry_failed_jobs resets failed
        assert retried == 1

        pending = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE stage = 'timbre' AND status = ?",
            (JobStatus.PENDING,),
        ).fetchone()[0]
        assert pending == 6  # all back to pending

        conn.close()


class TestTimbreReal:
    """End-to-end timbre tests using real synthetic audio (no mocks)."""

    def test_extract_timbre_on_sine_wave(self, synthetic_audio):
        """extract_timbre should produce valid features from a WAV file."""
        from musesleuth.timbre import extract_timbre

        _, sr, wav_path = synthetic_audio
        result = extract_timbre(str(wav_path))

        assert result is not None, "extract_timbre returned None on valid WAV"
        assert result.mfcc_mean is not None
        assert len(result.mfcc_mean) == 20
        assert len(result.mfcc_var) == 20
        assert result.centroid_mean is not None and result.centroid_mean > 0
        assert result.rolloff_mean is not None and result.rolloff_mean > 0
        assert result.bandwidth_mean is not None
        assert result.flatness_mean is not None
        assert result.zcr_mean is not None

    def test_run_timbre_for_track_persists_to_db(self, tmp_path, synthetic_audio):
        """run_timbre_for_track should write features to timbre_features table."""
        from musesleuth.timbre import run_timbre_for_track, deserialize_array

        _, sr, wav_path = synthetic_audio
        db_path = tmp_path / "timbre_test.db"
        conn = get_connection(db_path)
        create_schema(conn)

        mid = "timbre-test-001"
        conn.execute(
            "INSERT INTO tracks (metadata_id, file_path, filename, full_path) VALUES (?, ?, ?, ?)",
            (mid, str(wav_path.parent), wav_path.name, str(wav_path)),
        )
        conn.commit()

        success = run_timbre_for_track(conn, mid, str(wav_path))
        assert success is True

        row = conn.execute(
            "SELECT * FROM timbre_features WHERE metadata_id = ?", (mid,)
        ).fetchone()
        assert row is not None, "No row in timbre_features after run_timbre_for_track"
        assert row["centroid_mean"] > 0
        assert row["rolloff_mean"] > 0

        mfcc = deserialize_array(row["mfcc_mean"])
        assert mfcc is not None
        assert len(mfcc) == 20

        conn.close()

    def test_timbre_parallel_with_real_audio(self, tmp_path, synthetic_audio, caplog):
        """process_stage_parallel should process real audio files with multiple workers."""
        import shutil

        _, sr, wav_path = synthetic_audio
        db_path = tmp_path / "parallel_timbre.db"
        n = 8

        # Create n copies so each track has a unique full_path
        audio_dir = tmp_path / "audio"
        audio_dir.mkdir()
        track_paths = []
        for i in range(n):
            dest = audio_dir / f"track_{i}.wav"
            shutil.copy2(wav_path, dest)
            track_paths.append(str(dest))

        conn = get_connection(db_path)
        create_schema(conn)
        for i in range(n):
            mid = f"test-{i:04d}"
            conn.execute(
                "INSERT OR IGNORE INTO tracks (metadata_id, file_path, filename, full_path) VALUES (?, ?, ?, ?)",
                (mid, str(audio_dir), f"track_{i}.wav", track_paths[i]),
            )
        conn.commit()
        backfill_missing_jobs(conn, stages=["timbre"])

        orch = PipelineOrchestrator(conn, worker_id="test")

        with caplog.at_level(logging.DEBUG, logger="musesleuth.pipeline"):
            total = orch.process_stage_parallel(
                "timbre", max_workers=4, db_path=str(db_path)
            )

        assert total == n, f"Expected {n} processed, got {total}"

        done = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE stage = 'timbre' AND status = ?",
            (JobStatus.DONE,),
        ).fetchone()[0]
        assert done == n, f"Expected {n} done, got {done}"

        failed = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE stage = 'timbre' AND status = ?",
            (JobStatus.FAILED,),
        ).fetchone()[0]
        assert failed == 0, f"Expected 0 failed, got {failed}"

        # Verify DB rows were actually written
        timbre_rows = conn.execute("SELECT COUNT(*) FROM timbre_features").fetchone()[0]
        assert timbre_rows == n, f"Expected {n} timbre_features rows, got {timbre_rows}"

        # Verify logging captured thread activity
        assert any("ThreadPool" in r.message for r in caplog.records), (
            "Expected ThreadPool log entry in caplog"
        )

        conn.close()

    def test_timbre_missing_file_fails_gracefully(self, tmp_path):
        """Timbre on a nonexistent file should fail the job, not crash."""
        db_path = tmp_path / "missing.db"
        conn = _setup_db(db_path, n_tracks=3)

        orch = PipelineOrchestrator(conn, worker_id="test")
        total = orch.process_stage_parallel(
            "timbre", max_workers=2, db_path=str(db_path)
        )

        assert total == 3

        failed = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE stage = 'timbre' AND status = ?",
            (JobStatus.FAILED,),
        ).fetchone()[0]
        assert failed == 3, f"Expected 3 failed (missing files), got {failed}"

        conn.close()
