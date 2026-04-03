"""TDD tests for Rich TUI dashboard, retry command, and export -- written before implementation."""
from __future__ import annotations

import csv
import json
import sqlite3
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from musesleuth.cli import cli
from musesleuth.dashboard import build_status_table, build_stage_table
from musesleuth.db import create_schema, generate_metadata_id, get_connection
from musesleuth.job_queue import (
    STAGES,
    JobStatus,
    create_jobs_for_track,
    fail_job,
    claim_job,
    retry_failed_jobs,
)


# --- Helpers ---

def _seed(conn: sqlite3.Connection, mid: str) -> None:
    conn.execute(
        "INSERT INTO tracks (metadata_id, title, artist, file_path, filename, full_path) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (mid, "Song", "Artist", "C:\\t\\", "s.mp3", f"C:\\t\\{mid}.mp3"),
    )
    conn.commit()
    create_jobs_for_track(conn, mid)


def _setup_db_with_track(tmp_dir: Path) -> tuple[Path, str]:
    db_path = tmp_dir / "test.db"
    conn = get_connection(db_path)
    create_schema(conn)
    mid = generate_metadata_id()
    _seed(conn, mid)
    conn.close()
    return db_path, mid


@pytest.fixture
def db(in_memory_db: sqlite3.Connection) -> sqlite3.Connection:
    create_schema(in_memory_db)
    return in_memory_db


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# --- Retry Tests ---

class TestRetryFailedJobs:
    """Tests for the retry_failed_jobs job queue function."""

    @pytest.mark.integration
    def test_retries_all_failed(self, db: sqlite3.Connection) -> None:
        mid = generate_metadata_id()
        _seed(db, mid)
        job = claim_job(db, "import", "w")
        fail_job(db, job["id"], "boom")

        retried = retry_failed_jobs(db)
        assert retried == 1

        row = db.execute(
            "SELECT status FROM jobs WHERE id = ?", (job["id"],)
        ).fetchone()
        assert row["status"] == JobStatus.PENDING

    @pytest.mark.integration
    def test_retries_by_stage(self, db: sqlite3.Connection) -> None:
        mid = generate_metadata_id()
        _seed(db, mid)
        j1 = claim_job(db, "import", "w")
        j2 = claim_job(db, "probe", "w")
        fail_job(db, j1["id"], "err")
        fail_job(db, j2["id"], "err")

        retried = retry_failed_jobs(db, stage="import")
        assert retried == 1

    @pytest.mark.integration
    def test_respects_max_attempts(self, db: sqlite3.Connection) -> None:
        mid = generate_metadata_id()
        _seed(db, mid)
        job = claim_job(db, "import", "w")
        fail_job(db, job["id"], "err1")
        # attempts is now 1; retry with max_attempts=1 should skip it
        retried = retry_failed_jobs(db, max_attempts=1)
        assert retried == 0

    @pytest.mark.integration
    def test_returns_zero_when_none_failed(self, db: sqlite3.Connection) -> None:
        mid = generate_metadata_id()
        _seed(db, mid)
        retried = retry_failed_jobs(db)
        assert retried == 0


# --- Retry CLI Command Tests ---

class TestRetryCLI:
    """Tests for `musicmeta retry` CLI command."""

    @pytest.mark.cli
    def test_retry_command_runs(self, runner: CliRunner, tmp_dir: Path) -> None:
        db_path, mid = _setup_db_with_track(tmp_dir)
        result = runner.invoke(cli, ["retry", "--db", str(db_path)])
        assert result.exit_code == 0
        assert "retried" in result.output.lower() or "Retried" in result.output

    @pytest.mark.cli
    def test_retry_with_stage(self, runner: CliRunner, tmp_dir: Path) -> None:
        db_path, mid = _setup_db_with_track(tmp_dir)
        result = runner.invoke(cli, ["retry", "--db", str(db_path), "--stage", "probe"])
        assert result.exit_code == 0

    @pytest.mark.cli
    def test_retry_missing_db_fails(self, runner: CliRunner, tmp_dir: Path) -> None:
        result = runner.invoke(cli, ["retry", "--db", str(tmp_dir / "nope.db")])
        assert result.exit_code != 0


# --- Dashboard Rendering Tests ---

class TestDashboardRendering:
    """Tests for Rich table generation functions."""

    @pytest.mark.unit
    def test_build_status_table_returns_table(self, db: sqlite3.Connection) -> None:
        mid = generate_metadata_id()
        _seed(db, mid)
        table = build_status_table(db)
        # Rich Table object should have columns
        assert table is not None
        assert len(table.columns) >= 3

    @pytest.mark.unit
    def test_build_stage_table_returns_table(self, db: sqlite3.Connection) -> None:
        mid = generate_metadata_id()
        _seed(db, mid)
        table = build_stage_table(db)
        assert table is not None
        assert len(table.columns) >= 2


# --- Export CLI Tests ---

class TestExportCommand:
    """Tests for `musicmeta export` CLI command."""

    @pytest.mark.cli
    def test_export_csv(self, runner: CliRunner, tmp_dir: Path) -> None:
        db_path, mid = _setup_db_with_track(tmp_dir)
        out_file = tmp_dir / "out.csv"
        result = runner.invoke(cli, ["export", "--db", str(db_path), "--format", "csv", "--output", str(out_file)])
        assert result.exit_code == 0
        assert out_file.exists()
        content = out_file.read_text()
        assert "metadata_id" in content

    @pytest.mark.cli
    def test_export_json(self, runner: CliRunner, tmp_dir: Path) -> None:
        db_path, mid = _setup_db_with_track(tmp_dir)
        out_file = tmp_dir / "out.json"
        result = runner.invoke(cli, ["export", "--db", str(db_path), "--format", "json", "--output", str(out_file)])
        assert result.exit_code == 0
        data = json.loads(out_file.read_text())
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["metadata_id"] == mid

    @pytest.mark.cli
    def test_export_missing_db_fails(self, runner: CliRunner, tmp_dir: Path) -> None:
        result = runner.invoke(cli, ["export", "--db", str(tmp_dir / "nope.db"), "--format", "csv", "--output", "x.csv"])
        assert result.exit_code != 0
