"""Backfill integrity checks + repair attempts for all existing tracks.

Run alongside ml_classify -- no conflicts (different tables).
Usage: python scripts/backfill_integrity.py [--db music_new.db] [--limit N] [--workers 4]
"""
import argparse
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

sys.path.insert(0, "src")

from musesleuth.probe import check_integrity, repair_remux


class Stats:
    def __init__(self):
        self.ok = 0
        self.repaired = 0
        self.bad = 0
        self.errors = 0
        self.done = 0
        self._lock = Lock()

    def record(self, *, ok: bool, repaired: bool = False, error: bool = False):
        with self._lock:
            self.done += 1
            if error:
                self.errors += 1
            elif repaired:
                self.repaired += 1
            elif ok:
                self.ok += 1
            else:
                self.bad += 1


def process_track(db_path: str, mid: str, fpath: str) -> tuple[str, bool, bool, str | None]:
    """Check integrity and attempt repair. Returns (mid, ok, was_repaired, error_json)."""
    try:
        integrity = check_integrity(fpath)
        was_repaired = False

        if not integrity.ok:
            if repair_remux(fpath):
                recheck = check_integrity(fpath)
                if recheck.ok:
                    integrity = recheck
                    was_repaired = True

        return mid, integrity.ok, was_repaired, integrity.to_json()
    except Exception as e:
        return mid, False, False, f'["check_failed: {str(e)[:100]}"]'


def main():
    parser = argparse.ArgumentParser(description="Backfill audio integrity checks")
    parser.add_argument("--db", default="music_new.db", help="Database path")
    parser.add_argument("--limit", type=int, default=0, help="Max tracks to process (0=all)")
    parser.add_argument("--workers", type=int, default=4, help="Concurrent workers (default 4)")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")

    query = """
        SELECT t.metadata_id, t.full_path
        FROM tracks t
        JOIN technical_features tf ON t.metadata_id = tf.metadata_id
        WHERE tf.integrity_ok IS NULL
           OR (tf.integrity_ok = 0 AND tf.was_repaired = 0
               AND (tf.integrity_errors IS NULL OR tf.integrity_errors = '[]'))
    """
    if args.limit:
        query += f" LIMIT {args.limit}"

    rows = conn.execute(query).fetchall()
    total = len(rows)
    print(f"Found {total} tracks without integrity data ({args.workers} workers)")

    if total == 0:
        conn.close()
        return

    stats = Stats()
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(process_track, args.db, row["metadata_id"], row["full_path"]): row["metadata_id"]
            for row in rows
        }

        for future in as_completed(futures):
            mid, ok, was_repaired, errors_json = future.result()

            conn.execute(
                "UPDATE technical_features SET integrity_ok = ?, integrity_errors = ?, was_repaired = ? WHERE metadata_id = ?",
                (1 if ok else 0, errors_json, 1 if was_repaired else 0, mid),
            )
            conn.commit()

            is_error = errors_json and "check_failed" in errors_json
            stats.record(ok=ok, repaired=was_repaired, error=is_error)

            if stats.done % 50 == 0 or stats.done == total:
                elapsed = time.time() - t0
                rate = stats.done / elapsed if elapsed > 0 else 0
                eta = (total - stats.done) / rate if rate > 0 else 0
                print(
                    f"  [{stats.done}/{total}] ok={stats.ok} repaired={stats.repaired} "
                    f"bad={stats.bad} err={stats.errors} ({rate:.1f}/s, ETA {eta/60:.0f}m)"
                )

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed/60:.1f}m! ok={stats.ok}, repaired={stats.repaired}, bad={stats.bad}, errors={stats.errors}")
    conn.close()


if __name__ == "__main__":
    main()
