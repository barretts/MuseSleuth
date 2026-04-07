"""TDD tests for pipeline orchestrator -- written before implementation."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from musesleuth.db import create_schema, generate_metadata_id
from musesleuth.job_queue import create_jobs_for_track, claim_job, JobStatus, STAGES
from musesleuth.pipeline import (
    PipelineOrchestrator,
    PipelineStats,
    run_stage_for_job,
)


def _seed_track_with_jobs(conn: sqlite3.Connection, mid: str, full_path: str) -> None:
    conn.execute(
        "INSERT INTO tracks (metadata_id, title, artist, file_path, filename, full_path) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (mid, "Song", "Artist", str(Path(full_path).parent), Path(full_path).name, full_path),
    )
    conn.commit()
    create_jobs_for_track(conn, mid)


@pytest.fixture
def db(in_memory_db: sqlite3.Connection) -> sqlite3.Connection:
    create_schema(in_memory_db)
    return in_memory_db


class TestPipelineOrchestrator:
    """Tests for the pipeline orchestrator."""

    @pytest.mark.integration
    def test_creates_orchestrator(self, db: sqlite3.Connection) -> None:
        orch = PipelineOrchestrator(db)
        assert orch is not None

    @pytest.mark.integration
    def test_get_stats_empty(self, db: sqlite3.Connection) -> None:
        orch = PipelineOrchestrator(db)
        stats = orch.get_stats()
        assert isinstance(stats, PipelineStats)
        assert stats.total_tracks == 0

    @pytest.mark.integration
    def test_get_stats_with_tracks(self, db: sqlite3.Connection) -> None:
        mid = generate_metadata_id()
        _seed_track_with_jobs(db, mid, "C:\\test\\song.mp3")

        orch = PipelineOrchestrator(db)
        stats = orch.get_stats()
        assert stats.total_tracks == 1
        assert stats.total_jobs == len(STAGES)
        assert stats.pending_jobs == len(STAGES)

    @pytest.mark.integration
    def test_process_next_claims_job(self, db: sqlite3.Connection) -> None:
        mid = generate_metadata_id()
        _seed_track_with_jobs(db, mid, "C:\\test\\song.mp3")

        orch = PipelineOrchestrator(db, worker_id="test-worker")
        with patch("musesleuth.pipeline.run_stage_for_job") as mock_run:
            mock_run.return_value = True
            processed = orch.process_next("import")

        assert processed is True
        # The import job should now be done
        row = db.execute(
            "SELECT status FROM jobs WHERE metadata_id = ? AND stage = 'import'",
            (mid,),
        ).fetchone()
        assert row["status"] == JobStatus.DONE

    @pytest.mark.integration
    def test_process_next_returns_false_when_empty(self, db: sqlite3.Connection) -> None:
        orch = PipelineOrchestrator(db)
        processed = orch.process_next("import")
        assert processed is False

    @pytest.mark.integration
    def test_process_next_marks_failed_on_error(self, db: sqlite3.Connection) -> None:
        mid = generate_metadata_id()
        _seed_track_with_jobs(db, mid, "C:\\test\\song.mp3")

        orch = PipelineOrchestrator(db, worker_id="test-worker")
        with patch("musesleuth.pipeline.run_stage_for_job") as mock_run:
            mock_run.side_effect = Exception("stage crashed")
            processed = orch.process_next("import")

        assert processed is True  # we did process something, it just failed
        row = db.execute(
            "SELECT status, last_error FROM jobs WHERE metadata_id = ? AND stage = 'import'",
            (mid,),
        ).fetchone()
        assert row["status"] == JobStatus.FAILED
        assert "stage crashed" in row["last_error"]

    @pytest.mark.integration
    def test_run_all_stages_sequentially(self, db: sqlite3.Connection) -> None:
        mid = generate_metadata_id()
        _seed_track_with_jobs(db, mid, "C:\\test\\song.mp3")

        orch = PipelineOrchestrator(db, worker_id="test-worker")
        with patch("musesleuth.pipeline.run_stage_for_job") as mock_run:
            mock_run.return_value = True
            total = orch.run_all_pending()

        assert total == len(STAGES)

        # All jobs should be done
        rows = db.execute(
            "SELECT status FROM jobs WHERE metadata_id = ?", (mid,)
        ).fetchall()
        assert all(r["status"] == JobStatus.DONE for r in rows)


class TestRunStageForJob:
    """Tests for dispatching a single job to the right stage handler."""

    @pytest.mark.integration
    def test_import_stage_is_noop(self, db: sqlite3.Connection) -> None:
        # Import stage is already handled by CLI, so it should be a noop
        result = run_stage_for_job(db, "import", "fakeid", "C:\\test\\song.mp3")
        assert result is True

    @pytest.mark.integration
    def test_unknown_stage_returns_true(self, db: sqlite3.Connection) -> None:
        result = run_stage_for_job(db, "nonexistent", "fakeid", "C:\\test\\song.mp3")
        assert result is True

    @pytest.mark.integration
    def test_probe_stage_forwards_attempt_repair_flag(self, db: sqlite3.Connection) -> None:
        with patch("musesleuth.probe_runner.run_probe_for_track") as mock_probe:
            mock_probe.return_value = MagicMock(success=True, error=None)

            result = run_stage_for_job(
                db,
                "probe",
                "fakeid",
                "C:\\test\\song.mp3",
                probe_attempt_repair=True,
            )

        assert result is True
        mock_probe.assert_called_once_with(
            db,
            "fakeid",
            "C:\\test\\song.mp3",
            attempt_repair=True,
        )


class TestPipelineStats:
    """Tests for pipeline statistics."""

    @pytest.mark.integration
    def test_completion_percentage(self, db: sqlite3.Connection) -> None:
        mid = generate_metadata_id()
        _seed_track_with_jobs(db, mid, "C:\\test\\song.mp3")

        # Complete half the jobs
        for _ in range(len(STAGES) // 2):
            job = db.execute(
                "SELECT id FROM jobs WHERE metadata_id = ? AND status = 'pending' LIMIT 1",
                (mid,),
            ).fetchone()
            if job:
                db.execute(
                    "UPDATE jobs SET status = 'done' WHERE id = ?", (job["id"],)
                )
        db.commit()

        orch = PipelineOrchestrator(db)
        stats = orch.get_stats()
        assert 0.0 < stats.completion_pct < 100.0
