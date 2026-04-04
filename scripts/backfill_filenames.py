"""Backfill empty Title/Artist fields from filename parsing for existing DB rows."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

# Ensure musesleuth is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from musesleuth.filename_parser import parse_filename


def main(db_path: str) -> None:
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    conn.row_factory = sqlite3.Row

    total = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
    print(f"Total tracks in DB: {total}")

    # Find rows needing backfill
    rows = conn.execute(
        """
        SELECT metadata_id, filename, title, artist
        FROM tracks
        WHERE COALESCE(TRIM(title), '') = ''
           OR COALESCE(TRIM(artist), '') = ''
        """
    ).fetchall()

    print(f"Rows needing backfill: {len(rows)}")

    updated_title = 0
    updated_artist = 0

    for row in rows:
        mid = row["metadata_id"]
        filename = row["filename"] or ""
        old_title = (row["title"] or "").strip()
        old_artist = (row["artist"] or "").strip()

        if not filename:
            continue

        parsed = parse_filename(filename)

        new_title = old_title if old_title else parsed["title"]
        new_artist = old_artist if old_artist else parsed["artist"]

        if new_title != (row["title"] or "") or new_artist != (row["artist"] or ""):
            conn.execute(
                "UPDATE tracks SET title = ?, artist = ?, updated_at = datetime('now') WHERE metadata_id = ?",
                (new_title, new_artist, mid),
            )
            if not old_title and new_title:
                updated_title += 1
            if not old_artist and new_artist:
                updated_artist += 1

    conn.commit()
    conn.close()

    print(f"Backfilled {updated_title} titles, {updated_artist} artists.")

    # Verify
    conn2 = sqlite3.connect(db_path, timeout=30)
    still_empty_title = conn2.execute(
        "SELECT COUNT(*) FROM tracks WHERE COALESCE(TRIM(title), '') = ''"
    ).fetchone()[0]
    still_empty_artist = conn2.execute(
        "SELECT COUNT(*) FROM tracks WHERE COALESCE(TRIM(artist), '') = ''"
    ).fetchone()[0]
    conn2.close()

    print(f"Remaining empty titles: {still_empty_title}")
    print(f"Remaining empty artists: {still_empty_artist}")


if __name__ == "__main__":
    db = sys.argv[1] if len(sys.argv) > 1 else "music.db"
    main(db)
