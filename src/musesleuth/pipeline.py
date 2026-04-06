"""Pipeline orchestrator -- sequences stages via the job queue."""
from __future__ import annotations

import sqlite3
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from musesleuth.job_queue import (
    STAGES,
    JobStatus,
    claim_job,
    release_job,
    fail_job,
)


def _run_stage_worker(args: tuple) -> tuple:
    """Worker function for parallel stage processing (picklable for ProcessPoolExecutor)."""
    job_id, metadata_id, stage, db_path, probe_attempt_repair = args

    from musesleuth.pipeline import run_stage_for_job

    worker_conn = sqlite3.connect(db_path, timeout=30)
    worker_conn.execute("PRAGMA busy_timeout=30000")
    worker_conn.row_factory = sqlite3.Row

    try:
        track = worker_conn.execute(
            "SELECT full_path FROM tracks WHERE metadata_id = ?",
            (metadata_id,),
        ).fetchone()
        file_path = track["full_path"] if track else ""

        run_stage_for_job(
            worker_conn,
            stage,
            metadata_id,
            file_path,
            probe_attempt_repair=probe_attempt_repair,
        )
        return (job_id, True, None)

    except Exception as exc:
        return (job_id, False, str(exc))
    finally:
        worker_conn.close()


@dataclass
class PipelineStats:
    """Snapshot of pipeline progress."""

    total_tracks: int = 0
    total_jobs: int = 0
    pending_jobs: int = 0
    running_jobs: int = 0
    done_jobs: int = 0
    failed_jobs: int = 0
    completion_pct: float = 0.0
    stage_counts: dict[str, dict[str, int]] | None = None


