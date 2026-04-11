#!/usr/bin/env python
"""Generate a custom playlist with multi-seed parameters.

Persists a playlist in the DB and prints playlist id + count.
"""

from __future__ import annotations

import argparse
import json
import sqlite3

from musesleuth.db import create_schema, get_connection
from musesleuth.playlist_generator import generate_playlist


def _parse_csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--limit", type=int, default=80)
    parser.add_argument("--seed-ids", required=True, help="Comma-separated metadata IDs")
    parser.add_argument("--seed-facets", default="bpm,energy,embedding,timbre,year")
    parser.add_argument("--year-min", type=int, default=1999)
    parser.add_argument("--year-max", type=int, default=2009)
    parser.add_argument("--seed-year-window", type=int, default=2)
    parser.add_argument("--bpm-min", type=float, default=125.2)
    parser.add_argument("--bpm-max", type=float, default=140.0)
    parser.add_argument("--energy-min", type=float, default=0.222)
    parser.add_argument("--energy-max", type=float, default=0.391)
    parser.add_argument("--moods", default="dance,energetic")
    parser.add_argument("--genres", default="")
    parser.add_argument("--sort-by", default="popularity")
    parser.add_argument("--description", default="")
    args = parser.parse_args()

    seed_ids = _parse_csv(args.seed_ids)
    if not seed_ids:
        raise SystemExit("--seed-ids is required")

    params: dict[str, str | float | int] = {
        "seed_id": seed_ids[0],
        "seed_ids": ",".join(seed_ids),
        "seed_facets": args.seed_facets,
        "year_min": args.year_min,
        "year_max": args.year_max,
        "seed_year_window": args.seed_year_window,
        "bpm_min": args.bpm_min,
        "bpm_max": args.bpm_max,
        "energy_min": args.energy_min,
        "energy_max": args.energy_max,
        "moods": args.moods,
        "sort_by": args.sort_by,
    }
    if args.genres.strip():
        params["genres"] = args.genres.strip()

    conn = get_connection(args.db)
    create_schema(conn)
    result = generate_playlist(
        conn,
        "custom",
        args.name,
        params,
        limit=max(1, args.limit),
        description=(args.description or None),
    )

    track_rows = conn.execute(
        "SELECT metadata_id FROM playlist_tracks WHERE playlist_id = ? ORDER BY position",
        (result.playlist_id,),
    ).fetchall()
    playlist_ids = [row["metadata_id"] for row in track_rows]
    summary = {
        "playlist_id": result.playlist_id,
        "name": result.name,
        "track_count": result.track_count,
        "seed_ids": seed_ids,
        "seed_present": {mid: (mid in playlist_ids) for mid in seed_ids},
        "params": params,
    }
    print(json.dumps(summary, indent=2, default=str))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
