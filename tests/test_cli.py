"""TDD tests for CLI commands -- written before implementation."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from musesleuth.cli import cli
from musesleuth.db import create_schema, generate_metadata_id, get_connection
from musesleuth.job_queue import create_jobs_for_track, STAGES, JobStatus
from musesleuth.scanner import ScanResult, RelocateResult


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


class TestImportCommand:
    """Tests for `musicmeta import` CLI."""

    @pytest.mark.cli
    def test_import_creates_db(
        self, runner: CliRunner, sample_csv_file: Path, tmp_dir: Path
    ) -> None:
        db_path = tmp_dir / "test.db"
        result = runner.invoke(
            cli, ["import", str(sample_csv_file), "--db", str(db_path)]
        )
        assert result.exit_code == 0, result.output
        assert db_path.exists()

    @pytest.mark.cli
    def test_import_populates_tracks(
        self, runner: CliRunner, sample_csv_file: Path, tmp_dir: Path
    ) -> None:
        db_path = tmp_dir / "test.db"
        runner.invoke(cli, ["import", str(sample_csv_file), "--db", str(db_path)])
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        count = conn.execute("SELECT COUNT(*) as cnt FROM tracks").fetchone()["cnt"]
        conn.close()
        assert count == 2

    @pytest.mark.cli
    def test_import_assigns_metadata_ids(
        self, runner: CliRunner, sample_csv_file: Path, tmp_dir: Path
    ) -> None:
        db_path = tmp_dir / "test.db"
        runner.invoke(cli, ["import", str(sample_csv_file), "--db", str(db_path)])
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT metadata_id FROM tracks").fetchall()
        conn.close()
        ids = [r["metadata_id"] for r in rows]
        assert len(ids) == 2
        assert len(set(ids)) == 2  # unique
        assert all(len(mid) == 26 for mid in ids)  # ULID length

    @pytest.mark.cli
    def test_import_creates_jobs(
        self, runner: CliRunner, sample_csv_file: Path, tmp_dir: Path
    ) -> None:
        db_path = tmp_dir / "test.db"
        runner.invoke(cli, ["import", str(sample_csv_file), "--db", str(db_path)])
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        count = conn.execute("SELECT COUNT(*) as cnt FROM jobs").fetchone()["cnt"]
        conn.close()
        assert count > 0

    @pytest.mark.cli
    def test_import_marks_import_job_as_done(
        self, runner: CliRunner, sample_csv_file: Path, tmp_dir: Path
    ) -> None:
        db_path = tmp_dir / "test.db"
        runner.invoke(cli, ["import", str(sample_csv_file), "--db", str(db_path)])
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        # Check that import jobs are marked as done
        import_jobs = conn.execute(
            "SELECT status FROM jobs WHERE stage = 'import'"
        ).fetchall()
        conn.close()
        assert len(import_jobs) > 0
        assert all(job["status"] == "done" for job in import_jobs)

    @pytest.mark.cli
    def test_import_is_idempotent(
        self, runner: CliRunner, sample_csv_file: Path, tmp_dir: Path
    ) -> None:
        db_path = tmp_dir / "test.db"
        runner.invoke(cli, ["import", str(sample_csv_file), "--db", str(db_path)])
        runner.invoke(cli, ["import", str(sample_csv_file), "--db", str(db_path)])
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        count = conn.execute("SELECT COUNT(*) as cnt FROM tracks").fetchone()["cnt"]
        conn.close()
        assert count == 2

    @pytest.mark.cli
    def test_import_missing_csv_fails(
        self, runner: CliRunner, tmp_dir: Path
    ) -> None:
        db_path = tmp_dir / "test.db"
        result = runner.invoke(
            cli, ["import", str(tmp_dir / "nope.csv"), "--db", str(db_path)]
        )
        assert result.exit_code != 0


def _setup_db_with_track(tmp_dir: Path) -> tuple[Path, str]:
    """Helper: create a DB with one track and all jobs."""
    db_path = tmp_dir / "test.db"
    conn = get_connection(db_path)
    create_schema(conn)
    mid = generate_metadata_id()
    conn.execute(
        "INSERT INTO tracks (metadata_id, title, artist, file_path, filename, full_path) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (mid, "Test Song", "Test Artist", str(tmp_dir), "song.mp3", str(tmp_dir / "song.mp3")),
    )
    conn.commit()
    create_jobs_for_track(conn, mid)
    conn.close()
    return db_path, mid


class TestStatusCommand:
    """Tests for `musicmeta status` CLI."""

    @pytest.mark.cli
    def test_status_shows_track_count(self, runner: CliRunner, tmp_dir: Path) -> None:
        db_path, _ = _setup_db_with_track(tmp_dir)
        result = runner.invoke(cli, ["status", "--db", str(db_path)])
        assert result.exit_code == 0
        assert "1" in result.output  # at least the track count

    @pytest.mark.cli
    def test_status_shows_job_summary(self, runner: CliRunner, tmp_dir: Path) -> None:
        db_path, _ = _setup_db_with_track(tmp_dir)
        result = runner.invoke(cli, ["status", "--db", str(db_path)])
        assert result.exit_code == 0
        assert "pending" in result.output.lower() or "Pending" in result.output

    @pytest.mark.cli
    def test_status_missing_db_fails(self, runner: CliRunner, tmp_dir: Path) -> None:
        result = runner.invoke(cli, ["status", "--db", str(tmp_dir / "nope.db")])
        assert result.exit_code != 0


class TestRunCommand:
    """Tests for `musicmeta run` CLI."""

    @pytest.mark.cli
    def test_run_processes_jobs(self, runner: CliRunner, tmp_dir: Path) -> None:
        db_path, mid = _setup_db_with_track(tmp_dir)
        with patch("musesleuth.pipeline.run_stage_for_job", return_value=True):
            result = runner.invoke(cli, ["run", "--db", str(db_path)])
        assert result.exit_code == 0
        assert "processed" in result.output.lower() or "Processed" in result.output

    @pytest.mark.cli
    def test_run_with_stage_filter(self, runner: CliRunner, tmp_dir: Path) -> None:
        db_path, mid = _setup_db_with_track(tmp_dir)
        with patch("musesleuth.pipeline.run_stage_for_job", return_value=True):
            result = runner.invoke(cli, ["run", "--db", str(db_path), "--stage", "import"])
        assert result.exit_code == 0

    @pytest.mark.cli
    def test_run_missing_db_fails(self, runner: CliRunner, tmp_dir: Path) -> None:
        result = runner.invoke(cli, ["run", "--db", str(tmp_dir / "nope.db")])
        assert result.exit_code != 0

    @pytest.mark.cli
    def test_run_skips_ml_classify_by_default(self, runner: CliRunner, tmp_dir: Path) -> None:
        db_path, _ = _setup_db_with_track(tmp_dir)
        with patch("musesleuth.pipeline.run_stage_for_job", return_value=True) as mock_run:
            result = runner.invoke(cli, ["run", "--db", str(db_path)])

        assert result.exit_code == 0
        stages_run = [call.args[1] for call in mock_run.call_args_list]
        assert "ml_classify" not in stages_run

    @pytest.mark.cli
    def test_run_includes_ml_classify_with_flag(self, runner: CliRunner, tmp_dir: Path) -> None:
        db_path, _ = _setup_db_with_track(tmp_dir)
        with patch("musesleuth.pipeline.run_stage_for_job", return_value=True) as mock_run:
            result = runner.invoke(cli, ["run", "--db", str(db_path), "--include-ml-classify"])

        assert result.exit_code == 0
        stages_run = [call.args[1] for call in mock_run.call_args_list]
        assert "ml_classify" in stages_run

    @pytest.mark.cli
    def test_run_forwards_path_prefix_maps(self, runner: CliRunner, tmp_dir: Path) -> None:
        db_path, _ = _setup_db_with_track(tmp_dir)
        with patch("musesleuth.cli.PipelineOrchestrator") as mock_orchestrator:
            orch_instance = mock_orchestrator.return_value
            orch_instance.process_next.side_effect = [False]
            result = runner.invoke(
                cli,
                [
                    "run",
                    "--db",
                    str(db_path),
                    "--stage",
                    "import",
                    "--path-prefix-map",
                    "I:\\Music=Y:\\",
                ],
            )

        assert result.exit_code == 0, result.output
        assert mock_orchestrator.call_args.kwargs["path_prefix_maps"] == (("I:\\Music", "Y:\\"),)

    @pytest.mark.cli
    def test_run_rejects_invalid_path_prefix_map(self, runner: CliRunner, tmp_dir: Path) -> None:
        db_path, _ = _setup_db_with_track(tmp_dir)
        result = runner.invoke(
            cli,
            [
                "run",
                "--db",
                str(db_path),
                "--path-prefix-map",
                "I:\\Music->Y:\\",
            ],
        )

        assert result.exit_code != 0
        assert "Invalid --path-prefix-map" in result.output

    @pytest.mark.cli
    def test_run_rejects_quoted_path_prefix_map(self, runner: CliRunner, tmp_dir: Path) -> None:
        db_path, _ = _setup_db_with_track(tmp_dir)
        result = runner.invoke(
            cli,
            [
                "run",
                "--db",
                str(db_path),
                "--path-prefix-map",
                'I:\\Music=Y:\\" -v',
            ],
        )

        assert result.exit_code != 0
        assert "contains a quote character" in result.output


class TestWritebackCommand:
    """Tests for `musicmeta writeback` CLI."""

    @pytest.mark.cli
    def test_writeback_missing_db_fails(self, runner: CliRunner, tmp_dir: Path) -> None:
        result = runner.invoke(cli, ["writeback", "--db", str(tmp_dir / "nope.db")])
        assert result.exit_code != 0

    @pytest.mark.cli
    def test_writeback_shows_summary(self, runner: CliRunner, tmp_dir: Path) -> None:
        db_path, mid = _setup_db_with_track(tmp_dir)
        # No actual audio files to write to, but command should still run
        result = runner.invoke(cli, ["writeback", "--db", str(db_path)])
        assert result.exit_code == 0


class TestScanRelocateExcludeDirs:
    """Tests for --exclude-dir forwarding in scan/relocate CLI commands."""

    @pytest.mark.cli
    def test_scan_forwards_exclude_dir(self, runner: CliRunner, tmp_dir: Path) -> None:
        db_path = tmp_dir / "test.db"
        with patch("musesleuth.scanner.scan_directory") as mock_scan:
            mock_scan.return_value = ScanResult()
            result = runner.invoke(
                cli,
                [
                    "scan",
                    str(tmp_dir),
                    "--db",
                    str(db_path),
                    "--exclude-dir",
                    "electronci",
                ],
            )

        assert result.exit_code == 0
        assert mock_scan.call_args.kwargs["exclude_dirs"] == ("electronci",)

    @pytest.mark.cli
    def test_relocate_forwards_exclude_dir(self, runner: CliRunner, tmp_dir: Path) -> None:
        db_path, _ = _setup_db_with_track(tmp_dir)
        with patch("musesleuth.scanner.relocate_directory") as mock_relocate:
            mock_relocate.return_value = RelocateResult()
            result = runner.invoke(
                cli,
                [
                    "relocate",
                    str(tmp_dir),
                    "--db",
                    str(db_path),
                    "--exclude-dir",
                    "electronci",
                ],
            )

        assert result.exit_code == 0
        assert mock_relocate.call_args.kwargs["exclude_dirs"] == ("electronci",)
