"""TDD tests for the resumable job queue -- written before implementation."""
from __future__ import annotations

import sqlite3
import time

import pytest

from musesleuth.db import create_schema
from musesleuth.job_queue import (
    JobStatus,
    claim_job,
    create_jobs_for_track,
    expire_stale_leases,
    get_job_counts,
    release_job,
    fail_job,
    STAGES,
)


def _insert_dummy_track(conn: sqlite3.Connection, metadata_id: str) -> None:
    """Insert a minimal track row to satisfy FK constraints in tests."""
    conn.execute(
        "INSERT OR IGNORE INTO tracks (metadata_id, file_path, filename, full_path) "
        "VALUES (?, ?, ?, ?)",
        (metadata_id, "C:\\test\\", "dummy.mp3", f"C:\\test\\{metadata_id}.mp3"),
    )
    conn.commit()


@pytest.fixture
def db_with_schema(in_memory_db: sqlite3.Connection) -> sqlite3.Connection:
    create_schema(in_memory_db)
    return in_memory_db


class TestCreateJobs:
    """Tests for seeding jobs."""

    @pytest.mark.integration
    def test_creates_one_job_per_stage(self, db_with_schema: sqlite3.Connection) -> None:
        _insert_dummy_track(db_with_schema, "01HXTEST000000000000000001")
        create_jobs_for_track(db_with_schema, "01HXTEST000000000000000001")
        cursor = db_with_schema.execute(
            "SELECT COUNT(*) as cnt FROM jobs WHERE metadata_id = ?",
            ("01HXTEST000000000000000001",),
        )
        assert cursor.fetchone()["cnt"] == len(STAGES)

    @pytest.mark.integration
    def test_all_start_as_pending(self, db_with_schema: sqlite3.Connection) -> None:
        _insert_dummy_track(db_with_schema, "01HXTEST000000000000000001")
        create_jobs_for_track(db_with_schema, "01HXTEST000000000000000001")
        cursor = db_with_schema.execute(
            "SELECT DISTINCT status FROM jobs WHERE metadata_id = ?",
            ("01HXTEST000000000000000001",),
        )
        statuses = {row["status"] for row in cursor.fetchall()}
        assert statuses == {JobStatus.PENDING}

    @pytest.mark.integration
    def test_idempotent(self, db_with_schema: sqlite3.Connection) -> None:
        _insert_dummy_track(db_with_schema, "01HXTEST000000000000000001")
        create_jobs_for_track(db_with_schema, "01HXTEST000000000000000001")
        create_jobs_for_track(db_with_schema, "01HXTEST000000000000000001")
        cursor = db_with_schema.execute(
            "SELECT COUNT(*) as cnt FROM jobs WHERE metadata_id = ?",
            ("01HXTEST000000000000000001",),
        )
        assert cursor.fetchone()["cnt"] == len(STAGES)


class TestClaimAndRelease:
    """Tests for job claiming and releasing."""

    @pytest.mark.integration
    def test_claim_returns_job(self, db_with_schema: sqlite3.Connection) -> None:
        _insert_dummy_track(db_with_schema, "01HXTEST000000000000000001")
        create_jobs_for_track(db_with_schema, "01HXTEST000000000000000001")
        job = claim_job(db_with_schema, stage="import", worker_id="w1")
        assert job is not None
        assert job["metadata_id"] == "01HXTEST000000000000000001"

    @pytest.mark.integration
    def test_claim_marks_running(self, db_with_schema: sqlite3.Connection) -> None:
        _insert_dummy_track(db_with_schema, "01HXTEST000000000000000001")
        create_jobs_for_track(db_with_schema, "01HXTEST000000000000000001")
        job = claim_job(db_with_schema, stage="import", worker_id="w1")
        cursor = db_with_schema.execute(
            "SELECT status FROM jobs WHERE id = ?", (job["id"],)
        )
        assert cursor.fetchone()["status"] == JobStatus.RUNNING

    @pytest.mark.integration
    def test_claim_returns_none_when_empty(
        self, db_with_schema: sqlite3.Connection
    ) -> None:
        job = claim_job(db_with_schema, stage="import", worker_id="w1")
        assert job is None

    @pytest.mark.integration
    def test_release_marks_done(self, db_with_schema: sqlite3.Connection) -> None:
        _insert_dummy_track(db_with_schema, "01HXTEST000000000000000001")
        create_jobs_for_track(db_with_schema, "01HXTEST000000000000000001")
        job = claim_job(db_with_schema, stage="import", worker_id="w1")
        release_job(db_with_schema, job["id"])
        cursor = db_with_schema.execute(
            "SELECT status FROM jobs WHERE id = ?", (job["id"],)
        )
        assert cursor.fetchone()["status"] == JobStatus.DONE

    @pytest.mark.integration
    def test_double_claim_returns_none(
        self, db_with_schema: sqlite3.Connection
    ) -> None:
        _insert_dummy_track(db_with_schema, "01HXTEST000000000000000001")
        create_jobs_for_track(db_with_schema, "01HXTEST000000000000000001")
        claim_job(db_with_schema, stage="import", worker_id="w1")
        second = claim_job(db_with_schema, stage="import", worker_id="w2")
        assert second is None


class TestFailJob:
    """Tests for marking a job as failed."""

    @pytest.mark.integration
    def test_fail_records_error(self, db_with_schema: sqlite3.Connection) -> None:
        _insert_dummy_track(db_with_schema, "01HXTEST000000000000000001")
        create_jobs_for_track(db_with_schema, "01HXTEST000000000000000001")
        job = claim_job(db_with_schema, stage="import", worker_id="w1")
        fail_job(db_with_schema, job["id"], error="file not found")
        cursor = db_with_schema.execute(
            "SELECT status, last_error, attempts FROM jobs WHERE id = ?",
            (job["id"],),
        )
        row = cursor.fetchone()
        assert row["status"] == JobStatus.FAILED
        assert row["last_error"] == "file not found"
        assert row["attempts"] >= 1


class TestExpireStaleLeases:
    """Tests for lease expiration."""

    @pytest.mark.integration
    def test_expires_old_running_jobs(
        self, db_with_schema: sqlite3.Connection
    ) -> None:
        _insert_dummy_track(db_with_schema, "01HXTEST000000000000000001")
        create_jobs_for_track(db_with_schema, "01HXTEST000000000000000001")
        job = claim_job(db_with_schema, stage="import", worker_id="w1")
        # Manually backdate the lease
        db_with_schema.execute(
            "UPDATE jobs SET claimed_at = datetime('now', '-1 hour') WHERE id = ?",
            (job["id"],),
        )
        expired = expire_stale_leases(db_with_schema, max_age_seconds=60)
        assert expired == 1
        cursor = db_with_schema.execute(
            "SELECT status FROM jobs WHERE id = ?", (job["id"],)
        )
        assert cursor.fetchone()["status"] == JobStatus.PENDING


class TestGetJobCounts:
    """Tests for progress reporting."""

    @pytest.mark.integration
    def test_counts_by_stage(self, db_with_schema: sqlite3.Connection) -> None:
        _insert_dummy_track(db_with_schema, "01HXTEST000000000000000001")
        _insert_dummy_track(db_with_schema, "01HXTEST000000000000000002")
        create_jobs_for_track(db_with_schema, "01HXTEST000000000000000001")
        create_jobs_for_track(db_with_schema, "01HXTEST000000000000000002")
        counts = get_job_counts(db_with_schema)
        assert counts["import"]["pending"] == 2
