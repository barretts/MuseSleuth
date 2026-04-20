"""Retroactively apply prefer-official filtering to existing playlists.

For every playlist (except excluded names), drop tracks that would be
filtered out today by
:func:`musesleuth.prefer_official.filter_and_prefer_official`, then
renumber the remaining ``playlist_tracks.position`` values and update
``playlists.track_count``. Idempotent; ``--dry-run`` makes no changes.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from musesleuth.db import get_connection  # noqa: E402
from musesleuth.prefer_official import (  # noqa: E402
    DEFAULT_POPULARITY_FLOOR,
    filter_and_prefer_official,
)


def _fetch_playlist_rows(
    conn: sqlite3.Connection, playlist_id: str
) -> list[sqlite3.Row]:
    """Load the playlist's tracks as candidate rows with the dedup columns."""
    return conn.execute(
        """
        SELECT t.metadata_id,
               ps.duplicate_group AS duplicate_group,
               ps.remix_group     AS remix_group
        FROM playlist_tracks pt
        JOIN tracks t           ON t.metadata_id = pt.metadata_id
        LEFT JOIN playlist_signals ps ON ps.metadata_id = t.metadata_id
        WHERE pt.playlist_id = ?
        ORDER BY pt.position
        """,
        (playlist_id,),
    ).fetchall()


def _list_dropped(
    conn: sqlite3.Connection,
    dropped_ids: list[str],
) -> list[tuple[str, str]]:
    if not dropped_ids:
        return []
    placeholders = ",".join("?" * len(dropped_ids))
    rows = conn.execute(
        f"SELECT metadata_id, artist, title FROM tracks "
        f"WHERE metadata_id IN ({placeholders})",
        dropped_ids,
    ).fetchall()
    by_id = {r["metadata_id"]: (r["artist"] or "", r["title"] or "") for r in rows}
    return [by_id.get(mid, ("?", "?")) for mid in dropped_ids]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument(
        "--exclude-name",
        action="append",
        default=[],
        help="Playlist name to skip (case-insensitive, repeatable).",
    )
    ap.add_argument(
        "--popularity-floor",
        type=int,
        default=DEFAULT_POPULARITY_FLOOR,
    )
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--verbose",
        action="store_true",
        help="Print artist - title of each dropped track.",
    )
    args = ap.parse_args()

    if not args.db.exists():
        print(f"Database not found: {args.db}", file=sys.stderr)
        return 1

    excluded = {n.strip().lower() for n in args.exclude_name}

    conn = get_connection(args.db)
    playlists = conn.execute(
        "SELECT playlist_id, name, strategy, track_count FROM playlists "
        "ORDER BY created_at"
    ).fetchall()

    grand_total = 0
    grand_dropped = 0
    touched_playlists = 0

    for pl in playlists:
        pl_name = pl["name"]
        pl_id = pl["playlist_id"]
        if pl_name.strip().lower() in excluded:
            print(f"SKIP  {pl_id}  {pl_name}  (excluded)")
            continue

        rows = _fetch_playlist_rows(conn, pl_id)
        before = len(rows)
        grand_total += before
        if before == 0:
            print(f"EMPTY {pl_id}  {pl_name}")
            continue

        filtered = filter_and_prefer_official(
            conn,
            rows,
            popularity_floor=args.popularity_floor,
        )
        kept_ids = [r["metadata_id"] for r in filtered]
        kept_set = set(kept_ids)
        original_ids = [r["metadata_id"] for r in rows]
        dropped_ids = [mid for mid in original_ids if mid not in kept_set]

        after = len(kept_ids)
        dropped = before - after
        grand_dropped += dropped

        status = "KEEP " if dropped == 0 else "TRIM "
        print(
            f"{status} {pl_id}  {pl_name[:40]:40s}  "
            f"{before:>4} -> {after:>4}  (-{dropped})"
        )
        if args.verbose and dropped_ids:
            for artist, title in _list_dropped(conn, dropped_ids):
                print(f"        - {artist} - {title}")

        if dropped == 0 or args.dry_run:
            continue

        # Replace playlist_tracks rows with renumbered kept_ids.
        # We preserve the filter's output order (filter may re-sort within
        # remix groups, so the new order mirrors what a fresh regenerate
        # would produce today).
        conn.execute("DELETE FROM playlist_tracks WHERE playlist_id = ?", (pl_id,))
        conn.executemany(
            "INSERT INTO playlist_tracks (playlist_id, metadata_id, position) "
            "VALUES (?, ?, ?)",
            [(pl_id, mid, pos) for pos, mid in enumerate(kept_ids, start=1)],
        )
        conn.execute(
            "UPDATE playlists SET track_count = ? WHERE playlist_id = ?",
            (after, pl_id),
        )
        conn.commit()
        touched_playlists += 1

    suffix = " (dry-run, no writes)" if args.dry_run else ""
    print()
    print(
        f"Summary: {len(playlists)} playlists scanned, "
        f"{touched_playlists} modified, "
        f"{grand_dropped} / {grand_total} tracks dropped{suffix}."
    )
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
