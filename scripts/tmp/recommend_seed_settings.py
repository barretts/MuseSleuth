#!/usr/bin/env python
"""Recommend concrete custom playlist settings for a pair of seed tracks."""

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


def _camelot_parts(key: str | None) -> tuple[int, str] | None:
    if not key:
        return None
    s = str(key).strip().upper()
    if len(s) < 2:
        return None
    letter = s[-1]
    if letter not in ("A", "B"):
        return None
    try:
        num = int(s[:-1])
    except ValueError:
        return None
    if not (1 <= num <= 12):
        return None
    return num, letter


def _camelot_compatible(a: str | None, b: str | None) -> bool:
    pa = _camelot_parts(a)
    pb = _camelot_parts(b)
    if not pa or not pb:
        return False
    na, la = pa
    nb, lb = pb
    if na == nb:
        return True
    if la == lb and ((na - nb) % 12 in (1, 11)):
        return True
    return False


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--seed-a", required=True)
    parser.add_argument("--seed-b", required=True)
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        """
        SELECT
            t.metadata_id,
            t.title,
            t.artist,
            t.year,
            mf.bpm_final,
            mf.energy,
            ps.camelot_key,
            ml.genre_primary,
            ml.mood_tags,
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
        [args.seed_a, args.seed_b],
    ).fetchall()

    if len(rows) != 2:
        raise SystemExit(f"Expected 2 seed rows, found {len(rows)}")

    a, b = rows

    bpm_vals = [float(v) for v in (a["bpm_final"], b["bpm_final"]) if v is not None]
    energy_vals = [float(v) for v in (a["energy"], b["energy"]) if v is not None]

    bpm_min = bpm_max = None
    if len(bpm_vals) == 2:
        lo, hi = min(bpm_vals), max(bpm_vals)
        pad = max(4.0, (hi - lo) * 0.25)
        bpm_min = round(_clamp(lo - pad, 60.0, 220.0), 1)
        bpm_max = round(_clamp(hi + pad, 60.0, 220.0), 1)
    elif len(bpm_vals) == 1:
        bpm_min = round(_clamp(bpm_vals[0] - 6.0, 60.0, 220.0), 1)
        bpm_max = round(_clamp(bpm_vals[0] + 6.0, 60.0, 220.0), 1)

    energy_min = energy_max = None
    if len(energy_vals) == 2:
        lo, hi = min(energy_vals), max(energy_vals)
        pad = max(0.06, (hi - lo) * 0.25)
        energy_min = round(_clamp(lo - pad, 0.0, 1.0), 3)
        energy_max = round(_clamp(hi + pad, 0.0, 1.0), 3)
    elif len(energy_vals) == 1:
        energy_min = round(_clamp(energy_vals[0] - 0.08, 0.0, 1.0), 3)
        energy_max = round(_clamp(energy_vals[0] + 0.08, 0.0, 1.0), 3)

    ta = _deserialize_array(a["timbre_vec"]) if a["timbre_vec"] is not None else None
    tb = _deserialize_array(b["timbre_vec"]) if b["timbre_vec"] is not None else None
    ea = _deserialize_array(a["embedding_vec"]) if a["embedding_vec"] is not None else None
    eb = _deserialize_array(b["embedding_vec"]) if b["embedding_vec"] is not None else None

    timbre_cos = _cosine(ta, tb)
    embedding_cos = _cosine(ea, eb)

    include_camelot = _camelot_compatible(a["camelot_key"], b["camelot_key"])

    years = [_as_year(a["year"]), _as_year(b["year"])]
    if years[0] is None or years[1] is None:
        include_year = False
        seed_year_window = None
    else:
        year_delta = abs(years[0] - years[1])
        include_year = year_delta <= 8
        seed_year_window = max(2, min(8, int(math.ceil(year_delta / 2)))) if include_year else None

    genre_a = str(a["genre_primary"] or "").strip().lower()
    genre_b = str(b["genre_primary"] or "").strip().lower()
    same_genre = bool(genre_a and genre_b and genre_a == genre_b)

    facets: list[str] = ["bpm", "energy"]
    if same_genre:
        facets.insert(0, "genre")
    if include_camelot:
        facets.append("camelot")

    include_embedding = embedding_cos is not None and embedding_cos >= 0.65
    include_timbre = timbre_cos is not None and timbre_cos >= 0.6
    if include_embedding:
        facets.append("embedding")
    if include_timbre:
        facets.append("timbre")
    if include_year:
        facets.append("year")

    settings: dict[str, Any] = {
        "strategy": "custom",
        "seed_ids": [args.seed_a, args.seed_b],
        "seed_facets": facets,
        "sort_by": "popularity",
        "limit": 50,
    }
    if bpm_min is not None and bpm_max is not None:
        settings["bpm_min"] = bpm_min
        settings["bpm_max"] = bpm_max
    if energy_min is not None and energy_max is not None:
        settings["energy_min"] = energy_min
        settings["energy_max"] = energy_max
    if include_year and seed_year_window is not None:
        settings["seed_year_window"] = seed_year_window

    diagnostics = {
        "track_a": {
            "metadata_id": a["metadata_id"],
            "title": a["title"],
            "artist": a["artist"],
            "year": a["year"],
            "bpm": a["bpm_final"],
            "energy": a["energy"],
            "camelot_key": a["camelot_key"],
            "genre_primary": a["genre_primary"],
            "mood_tags": a["mood_tags"],
            "has_embedding": ea is not None,
            "has_timbre": ta is not None,
        },
        "track_b": {
            "metadata_id": b["metadata_id"],
            "title": b["title"],
            "artist": b["artist"],
            "year": b["year"],
            "bpm": b["bpm_final"],
            "energy": b["energy"],
            "camelot_key": b["camelot_key"],
            "genre_primary": b["genre_primary"],
            "mood_tags": b["mood_tags"],
            "has_embedding": eb is not None,
            "has_timbre": tb is not None,
        },
        "pairwise": {
            "bpm_delta": None if a["bpm_final"] is None or b["bpm_final"] is None else abs(float(a["bpm_final"]) - float(b["bpm_final"])),
            "energy_delta": None if a["energy"] is None or b["energy"] is None else abs(float(a["energy"]) - float(b["energy"])),
            "year_delta": None if years[0] is None or years[1] is None else abs(years[0] - years[1]),
            "camelot_compatible": include_camelot,
            "embedding_cosine": embedding_cos,
            "timbre_cosine": timbre_cos,
        },
    }

    print("RECOMMENDED SETTINGS")
    print("=" * 80)
    print(json.dumps(settings, indent=2, default=str))
    print("\nDIAGNOSTICS")
    print("=" * 80)
    print(json.dumps(diagnostics, indent=2, default=str))

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
