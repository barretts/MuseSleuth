from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from musesleuth.db import create_schema, get_connection


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="List MuseSleuth playlists from the SQLite database.")
    parser.add_argument("--db", default=r"E:\ms\music_new.db")
    parser.add_argument("--limit", type=int, default=25)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    conn = get_connection(Path(args.db))
    create_schema(conn)
    rows = conn.execute(
        """
        SELECT p.playlist_id, p.name, COUNT(pt.metadata_id) AS track_count, p.created_at
        FROM playlists p
        LEFT JOIN playlist_tracks pt ON pt.playlist_id = p.playlist_id
        GROUP BY p.playlist_id, p.name, p.created_at
        ORDER BY p.created_at DESC
        LIMIT ?
        """,
        (args.limit,),
    ).fetchall()
    if not rows:
        print("No playlists found.")
        conn.close()
        return 0

    for row in rows:
        print(f"{row['playlist_id']}\t{row['name']}\ttracks={row['track_count']}\tcreated={row['created_at']}")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
