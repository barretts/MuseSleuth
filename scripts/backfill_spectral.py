"""Backfill spectral/MFCC/timbral features for tracks already classified.

Only touches tracks that have ml_features rows but NULL spectral_centroid.
Does NOT re-run the EDM model or any other classification -- just extracts
spectral features from audio and updates the existing row.

Usage: python scripts/backfill_spectral.py [--db music_new.db] [--limit N] [--workers 4]
"""
import argparse
import json
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

sys.path.insert(0, "src")

from musesleuth.ml_runner import _analyze_spectral_features


class Stats:
    def __init__(self):
        self.ok = 0
        self.errors = 0
        self.done = 0
        self._lock = Lock()

    def record(self, *, ok: bool):
        with self._lock:
            self.done += 1
            if ok:
                self.ok += 1
            else:
                self.errors += 1


def process_track(fpath: str) -> tuple[dict | None, str | None]:
    try:
        result = _analyze_spectral_features(fpath)
        return result, None
    except Exception as e:
        return None, str(e)[:200]


def main():
    parser = argparse.ArgumentParser(description="Backfill spectral/MFCC features")
    parser.add_argument("--db", default="music_new.db", help="Database path")
    parser.add_argument("--limit", type=int, default=0, help="Max tracks (0=all)")
    parser.add_argument("--workers", type=int, default=4, help="Concurrent workers")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")

    query = """
        SELECT m.metadata_id, t.full_path
        FROM ml_features m
        JOIN tracks t ON t.metadata_id = m.metadata_id
        WHERE m.spectral_centroid IS NULL
    """
    if args.limit:
        query += f" LIMIT {args.limit}"

    rows = conn.execute(query).fetchall()
    total = len(rows)
    print(f"Found {total} tracks needing spectral backfill ({args.workers} workers)")

    if total == 0:
        conn.close()
        return

    stats = Stats()
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(process_track, row["full_path"]): row["metadata_id"]
            for row in rows
        }

        for future in as_completed(futures):
            mid = futures[future]
            spectral, error = future.result()

            if spectral and error is None:
                conn.execute(
                    """UPDATE ml_features SET
                        spectral_centroid = ?,
                        spectral_bandwidth = ?,
                        spectral_rolloff = ?,
                        spectral_flatness = ?,
                        zero_crossing_rate = ?,
                        mfcc_mean = ?,
                        mfcc_std = ?,
                        spectral_contrast = ?,
                        harmonic_percussive_ratio = ?
                    WHERE metadata_id = ?""",
                    (
                        spectral.get("spectral_centroid"),
                        spectral.get("spectral_bandwidth"),
                        spectral.get("spectral_rolloff"),
                        spectral.get("spectral_flatness"),
                        spectral.get("zero_crossing_rate"),
                        json.dumps(spectral.get("mfcc_mean")),
                        json.dumps(spectral.get("mfcc_std")),
                        json.dumps(spectral.get("spectral_contrast")),
                        spectral.get("harmonic_percussive_ratio"),
                        mid,
                    ),
                )
                conn.commit()
                stats.record(ok=True)
            else:
                stats.record(ok=False)

            if stats.done % 50 == 0 or stats.done == total:
                elapsed = time.time() - t0
                rate = stats.done / elapsed if elapsed > 0 else 0
                eta = (total - stats.done) / rate if rate > 0 else 0
                print(
                    f"  [{stats.done}/{total}] ok={stats.ok} err={stats.errors} "
                    f"({rate:.1f}/s, ETA {eta / 60:.0f}m)"
                )

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed / 60:.1f}m! ok={stats.ok}, errors={stats.errors}")
    conn.close()


if __name__ == "__main__":
    main()
