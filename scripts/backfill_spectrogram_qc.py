"""Backfill spectrogram-derived QC metrics and preview images.

Runs the same spectrogram analysis used by the analyze stage and writes:
  - technical_features.lowpass_cutoff_hz
  - technical_features.silence_ratio
  - technical_features.clipping_ratio
  - technical_features.spectrogram_path
  - technical_features.spectrogram_generated_at

Usage:
  python scripts/backfill_spectrogram_qc.py --db music_new.db --workers 4
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from musesleuth.db import create_schema, get_connection
from musesleuth.spectral_qc import analyze_spectrogram_quality


class Stats:
    def __init__(self) -> None:
        self.ok = 0
        self.errors = 0
        self.done = 0
        self._lock = Lock()

    def record(self, ok: bool) -> None:
        with self._lock:
            self.done += 1
            if ok:
                self.ok += 1
            else:
                self.errors += 1


def process_track(full_path: str) -> tuple[dict | None, str | None]:
    try:
        result = analyze_spectrogram_quality(
            full_path,
            output_path=full_path + ".spectrogram.pgm",
        )
        return result, None
    except Exception as exc:
        return None, str(exc)[:200]


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill spectrogram QC metrics")
    parser.add_argument("--db", default="music_new.db", help="Database path")
    parser.add_argument("--limit", type=int, default=0, help="Max tracks (0=all)")
    parser.add_argument("--workers", type=int, default=4, help="Concurrent workers")
    parser.add_argument(
        "--only-missing",
        action="store_true",
        help="Process only tracks missing spectrogram_generated_at",
    )
    args = parser.parse_args()

    conn = get_connection(Path(args.db))
    create_schema(conn)

    query = """
        SELECT t.metadata_id, t.full_path
        FROM tracks t
        LEFT JOIN technical_features tf ON tf.metadata_id = t.metadata_id
    """
    if args.only_missing:
        query += " WHERE tf.spectrogram_generated_at IS NULL"
    query += " ORDER BY t.imported_at"
    if args.limit:
        query += f" LIMIT {args.limit}"

    rows = conn.execute(query).fetchall()
    total = len(rows)
    print(f"Found {total} tracks for spectrogram QC backfill ({args.workers} workers)")

    if total == 0:
        conn.close()
        return

    stats = Stats()
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {
            pool.submit(process_track, row["full_path"]): row["metadata_id"]
            for row in rows
        }

        for fut in as_completed(futures):
            mid = futures[fut]
            qc, err = fut.result()
            if qc is None or err is not None:
                stats.record(False)
                continue

            update_cursor = conn.execute(
                """
                UPDATE technical_features
                SET lowpass_cutoff_hz = ?,
                    silence_ratio = ?,
                    clipping_ratio = ?,
                    spectrogram_path = ?,
                    spectrogram_generated_at = datetime('now')
                WHERE metadata_id = ?
                """,
                (
                    qc.get("lowpass_cutoff_hz"),
                    qc.get("silence_ratio"),
                    qc.get("clipping_ratio"),
                    qc.get("spectrogram_path"),
                    mid,
                ),
            )
            if update_cursor.rowcount == 0:
                conn.execute(
                    """
                    INSERT INTO technical_features
                        (metadata_id, lowpass_cutoff_hz, silence_ratio, clipping_ratio,
                         spectrogram_path, spectrogram_generated_at, analyzed_at)
                    VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                    """,
                    (
                        mid,
                        qc.get("lowpass_cutoff_hz"),
                        qc.get("silence_ratio"),
                        qc.get("clipping_ratio"),
                        qc.get("spectrogram_path"),
                    ),
                )
            conn.commit()
            stats.record(True)

            if stats.done % 50 == 0 or stats.done == total:
                elapsed = time.time() - t0
                rate = stats.done / elapsed if elapsed > 0 else 0.0
                eta = (total - stats.done) / rate if rate > 0 else 0.0
                print(
                    f"  [{stats.done}/{total}] ok={stats.ok} err={stats.errors} "
                    f"({rate:.1f}/s, ETA {eta/60:.0f}m)"
                )

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed/60:.1f}m! ok={stats.ok}, errors={stats.errors}")
    conn.close()


if __name__ == "__main__":
    main()

