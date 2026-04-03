"""Resumable per-track per-stage job queue backed by SQLite."""
from __future__ import annotations

import sqlite3
from typing import Optional


class JobStatus:
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"


STAGES = [
    "import",
    "probe",
    "analyze",
    "ml_classify",
    "fingerprint_match",
    "enrich",
    "derive_signals",
    "writeback",
]


def create_jobs_for_track(
    conn: sqlite3.Connection,
    metadata_id: str,
) -> None:
    """Seed one job per stage for a track. Idempotent via INSERT OR IGNORE."""
    for stage in STAGES:
        conn.execute(
            """
            INSERT OR IGNORE INTO jobs (metadata_id, stage, status)
            VALUES (?, ?, ?)
            """,
            (metadata_id, stage, JobStatus.PENDING),
        )
    conn.commit()


def claim_job(
    conn: sqlite3.Connection,
    stage: str,
    worker_id: str,
) -> Optional[sqlite3.Row]:
    """Claim the next pending job for a stage. Returns the job row or None."""
    cursor = conn.execute(
        """
        SELECT id, metadata_id, stage, status, attempts
        FROM jobs
        WHERE stage = ? AND status = ?
        ORDER BY id
        LIMIT 1
        """,
        (stage, JobStatus.PENDING),
    )
    row = cursor.fetchone()
    if row is None:
        return None

    conn.execute(
        """
        UPDATE jobs
        SET status = ?, worker_id = ?, claimed_at = datetime('now'),
            updated_at = datetime('now')
        WHERE id = ?
        """,
        (JobStatus.RUNNING, worker_id, row["id"]),
    )
    conn.commit()
    return row


def release_job(conn: sqlite3.Connection, job_id: int) -> None:
    """Mark a job as done."""
    conn.execute(
        """
        UPDATE jobs
        SET status = ?, completed_at = datetime('now'), updated_at = datetime('now')
        WHERE id = ?
        """,
        (JobStatus.DONE, job_id),
    )
    conn.commit()


def fail_job(
    conn: sqlite3.Connection,
    job_id: int,
    error: str,
) -> None:
    """Mark a job as failed with an error message. Increments attempts."""
    conn.execute(
        """
        UPDATE jobs
        SET status = ?, last_error = ?, attempts = attempts + 1,
            updated_at = datetime('now')
        WHERE id = ?
        """,
        (JobStatus.FAILED, error, job_id),
    )
    conn.commit()


def expire_stale_leases(
    conn: sqlite3.Connection,
    max_age_seconds: int = 300,
) -> int:
    """Reset running jobs whose lease has expired back to pending.

    Returns the number of jobs expired.
    """
    cursor = conn.execute(
        """
        UPDATE jobs
        SET status = ?, worker_id = NULL, updated_at = datetime('now')
        WHERE status = ?
          AND claimed_at < datetime('now', ? || ' seconds')
        """,
        (JobStatus.PENDING, JobStatus.RUNNING, str(-max_age_seconds)),
    )
    conn.commit()
    return cursor.rowcount


def retry_failed_jobs(
    conn: sqlite3.Connection,
    stage: str | None = None,
    max_attempts: int = 0,
) -> int:
    """Reset failed jobs back to pending for retry.

    If stage is given, only retry that stage. If max_attempts > 0, only retry
    jobs with fewer than max_attempts. Returns the number of jobs retried.
    """
    if stage:
        if max_attempts > 0:
            cursor = conn.execute(
                """
                UPDATE jobs SET status = ?, last_error = NULL, updated_at = datetime('now')
                WHERE status = ? AND stage = ? AND attempts < ?
                """,
                (JobStatus.PENDING, JobStatus.FAILED, stage, max_attempts),
            )
        else:
            cursor = conn.execute(
                """
                UPDATE jobs SET status = ?, last_error = NULL, updated_at = datetime('now')
                WHERE status = ? AND stage = ?
                """,
                (JobStatus.PENDING, JobStatus.FAILED, stage),
            )
    else:
        if max_attempts > 0:
            cursor = conn.execute(
                """
                UPDATE jobs SET status = ?, last_error = NULL, updated_at = datetime('now')
                WHERE status = ? AND attempts < ?
                """,
                (JobStatus.PENDING, JobStatus.FAILED, max_attempts),
            )
        else:
            cursor = conn.execute(
                """
                UPDATE jobs SET status = ?, last_error = NULL, updated_at = datetime('now')
                WHERE status = ?
                """,
                (JobStatus.PENDING, JobStatus.FAILED),
            )
    conn.commit()
    return cursor.rowcount