class PipelineOrchestrator:
    """Drives tracks through all pipeline stages via the job queue."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        worker_id: str = "default",
        probe_attempt_repair: bool = False,
    ) -> None:
        self._conn = conn
        self._worker_id = worker_id
        self._probe_attempt_repair = probe_attempt_repair

    def get_stats(self) -> PipelineStats:
        """Return current pipeline statistics."""
        total_tracks = self._conn.execute(
            "SELECT COUNT(*) as cnt FROM tracks"
        ).fetchone()["cnt"]

        rows = self._conn.execute(
            "SELECT status, COUNT(*) as cnt FROM jobs GROUP BY status"
        ).fetchall()

        counts = {r["status"]: r["cnt"] for r in rows}
        total = sum(counts.values())
        done = counts.get(JobStatus.DONE, 0)

        return PipelineStats(
            total_tracks=total_tracks,
            total_jobs=total,
            pending_jobs=counts.get(JobStatus.PENDING, 0),
            running_jobs=counts.get(JobStatus.RUNNING, 0),
            done_jobs=done,
            failed_jobs=counts.get(JobStatus.FAILED, 0),
            completion_pct=(done / total * 100.0) if total > 0 else 0.0,
        )

    def process_next(self, stage: str) -> bool:
        """Claim and process the next pending job for a stage.

        Returns True if a job was processed (success or failure),
        False if no jobs were available.
        """
        job = claim_job(self._conn, stage, self._worker_id)
        if job is None:
            return False

        metadata_id = job["metadata_id"]

        # Get file path
        track = self._conn.execute(
            "SELECT full_path FROM tracks WHERE metadata_id = ?",
            (metadata_id,),
        ).fetchone()
        file_path = track["full_path"] if track else ""

        try:
            run_stage_for_job(
                self._conn,
                stage,
                metadata_id,
                file_path,
                probe_attempt_repair=self._probe_attempt_repair,
            )
            release_job(self._conn, job["id"])
        except Exception as exc:
            fail_job(self._conn, job["id"], str(exc))

        return True

    def run_all_pending(self) -> int:
        """Process all pending jobs across all stages in order.

        Returns the total number of jobs processed.
        """
        total = 0
        for stage in STAGES:
            while self.process_next(stage):
                total += 1
        return total

    def process_stage_parallel(
        self,
        stage: str,
        max_workers: int = 4,
        db_path: Optional[str] = None,
    ) -> int:
        """Process all pending jobs for a stage in parallel.

        Args:
            stage: The pipeline stage to process.
            max_workers: Number of parallel workers (default: 4).
            db_path: Path to the SQLite database for creating worker connections.

        Returns:
            The total number of jobs processed.
        """
        if stage not in STAGES:
            return 0

        # Collect all pending jobs for this stage
        jobs = []
        while True:
            job = claim_job(self._conn, stage, self._worker_id)
            if job is None:
                break
            jobs.append(job)

        if not jobs:
            return 0

        total = 0

        # If db_path is not provided, try to get it from the connection
        if db_path is None:
            # Try to get database path from connection
            try:
                row = self._conn.execute("PRAGMA database_list").fetchone()
                if row and row[2]:
                    db_path = row[2]
            except Exception:
                pass

        if db_path is None:
            # Fall back to sequential processing if we can't get the db path
            total = 0
            for job in jobs:
                metadata_id = job["metadata_id"]
                track = self._conn.execute(
                    "SELECT full_path FROM tracks WHERE metadata_id = ?",
                    (metadata_id,),
                ).fetchone()
                file_path = track["full_path"] if track else ""

                try:
                    run_stage_for_job(
                        self._conn,
                        stage,
                        metadata_id,
                        file_path,
                        probe_attempt_repair=self._probe_attempt_repair,
                    )
                    release_job(self._conn, job["id"])
                except Exception as exc:
                    fail_job(self._conn, job["id"], str(exc))
                total += 1
            return total

        # Process jobs in parallel
        # Use ProcessPoolExecutor for CPU-bound stages, ThreadPoolExecutor for I/O-bound
        cpu_bound_stages = ("probe", "analyze", "ml_classify", "fingerprint_match", "derive_signals")
        use_processes = stage in cpu_bound_stages

        if use_processes:
            # For ProcessPoolExecutor, use module-level picklable function
            from musesleuth.pipeline import _run_stage_worker
            work_items = [
                (job["id"], job["metadata_id"], stage, db_path, self._probe_attempt_repair)
                for job in jobs
            ]

            with ProcessPoolExecutor(max_workers=max_workers) as executor:
                futures = {executor.submit(_run_stage_worker, item): item[0] for item in work_items}
                for future in as_completed(futures):
                    job_id, success, error = future.result()
                    if success:
                        release_job(self._conn, job_id)
                    else:
                        fail_job(self._conn, job_id, error or "Unknown error")
                    total += 1
        else:
            # For ThreadPoolExecutor, use inline function
            def process_job(job: sqlite3.Row) -> tuple[int, bool, Optional[str]]:
                metadata_id = job["metadata_id"]
                job_id = job["id"]
                worker_conn = sqlite3.connect(db_path, timeout=30)
                worker_conn.execute("PRAGMA busy_timeout=30000")
                worker_conn.row_factory = sqlite3.Row
                try:
                    track = worker_conn.execute(
                        "SELECT full_path FROM tracks WHERE metadata_id = ?",
                        (metadata_id,),
                    ).fetchone()
                    file_path = track["full_path"] if track else ""
                    run_stage_for_job(
                        worker_conn,
                        stage,
                        metadata_id,
                        file_path,
                        probe_attempt_repair=self._probe_attempt_repair,
                    )
                    return (job_id, True, None)
                except Exception as exc:
                    return (job_id, False, str(exc))
                finally:
                    worker_conn.close()

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {executor.submit(process_job, job): job for job in jobs}
                for future in as_completed(futures):
                    job_id, success, error = future.result()
                    if success:
                        release_job(self._conn, job_id)
                    else:
                        fail_job(self._conn, job_id, error or "Unknown error")
                    total += 1

        return total


def run_stage_for_job(
    conn: sqlite3.Connection,
    stage: str,
    metadata_id: str,
    file_path: str,
    probe_attempt_repair: bool = False,
) -> bool:
    """Dispatch a job to the appropriate stage handler.

    Returns True on success. Raises on failure.
    """
    if stage == "import":
        return True  # already handled by CLI import

    if stage == "probe":
        from musesleuth.probe_runner import run_probe_for_track
        result = run_probe_for_track(
            conn,
            metadata_id,
            file_path,
            attempt_repair=probe_attempt_repair,
        )
        if not result.success:
            raise RuntimeError(result.error or "Probe failed")
        return True

    if stage == "analyze":
        from musesleuth.analyze_runner import run_analyze_for_track
        result = run_analyze_for_track(conn, metadata_id, file_path)
        if not result.success:
            raise RuntimeError(result.error or "Analyze failed")
        return True

    if stage == "ml_classify":
        from musesleuth.ml_runner import run_ml_classify_for_track
        result = run_ml_classify_for_track(conn, metadata_id, file_path)
        if not result.success:
            raise RuntimeError(result.error or "ML classification failed")
        return True

    if stage == "fingerprint_match":
        from musesleuth.matcher import run_match_for_track
        result = run_match_for_track(conn, metadata_id)
        if not result.success:
            raise RuntimeError(result.error or "Match failed")
        return True

    if stage == "enrich":
        from musesleuth.enrich_runner import run_enrich_for_track
        result = run_enrich_for_track(conn, metadata_id)
        if not result.success:
            raise RuntimeError(result.error or "Enrich failed")
        return True

    if stage == "derive_signals":
        from musesleuth.signals import run_derive_signals_for_track
        run_derive_signals_for_track(conn, metadata_id)
        return True

    if stage == "writeback":
        from musesleuth.writeback_runner import run_writeback_for_track
        result = run_writeback_for_track(conn, metadata_id)
        if not result.success:
            raise RuntimeError(result.error or "Writeback failed")
        return True

    # Unknown stage -- just pass through
    return True