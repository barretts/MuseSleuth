#!/usr/bin/env python
"""Inspect two seed tracks in a MuseSleuth SQLite DB.

Read-only utility that prints:
- per-track metadata/features
- timbre/embedding coverage
- pairwise deltas and cosine similarity where available
"""

from __future__ import annotations

import argparse
import io
import json
import math
import sqlite3
from typing import Any


def _deserialize_array(blob: bytes | None) -> list[float] | None:
    if blob is None:
        return None
    try:
        import numpy as np
    except Exception:
        return None
    try:
        arr = np.load(io.BytesIO(blob), allow_pickle=False)
        return [float(x) for x in arr.tolist()]
    except Exception:
        return None


def _cosine(a: list[float] | None, b: list[float] | None) -> float | None:
    if not a or not b:
        return None
    n = min(len(a), len(b))
    if n <= 0:
        return None
    aa = a[:n]
    bb = b[:n]
    dot = sum(x * y for x, y in zip(aa, bb))
    na = math.sqrt(sum(x * x for x in aa))
    nb = math.sqrt(sum(y * y for y in bb))
    if na == 0.0 or nb == 0.0:
        return None
    return dot / (na * nb)


def _as_year(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if len(text) < 4:
        return None
    try:
        return int(text[:4])
    except ValueError:
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, help="Path to SQLite DB")
    parser.add_argument("--seed-a", required=True)
    parser.add_argument("--seed-b", required=True)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON only")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    seeds = [args.seed_a, args.seed_b]

    rows = conn.execute(
        """
        SELECT
            t.metadata_id,
            t.title,
            t.artist,
            t.album,
            t.year,
            mf.bpm_final,
            mf.energy,
            ps.camelot_key,
            ml.genre_primary,
            ml.mood_tags,
            ml.danceability,
            tf.mfcc_mean AS timbre_vec,
            (
                SELECT e.vector
                FROM embeddings e
                WHERE e.metadata_id = t.metadata_id AND e.scope = 'global'
                ORDER BY e.id DESC
                LIMIT 1
            ) AS embedding_vec
        FROM tracks t
        LEFT JOIN musical_features mf ON mf.metadata_id = t.metadata_id
        LEFT JOIN playlist_signals ps ON ps.metadata_id = t.metadata_id
        LEFT JOIN ml_features ml ON ml.metadata_id = t.metadata_id
        LEFT JOIN timbre_features tf ON tf.metadata_id = t.metadata_id
        WHERE t.metadata_id IN (?, ?)
        ORDER BY t.metadata_id
        """,
        seeds,
    ).fetchall()

    track_out: list[dict[str, Any]] = []
    for row in rows:
        d = dict(row)
        d["has_timbre"] = row["timbre_vec"] is not None
        d["has_embedding"] = row["embedding_vec"] is not None
        d.pop("timbre_vec", None)
        d.pop("embedding_vec", None)
        track_out.append(d)

    pairwise: dict[str, Any] = {}
    if len(rows) == 2:
        a = rows[0]
        b = rows[1]
        ta = _deserialize_array(a["timbre_vec"]) if a["timbre_vec"] is not None else None
        tb = _deserialize_array(b["timbre_vec"]) if b["timbre_vec"] is not None else None
        ea = _deserialize_array(a["embedding_vec"]) if a["embedding_vec"] is not None else None
        eb = _deserialize_array(b["embedding_vec"]) if b["embedding_vec"] is not None else None

        a_year = _as_year(a["year"])
        b_year = _as_year(b["year"])

        pairwise = {
            "bpm_delta": None if a["bpm_final"] is None or b["bpm_final"] is None else abs(float(a["bpm_final"]) - float(b["bpm_final"])),
            "energy_delta": None if a["energy"] is None or b["energy"] is None else abs(float(a["energy"]) - float(b["energy"])),
            "year_delta": None if a_year is None or b_year is None else abs(a_year - b_year),
            "camelot_a": a["camelot_key"],
            "camelot_b": b["camelot_key"],
            "genre_a": a["genre_primary"],
            "genre_b": b["genre_primary"],
            "mood_a": a["mood_tags"],
            "mood_b": b["mood_tags"],
            "danceability_a": a["danceability"],
            "danceability_b": b["danceability"],
            "timbre_cosine_similarity": _cosine(ta, tb),
            "embedding_cosine_similarity": _cosine(ea, eb),
            "timbre_len_a": len(ta) if ta else None,
            "timbre_len_b": len(tb) if tb else None,
            "embedding_len_a": len(ea) if ea else None,
            "embedding_len_b": len(eb) if eb else None,
        }

    embedding_rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT metadata_id, model, scope, LENGTH(vector) AS bytes_len
            FROM embeddings
            WHERE metadata_id IN (?, ?)
            ORDER BY metadata_id, id DESC
            """,
            seeds,
        ).fetchall()
    ]

    timbre_rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT metadata_id,
                   LENGTH(mfcc_mean) AS mfcc_mean_bytes,
                   LENGTH(mfcc_var) AS mfcc_var_bytes,
                   centroid_mean,
                   rolloff_mean,
                   bandwidth_mean,
                   flatness_mean,
                   zcr_mean
            FROM timbre_features
            WHERE metadata_id IN (?, ?)
            ORDER BY metadata_id
            """,
            seeds,
        ).fetchall()
    ]

    genre_rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT metadata_id, tag_value, source, confidence
            FROM genres_tags
            WHERE metadata_id IN (?, ?)
            ORDER BY metadata_id, confidence DESC, tag_value
            LIMIT 100
            """,
            seeds,
        ).fetchall()
    ]

    out = {
        "db": args.db,
        "seed_ids": seeds,
        "tracks": track_out,
        "pairwise": pairwise,
        "embeddings": embedding_rows,
        "timbre": timbre_rows,
        "genre_tags": genre_rows,
    }

    if args.json:
        print(json.dumps(out, indent=2, default=str))
    else:
        print("SEED TRACK DETAILS")
        print("=" * 80)
        for t in track_out:
            print(json.dumps(t, indent=2, default=str))
        print("\nPAIRWISE")
        print("=" * 80)
        print(json.dumps(pairwise, indent=2, default=str))
        print("\nEMBEDDINGS")
        print("=" * 80)
        for r in embedding_rows:
            print(json.dumps(r, indent=2, default=str))
        print("\nTIMBRE")
        print("=" * 80)
        for r in timbre_rows:
            print(json.dumps(r, indent=2, default=str))
        print("\nGENRE TAGS (TOP)")
        print("=" * 80)
        for r in genre_rows[:40]:
            print(json.dumps(r, indent=2, default=str))

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
