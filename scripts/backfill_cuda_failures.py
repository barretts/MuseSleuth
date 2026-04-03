"""Retry ml_classify jobs that failed with CUDA errors, single-threaded.

Runs one track at a time in the main process to avoid the GPU context
corruption that ProcessPoolExecutor causes on Windows.

Usage: python scripts/backfill_cuda_failures.py [--db music_new.db] [--limit N]
"""
import argparse
import sqlite3
import sys
import time

sys.path.insert(0, "src")

from musesleuth.ml_runner import run_ml_classify_for_track


def main():
    parser = argparse.ArgumentParser(description="Retry CUDA-failed ml_classify jobs")
    parser.add_argument("--db", default="music_new.db", help="Database path")
    parser.add_argument("--limit", type=int, default=0, help="Max tracks (0=all)")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")

    query = """
        SELECT j.metadata_id, t.full_path
        FROM jobs j
        JOIN tracks t ON t.metadata_id = j.metadata_id
        WHERE j.stage = 'ml_classify' AND j.status = 'failed'
        ORDER BY j.metadata_id
    """
    if args.limit:
        query = query.rstrip() + f"\nLIMIT {args.limit}"

    rows = conn.execute(query).fetchall()
    total = len(rows)
    print(f"Found {total} failed ml_classify jobs to retry (single-threaded)")

    if total == 0:
        conn.close()
        return

    ok = 0
    errors = 0
    t0 = time.time()

    for i, row in enumerate(rows, 1):
        mid = row["metadata_id"]
        fpath = row["full_path"]

        try:
            result = run_ml_classify_for_track(conn, mid, fpath)
            if result.success:
                conn.execute(
                    "UPDATE jobs SET status='done', last_error=NULL, completed_at=datetime('now') "
                    "WHERE metadata_id=? AND stage='ml_classify'",
                    (mid,),
                )
                conn.commit()
                ok += 1
            else:
                conn.execute(
                    "UPDATE jobs SET last_error=? WHERE metadata_id=? AND stage='ml_classify'",
                    (result.error or "unknown", mid),
                )
                conn.commit()
                errors += 1
        except Exception as e:
            conn.execute(
                "UPDATE jobs SET last_error=? WHERE metadata_id=? AND stage='ml_classify'",
                (str(e)[:500], mid),
            )
            conn.commit()
            errors += 1

        if i % 25 == 0 or i == total:
            elapsed = time.time() - t0
            rate = i / elapsed if elapsed > 0 else 0
            eta = (total - i) / rate if rate > 0 else 0
            print(
                f"  [{i}/{total}] ok={ok} err={errors} "
                f"({rate:.1f}/s, ETA {eta / 60:.0f}m)"
            )

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed / 60:.1f}m — ok={ok}, errors={errors}")
    conn.close()


if __name__ == "__main__":
    main()
