"""Reset failed analyze jobs caused by the historical analyze_energy import error."""
from __future__ import annotations

import argparse

from musesleuth.db import get_connection

ERROR_TEXT = "cannot import name 'analyze_energy' from 'musesleuth.bpm_analyzer'"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="music.db")
    parser.add_argument("--limit", type=int, default=0, help="0 means reset all matching jobs")
    args = parser.parse_args()

    conn = get_connection(args.db)
    matches = conn.execute(
        """
        SELECT id, metadata_id
        FROM jobs
        WHERE stage = 'analyze'
          AND status = 'failed'
          AND last_error LIKE ?
        ORDER BY id
        """,
        (f"%{ERROR_TEXT}%",),
    ).fetchall()

    total = len(matches)
    if args.limit > 0:
        matches = matches[: args.limit]

    ids = [row["id"] for row in matches]
    print(f"matching failed analyze jobs: {total}")
    print(f"resetting: {len(ids)}")

    if ids:
        placeholders = ",".join("?" for _ in ids)
        conn.execute(
            f"""
            UPDATE jobs
            SET status = 'pending',
                worker_id = NULL,
                last_error = NULL,
                claimed_at = NULL,
                completed_at = NULL,
                updated_at = datetime('now')
            WHERE id IN ({placeholders})
            """,
            ids,
        )
        conn.commit()

    remaining_failed = conn.execute(
        """
        SELECT count(*) AS c
        FROM jobs
        WHERE stage = 'analyze' AND status = 'failed'
        """
    ).fetchone()["c"]
    pending = conn.execute(
        """
        SELECT count(*) AS c
        FROM jobs
        WHERE stage = 'analyze' AND status = 'pending'
        """
    ).fetchone()["c"]

    print(f"remaining analyze failed: {remaining_failed}")
    print(f"analyze pending: {pending}")
    conn.close()


if __name__ == "__main__":
    main()
