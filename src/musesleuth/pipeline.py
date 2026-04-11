"""Pipeline orchestrator -- sequences stages via the job queue."""
from __future__ import annotations

import logging
import sqlite3
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

log = logging.getLogger(__name__)

from musesleuth.job_queue import (
    STAGES,
    JobStatus,
    claim_job,
    release_job,
    fail_job,
)


def _rewrite_path_prefix(
    file_path: str,
    path_prefix_maps: Tuple[Tuple[str, str], ...] | None = None,
) -> str:
    if not file_path or not path_prefix_maps:
        return file_path
    for source_prefix, target_prefix in sorted(path_prefix_maps, key=lambda item: len(item[0]), reverse=True):
        if file_path.startswith(source_prefix):
            suffix = file_path[len(source_prefix):]
            if target_prefix.endswith(("\\", "/")) and suffix.startswith(("\\", "/")):
                suffix = suffix[1:]
            return target_prefix + suffix
    return file_path


def _resolve_path(
    file_path: str,
    library_root: Optional[Path] = None,
    path_prefix_maps: Tuple[Tuple[str, str], ...] | None = None,
) -> str:
    """Resolve a potentially relative file path against the library root.

    If file_path is already absolute and exists, return as-is.
    Otherwise try library_root / file_path.
    """
    if not file_path:
        return file_path
    rewritten_path = _rewrite_path_prefix(file_path, path_prefix_maps)
    if rewritten_path != file_path:
        rewritten = Path(rewritten_path)
        if rewritten.is_absolute():
            return rewritten_path
    p = Path(file_path)
    if p.is_absolute() and p.exists():
        return file_path
    if library_root is not None:
        resolved = library_root / rewritten_path
        if resolved.exists():
            return str(resolved)
    return rewritten_path