def refresh_incomplete_enrich(conn: sqlite3.Connection) -> int:
    """Reset done enrich jobs to pending where enrichment data has gaps.

    Targets tracks missing any of: Last.fm wiki/bio, genre tags,
    MusicBrainz recording/artist IDs, or artist active_years.
    Returns the number of jobs reset.
    """
    cursor = conn.execute(
        """
        UPDATE jobs
        SET status = ?, updated_at = datetime('now')
        WHERE stage = 'enrich' AND status = ?
          AND metadata_id IN (
              SELECT t.metadata_id
              FROM tracks t
              LEFT JOIN track_stats ts
                ON ts.metadata_id = t.metadata_id AND ts.source = 'lastfm'
              LEFT JOIN artist_stats lfm_ast
                ON lfm_ast.metadata_id = t.metadata_id AND lfm_ast.source = 'lastfm'
              LEFT JOIN artist_stats mb_ast
                ON mb_ast.metadata_id = t.metadata_id AND mb_ast.source = 'musicbrainz'
              LEFT JOIN (
                  SELECT metadata_id, COUNT(*) AS cnt
                  FROM genres_tags GROUP BY metadata_id
              ) gt ON gt.metadata_id = t.metadata_id
              LEFT JOIN (
                  SELECT metadata_id, COUNT(*) AS cnt
                  FROM external_ids WHERE source = 'musicbrainz'
                  GROUP BY metadata_id
              ) ei_rec ON ei_rec.metadata_id = t.metadata_id
              LEFT JOIN (
                  SELECT metadata_id, COUNT(*) AS cnt
                  FROM external_ids WHERE source = 'musicbrainz_artist'
                  GROUP BY metadata_id
              ) ei_art ON ei_art.metadata_id = t.metadata_id
              WHERE ts.metadata_id IS NULL
                 OR ts.wiki IS NULL OR ts.wiki = ''
                 OR lfm_ast.metadata_id IS NULL
                 OR lfm_ast.bio IS NULL OR lfm_ast.bio = ''
                 OR gt.cnt IS NULL
                 OR ei_rec.cnt IS NULL
                 OR ei_art.cnt IS NULL
                 OR mb_ast.active_years IS NULL
          )
        """,
        (JobStatus.PENDING, JobStatus.DONE),
    )
    conn.commit()
    return cursor.rowcount


def refresh_stage(conn: sqlite3.Connection, stage: str) -> int:
    """Reset all done jobs for a stage back to pending.

    Use for stages like derive_signals or writeback that should be re-run
    after upstream data has changed. Returns the number of jobs reset.
    """
    cursor = conn.execute(
        """
        UPDATE jobs
        SET status = ?, updated_at = datetime('now')
        WHERE stage = ? AND status = ?
        """,
        (JobStatus.PENDING, stage, JobStatus.DONE),
    )
    conn.commit()
    return cursor.rowcount


def get_job_counts(conn: sqlite3.Connection) -> dict[str, dict[str, int]]:
    """Return job counts grouped by stage and status."""
    cursor = conn.execute(
        """
        SELECT stage, status, COUNT(*) as cnt
        FROM jobs
        GROUP BY stage, status
        """
    )
    result: dict[str, dict[str, int]] = {}
    for row in cursor.fetchall():
        stage = row["stage"]
        if stage not in result:
            result[stage] = {}
        result[stage][row["status"]] = row["cnt"]
    return result