def _run_stage_worker(args: tuple) -> tuple:
    """Worker function for parallel stage processing (picklable for ProcessPoolExecutor)."""
    job_id, metadata_id, stage, db_path, probe_attempt_repair = args[:5]
    library_root_str = args[5] if len(args) > 5 else None
    path_prefix_maps = args[6] if len(args) > 6 else None
    _log = logging.getLogger(__name__)

    from musesleuth.pipeline import run_stage_for_job, _resolve_path

    lib_root = Path(library_root_str) if library_root_str else None

    _log.debug("[proc-worker] job=%d mid=%s stage=%s start", job_id, metadata_id, stage)
    worker_conn = sqlite3.connect(db_path, timeout=30)
    worker_conn.execute("PRAGMA busy_timeout=30000")
    worker_conn.row_factory = sqlite3.Row

    try:
        track = worker_conn.execute(
            "SELECT full_path FROM tracks WHERE metadata_id = ?",
            (metadata_id,),
        ).fetchone()
        file_path = track["full_path"] if track else ""
        file_path = _resolve_path(file_path, lib_root, path_prefix_maps)

        run_stage_for_job(
            worker_conn,
            stage,
            metadata_id,
            file_path,
            probe_attempt_repair=probe_attempt_repair,
        )
        _log.debug("[proc-worker] job=%d mid=%s stage=%s ok", job_id, metadata_id, stage)
        return (job_id, True, None)

    except Exception as exc:
        _log.warning("[proc-worker] job=%d mid=%s stage=%s FAILED: %s", job_id, metadata_id, stage, exc)
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
        library_root: Optional[Path] = None,
        path_prefix_maps: Tuple[Tuple[str, str], ...] | None = None,
    ) -> None:
        self._conn = conn
        self._worker_id = worker_id
        self._probe_attempt_repair = probe_attempt_repair
        self._library_root = library_root
        self._path_prefix_maps = path_prefix_maps

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
        file_path = _resolve_path(file_path, self._library_root, self._path_prefix_maps)

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

        Uses a streaming producer-consumer pattern: jobs are claimed in
        small batches as workers become available, with real-time progress.

        Args:
            stage: The pipeline stage to process.
            max_workers: Number of parallel workers (default: 4).
            db_path: Path to the SQLite database for creating worker connections.

        Returns:
            The total number of jobs processed.
        """
        import sys
        import time

        if stage not in STAGES:
            return 0

        # Count total pending for progress display
        total_pending = self._conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE stage = ? AND status = ?",
            (stage, JobStatus.PENDING),
        ).fetchone()[0]

        if total_pending == 0:
            log.info("%s: 0 pending jobs, nothing to do", stage)
            return 0

        # Resolve db_path for worker connections
        if db_path is None:
            try:
                row = self._conn.execute("PRAGMA database_list").fetchone()
                if row and row[2]:
                    db_path = row[2]
            except Exception:
                pass

        if db_path is None:
            # Fall back to sequential processing
            log.warning("%s: no db_path resolved, falling back to sequential processing", stage)
            completed = 0
            while True:
                job = claim_job(self._conn, stage, self._worker_id)
                if job is None:
                    break
                metadata_id = job["metadata_id"]
                track = self._conn.execute(
                    "SELECT full_path FROM tracks WHERE metadata_id = ?",
                    (metadata_id,),
                ).fetchone()
                file_path = track["full_path"] if track else ""
                file_path = _resolve_path(file_path, self._library_root, self._path_prefix_maps)
                try:
                    run_stage_for_job(
                        self._conn, stage, metadata_id, file_path,
                        probe_attempt_repair=self._probe_attempt_repair,
                    )
                    release_job(self._conn, job["id"])
                except Exception as exc:
                    fail_job(self._conn, job["id"], str(exc))
                completed += 1
                sys.stderr.write(f"\r  {stage}: {completed}/{total_pending}")
                sys.stderr.flush()
            sys.stderr.write("\n")
            return completed

        # Batch-claim helper: claims up to `n` pending jobs in one round-trip
        def _claim_batch(n: int) -> list[sqlite3.Row]:
            rows = self._conn.execute(
                """
                SELECT id, metadata_id, stage, status, attempts
                FROM jobs
                WHERE stage = ? AND status = ?
                ORDER BY id
                LIMIT ?
                """,
                (stage, JobStatus.PENDING, n),
            ).fetchall()
            if not rows:
                return []
            ids = [r["id"] for r in rows]
            placeholders = ",".join("?" for _ in ids)
            self._conn.execute(
                f"""
                UPDATE jobs
                SET status = ?, worker_id = ?, claimed_at = datetime('now'),
                    updated_at = datetime('now')
                WHERE id IN ({placeholders})
                """,
                [JobStatus.RUNNING, self._worker_id] + ids,
            )
            self._conn.commit()
            return rows

        # Choose executor type
        cpu_bound_stages = (
            "embed",
            "structure",
            "beatgrid",
        )

        use_processes = stage in cpu_bound_stages

        ExecutorClass = ProcessPoolExecutor if use_processes else ThreadPoolExecutor
        log.info(
            "%s: %d pending, %d workers, executor=%s",
            stage, total_pending, max_workers,
            "ProcessPool" if use_processes else "ThreadPool",
        )

        probe_repair = self._probe_attempt_repair
        _db_path = db_path  # capture for closure
        _library_root = self._library_root  # capture for closure
        _path_prefix_maps = self._path_prefix_maps  # capture for closure

        if use_processes:
            from musesleuth.pipeline import _run_stage_worker

        def _make_thread_worker(job: sqlite3.Row) -> tuple[int, bool, Optional[str]]:
            """Thread worker: each thread gets its own SQLite connection."""
            import threading
            metadata_id = job["metadata_id"]
            job_id = job["id"]
            tid = threading.current_thread().name
            log.debug("[%s] job=%d mid=%s start", tid, job_id, metadata_id)
            worker_conn = sqlite3.connect(_db_path, timeout=60)
            worker_conn.execute("PRAGMA busy_timeout=60000")
            worker_conn.row_factory = sqlite3.Row
            try:
                track = worker_conn.execute(
                    "SELECT full_path FROM tracks WHERE metadata_id = ?",
                    (metadata_id,),
                ).fetchone()
                file_path = track["full_path"] if track else ""
                file_path = _resolve_path(file_path, _library_root, _path_prefix_maps)
                log.debug("[%s] job=%d running %s on %s", tid, job_id, stage, file_path)
                run_stage_for_job(
                    worker_conn, stage, metadata_id, file_path,
                    probe_attempt_repair=probe_repair,
                )
                log.debug("[%s] job=%d ok", tid, job_id)
                return (job_id, True, None)
            except Exception as exc:
                log.warning("[%s] job=%d FAILED: %s", tid, job_id, exc)
                return (job_id, False, str(exc))
            finally:
                worker_conn.close()

        completed = 0
        failed = 0
        t0 = time.monotonic()

        with ExecutorClass(max_workers=max_workers) as executor:
            futures: dict = {}
            exhausted = False

            while True:
                # Fill worker slots with new jobs
                while not exhausted and len(futures) < max_workers:
                    batch = _claim_batch(max_workers - len(futures))
                    if not batch:
                        exhausted = True
                        break
                    for job in batch:
                        if use_processes:
                            lib_root_str = str(_library_root) if _library_root else None
                            path_maps = tuple(_path_prefix_maps or ())
                            item = (job["id"], job["metadata_id"], stage,
                                    _db_path, probe_repair, lib_root_str, path_maps)
                            fut = executor.submit(_run_stage_worker, item)
                        else:
                            fut = executor.submit(_make_thread_worker, job)
                        futures[fut] = job["id"]
                        log.debug("job=%d mid=%s stage=%s claimed", job["id"], job["metadata_id"], stage)

                if not futures:
                    break

                # Wait for at least one future to finish
                done_set = set()
                for fut in as_completed(futures):
                    done_set.add(fut)
                    job_id, success, error = fut.result()
                    if success:
                        release_job(self._conn, job_id)
                        completed += 1
                        log.debug("job=%d ok", job_id)
                    else:
                        fail_job(self._conn, job_id, error or "Unknown error")
                        failed += 1
                        log.warning("job=%d FAILED: %s", job_id, error or "Unknown error")

                    processed = completed + failed
                    elapsed = time.monotonic() - t0
                    rate = processed / elapsed if elapsed > 0 else 0
                    sys.stderr.write(
                        f"\r  {stage}: {processed}/{total_pending}"
                        f"  ({completed} ok, {failed} err)"
                        f"  [{rate:.1f} jobs/s]"
                    )
                    sys.stderr.flush()

                    # Break out to refill worker slots
                    break

                for fut in done_set:
                    del futures[fut]

        sys.stderr.write("\n")
        elapsed = time.monotonic() - t0
        log.info(
            "%s: finished %d jobs in %.1fs (%d ok, %d failed, %.1f jobs/s)",
            stage, completed + failed, elapsed, completed, failed,
            (completed + failed) / elapsed if elapsed > 0 else 0,
        )
        return completed + failed


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

    if stage == "loudness":
        from musesleuth.loudness import run_loudness_for_track
        success = run_loudness_for_track(conn, metadata_id, file_path)
        if not success:
            raise RuntimeError("Loudness analysis failed")
        return True

    if stage == "timbre":
        from musesleuth.timbre import run_timbre_for_track
        success = run_timbre_for_track(conn, metadata_id, file_path)
        if not success:
            raise RuntimeError("Timbre analysis failed")
        return True

    if stage == "embed":
        from musesleuth.audio_embeddings import run_embedding_for_track
        success = run_embedding_for_track(conn, metadata_id, file_path)
        if not success:
            raise RuntimeError("Embedding extraction failed")
        return True

    if stage == "structure":
        from musesleuth.structure import run_structure_for_track
        success = run_structure_for_track(conn, metadata_id, file_path)
        if not success:
            raise RuntimeError("Structure analysis failed")
        return True

    if stage == "beatgrid":
        from musesleuth.beatgrid import run_beatgrid_for_track
        success = run_beatgrid_for_track(conn, metadata_id, file_path)
        if not success:
            raise RuntimeError("Beat grid analysis failed")
        return True

    # Unknown stage -- just pass through
    return True