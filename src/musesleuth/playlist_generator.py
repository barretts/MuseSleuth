"""Strategy-based playlist generation from enriched track data."""
from __future__ import annotations

import json
import math
import sqlite3
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from musesleuth.db import generate_metadata_id


STRATEGIES = ("genre", "bpm_range", "year_range", "camelot_chain", "energy_arc", "decade", "mood", "custom", "manual")


@dataclass
class PlaylistResult:
    """Returned by every generator after a successful build."""

    playlist_id: str
    name: str
    track_count: int


# ---------------------------------------------------------------------------
# Camelot compatibility
# ---------------------------------------------------------------------------

def camelot_compatible(key1: str, key2: str) -> bool:
    """True when two Camelot keys are mix-compatible.

    Compatible means same key, +/-1 on the wheel (wrapping 12<->1),
    or the relative major/minor swap (A<->B at same number).
    """
    if not key1 or not key2:
        return False
    try:
        num1, letter1 = int(key1[:-1]), key1[-1]
        num2, letter2 = int(key2[:-1]), key2[-1]
    except (ValueError, IndexError):
        return False

    if num1 == num2 and letter1 == letter2:
        return True
    if num1 == num2 and letter1 != letter2:
        return True
    if letter1 == letter2:
        diff = abs(num1 - num2)
        if diff == 1 or diff == 11:
            return True
    return False


# ---------------------------------------------------------------------------
# Dedup helper
# ---------------------------------------------------------------------------

def _dedup_candidates(rows: list[sqlite3.Row]) -> list[sqlite3.Row]:
    """Keep only one track per duplicate_group and remix_group (first encountered wins)."""
    seen_dup: set[str] = set()
    seen_remix: set[str] = set()
    result: list[sqlite3.Row] = []
    for row in rows:
        dg = row["duplicate_group"]
        if dg and dg in seen_dup:
            continue
        rg = row["remix_group"]
        if rg and rg in seen_remix:
            continue
        if dg:
            seen_dup.add(dg)
        if rg:
            seen_remix.add(rg)
        result.append(row)
    return result


def _parse_seed_facets(raw: str | None) -> list[str]:
    if not raw:
        return []
    allowed = {"genre", "bpm", "energy", "camelot", "mood", "year", "embedding", "timbre"}
    out: list[str] = []
    for facet in (part.strip().lower() for part in raw.split(",")):
        if facet and facet in allowed and facet not in out:
            out.append(facet)
    return out


def _as_year(value: str | int | None) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if len(text) < 4:
        return None
    try:
        return int(text[:4])
    except ValueError:
        return None


def _mood_set(value: str | None) -> set[str]:
    if not value:
        return set()
    return {part.strip().lower() for part in str(value).split(",") if part.strip()}


def _blob_cosine_similarity_score(
    seed_blob: bytes | None,
    candidate_blob: bytes | None,
) -> float | None:
    if seed_blob is None or candidate_blob is None:
        return None

    try:
        from musesleuth.timbre import deserialize_array
    except Exception:
        return None

    try:
        seed_vec = deserialize_array(seed_blob)
        candidate_vec = deserialize_array(candidate_blob)
    except Exception:
        return None

    if not seed_vec or not candidate_vec:
        return None

    length = min(len(seed_vec), len(candidate_vec))
    if length == 0:
        return None

    seed_vals = [float(v) for v in seed_vec[:length]]
    candidate_vals = [float(v) for v in candidate_vec[:length]]
    dot = sum(a * b for a, b in zip(seed_vals, candidate_vals))
    seed_norm = math.sqrt(sum(a * a for a in seed_vals))
    candidate_norm = math.sqrt(sum(b * b for b in candidate_vals))
    if seed_norm == 0.0 or candidate_norm == 0.0:
        return None

    cosine_sim = max(-1.0, min(1.0, dot / (seed_norm * candidate_norm)))
    cosine_dist = 1.0 - cosine_sim
    return max(0.0, 1.0 - min(cosine_dist, 2.0) / 2.0)


def _seed_similarity_score(seed: dict, candidate: dict, facets: list[str]) -> float:
    if not facets:
        return 0.0

    total = 0.0
    used = 0
    for facet in facets:
        if facet == "genre":
            seed_genre = (seed.get("genre_primary") or "").strip().lower()
            cand_genre = (candidate.get("genre_primary") or "").strip().lower()
            if seed_genre and cand_genre:
                used += 1
                total += 1.0 if seed_genre == cand_genre else 0.0
        elif facet == "bpm":
            seed_bpm = seed.get("bpm_final")
            cand_bpm = candidate.get("bpm_final")
            if seed_bpm is not None and cand_bpm is not None:
                used += 1
                total += max(0.0, 1.0 - min(abs(float(seed_bpm) - float(cand_bpm)), 20.0) / 20.0)
        elif facet == "energy":
            seed_energy = seed.get("energy")
            cand_energy = candidate.get("energy")
            if seed_energy is not None and cand_energy is not None:
                used += 1
                total += max(0.0, 1.0 - min(abs(float(seed_energy) - float(cand_energy)), 0.25) / 0.25)
        elif facet == "camelot":
            seed_key = str(seed.get("camelot_key") or "")
            cand_key = str(candidate.get("camelot_key") or "")
            if seed_key and cand_key:
                used += 1
                if seed_key == cand_key:
                    total += 1.0
                elif camelot_compatible(seed_key, cand_key):
                    total += 0.7
        elif facet == "mood":
            seed_moods = _mood_set(seed.get("mood_tags"))
            cand_moods = _mood_set(candidate.get("mood_tags"))
            if seed_moods and cand_moods:
                used += 1
                overlap = len(seed_moods & cand_moods)
                union = len(seed_moods | cand_moods)
                total += (overlap / union) if union else 0.0
        elif facet == "year":
            seed_year = _as_year(seed.get("year"))
            cand_year = _as_year(candidate.get("year"))
            if seed_year is not None and cand_year is not None:
                used += 1
                total += max(0.0, 1.0 - min(abs(seed_year - cand_year), 10) / 10.0)
        elif facet == "embedding":
            score = _blob_cosine_similarity_score(seed.get("embedding_vec"), candidate.get("embedding_vec"))
            if score is not None:
                used += 1
                total += score
        elif facet == "timbre":
            score = _blob_cosine_similarity_score(seed.get("timbre_vec"), candidate.get("timbre_vec"))
            if score is not None:
                used += 1
                total += score

    return (total / used) if used else 0.0


def _multi_seed_similarity_score(seeds: list[dict], candidate: dict, facets: list[str]) -> float:
    if not seeds:
        return 0.0
    scores = [_seed_similarity_score(seed, candidate, facets) for seed in seeds]
    return sum(scores) / len(scores)


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def _save_playlist(
    conn: sqlite3.Connection,
    playlist_id: str,
    name: str,
    description: Optional[str],
    strategy: str,
    strategy_params: dict,
    track_ids: list[str],
) -> PlaylistResult:
    """Insert a playlist and its ordered track list into the DB."""
    conn.execute(
        """
        INSERT INTO playlists
            (playlist_id, name, description, strategy, strategy_params, track_count)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (playlist_id, name, description, strategy, json.dumps(strategy_params), len(track_ids)),
    )
    for pos, mid in enumerate(track_ids, start=1):
        conn.execute(
            """
            INSERT INTO playlist_tracks (playlist_id, metadata_id, position)
            VALUES (?, ?, ?)
            """,
            (playlist_id, mid, pos),
        )
    conn.commit()
    return PlaylistResult(playlist_id=playlist_id, name=name, track_count=len(track_ids))


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class PlaylistStrategy(ABC):
    """Interface every playlist generation strategy must implement."""

    strategy_name: str = ""

    @abstractmethod
    def generate(
        self,
        conn: sqlite3.Connection,
        name: str,
        params: dict,
        *,
        limit: int = 50,
        description: Optional[str] = None,
    ) -> PlaylistResult:
        ...


# ---------------------------------------------------------------------------
# Concrete strategies
# ---------------------------------------------------------------------------

class GenrePlaylist(PlaylistStrategy):
    strategy_name = "genre"

    def generate(
        self,
        conn: sqlite3.Connection,
        name: str,
        params: dict,
        *,
        limit: int = 50,
        description: Optional[str] = None,
    ) -> PlaylistResult:
        genre = params.get("genre", "")
        rows = conn.execute(
            """
            SELECT t.metadata_id, ps.duplicate_group, ps.remix_group
            FROM tracks t
            JOIN ml_features ml ON ml.metadata_id = t.metadata_id
            LEFT JOIN playlist_signals ps ON ps.metadata_id = t.metadata_id
            WHERE ml.genre_primary = ?
            ORDER BY ml.genre_confidence DESC
            """,
            (genre,),
        ).fetchall()

        rows = _dedup_candidates(rows)
        track_ids = [r["metadata_id"] for r in rows[:limit]]
        pid = generate_metadata_id()
        return _save_playlist(conn, pid, name, description, self.strategy_name, params, track_ids)


class BpmRangePlaylist(PlaylistStrategy):
    strategy_name = "bpm_range"

    def generate(
        self,
        conn: sqlite3.Connection,
        name: str,
        params: dict,
        *,
        limit: int = 50,
        description: Optional[str] = None,
    ) -> PlaylistResult:
        bpm_min = params.get("bpm_min", 0)
        bpm_max = params.get("bpm_max", 999)
        rows = conn.execute(
            """
            SELECT t.metadata_id, mf.bpm_final, ps.duplicate_group, ps.remix_group
            FROM tracks t
            JOIN musical_features mf ON mf.metadata_id = t.metadata_id
            LEFT JOIN playlist_signals ps ON ps.metadata_id = t.metadata_id
            WHERE mf.bpm_final BETWEEN ? AND ?
            ORDER BY mf.bpm_final ASC
            """,
            (bpm_min, bpm_max),
        ).fetchall()

        rows = _dedup_candidates(rows)
        track_ids = [r["metadata_id"] for r in rows[:limit]]
        pid = generate_metadata_id()
        return _save_playlist(conn, pid, name, description, self.strategy_name, params, track_ids)


class CamelotChainPlaylist(PlaylistStrategy):
    strategy_name = "camelot_chain"

    def generate(
        self,
        conn: sqlite3.Connection,
        name: str,
        params: dict,
        *,
        limit: int = 50,
        description: Optional[str] = None,
    ) -> PlaylistResult:
        seed_id = params.get("seed_id")
        if not seed_id:
            raise ValueError("camelot_chain requires a 'seed_id' parameter")

        seed_row = conn.execute(
            "SELECT camelot_key FROM playlist_signals WHERE metadata_id = ?",
            (seed_id,),
        ).fetchone()
        if not seed_row or not seed_row["camelot_key"]:
            raise ValueError(f"Seed track {seed_id} has no Camelot key")

        all_rows = conn.execute(
            """
            SELECT t.metadata_id, ps.camelot_key, ps.duplicate_group, ps.remix_group
            FROM tracks t
            JOIN playlist_signals ps ON ps.metadata_id = t.metadata_id
            WHERE ps.camelot_key IS NOT NULL
            """,
        ).fetchall()

        all_rows = _dedup_candidates(all_rows)
        pool = {r["metadata_id"]: r["camelot_key"] for r in all_rows}

        chain = [seed_id]
        used = {seed_id}
        current_key = seed_row["camelot_key"]

        while len(chain) < limit:
            best = None
            for mid, ckey in pool.items():
                if mid in used:
                    continue
                if camelot_compatible(current_key, ckey):
                    best = mid
                    current_key = ckey
                    break
            if best is None:
                break
            chain.append(best)
            used.add(best)

        pid = generate_metadata_id()
        return _save_playlist(conn, pid, name, description, self.strategy_name, params, chain)


class EnergyArcPlaylist(PlaylistStrategy):
    """Build a playlist that follows an energy arc: ramp up, peak, cool down."""

    strategy_name = "energy_arc"

    def generate(
        self,
        conn: sqlite3.Connection,
        name: str,
        params: dict,
        *,
        limit: int = 50,
        description: Optional[str] = None,
    ) -> PlaylistResult:
        peak_position = params.get("peak_position", 0.6)

        rows = conn.execute(
            """
            SELECT t.metadata_id, mf.energy, ps.duplicate_group, ps.remix_group
            FROM tracks t
            JOIN musical_features mf ON mf.metadata_id = t.metadata_id
            LEFT JOIN playlist_signals ps ON ps.metadata_id = t.metadata_id
            WHERE mf.energy IS NOT NULL
            ORDER BY mf.energy ASC
            """,
        ).fetchall()

        rows = _dedup_candidates(rows)
        if len(rows) > limit:
            rows = rows[:limit]

        n = len(rows)
        if n == 0:
            pid = generate_metadata_id()
            return _save_playlist(conn, pid, name, description, self.strategy_name, params, [])

        peak_idx = max(0, min(n - 1, int(n * peak_position)))

        ascending = sorted(rows[:peak_idx + 1], key=lambda r: r["energy"])
        descending = sorted(rows[peak_idx + 1:], key=lambda r: r["energy"], reverse=True)

        ordered = ascending + descending
        track_ids = [r["metadata_id"] for r in ordered]

        pid = generate_metadata_id()
        return _save_playlist(conn, pid, name, description, self.strategy_name, params, track_ids)


class DecadePlaylist(PlaylistStrategy):
    strategy_name = "decade"

    def generate(
        self,
        conn: sqlite3.Connection,
        name: str,
        params: dict,
        *,
        limit: int = 50,
        description: Optional[str] = None,
    ) -> PlaylistResult:
        decade = params.get("decade", "")
        rows = conn.execute(
            """
            SELECT t.metadata_id, ps.duplicate_group, ps.remix_group
            FROM tracks t
            JOIN playlist_signals ps ON ps.metadata_id = t.metadata_id
            LEFT JOIN musical_features mf ON mf.metadata_id = t.metadata_id
            WHERE ps.decade_bucket = ?
            ORDER BY t.year ASC, COALESCE(mf.bpm_final, 9999) ASC
            """,
            (decade,),
        ).fetchall()

        rows = _dedup_candidates(rows)
        track_ids = [r["metadata_id"] for r in rows[:limit]]
        pid = generate_metadata_id()
        return _save_playlist(conn, pid, name, description, self.strategy_name, params, track_ids)


class YearRangePlaylist(PlaylistStrategy):
    """Select tracks released within a year range.

    Supports sort_by param: 'popularity' (Last.fm listeners DESC),
    'year' (year ASC, BPM ASC — default).
    """

    strategy_name = "year_range"

    _SORT_SQL = {
        "popularity": "COALESCE(ts.listener_count, 0) DESC",
        "year": "t.year ASC, COALESCE(mf.bpm_final, 9999) ASC",
    }

    def generate(
        self,
        conn: sqlite3.Connection,
        name: str,
        params: dict,
        *,
        limit: int = 50,
        description: Optional[str] = None,
    ) -> PlaylistResult:
        year_min = int(params.get("year_min", 1900))
        year_max = int(params.get("year_max", 2099))
        sort_by = params.get("sort_by", "year")
        order = self._SORT_SQL.get(sort_by, self._SORT_SQL["year"])

        rows = conn.execute(
            f"""
            SELECT t.metadata_id, ps.duplicate_group, ps.remix_group
            FROM tracks t
            LEFT JOIN playlist_signals ps ON ps.metadata_id = t.metadata_id
            LEFT JOIN musical_features mf ON mf.metadata_id = t.metadata_id
            LEFT JOIN track_stats ts ON ts.metadata_id = t.metadata_id AND ts.source = 'lastfm'
            WHERE CAST(SUBSTR(t.year, 1, 4) AS INTEGER) BETWEEN ? AND ?
              AND t.year IS NOT NULL AND t.year != ''
            ORDER BY {order}
            """,
            (year_min, year_max),
        ).fetchall()

        rows = _dedup_candidates(rows)
        track_ids = [r["metadata_id"] for r in rows[:limit]]
        pid = generate_metadata_id()
        return _save_playlist(conn, pid, name, description, self.strategy_name, params, track_ids)


class MoodPlaylist(PlaylistStrategy):
    strategy_name = "mood"

    def generate(
        self,
        conn: sqlite3.Connection,
        name: str,
        params: dict,
        *,
        limit: int = 50,
        description: Optional[str] = None,
    ) -> PlaylistResult:
        mood = params.get("mood", "")
        rows = conn.execute(
            """
            SELECT t.metadata_id, ps.duplicate_group, ps.remix_group
            FROM tracks t
            JOIN ml_features ml ON ml.metadata_id = t.metadata_id
            LEFT JOIN playlist_signals ps ON ps.metadata_id = t.metadata_id
            LEFT JOIN musical_features mf ON mf.metadata_id = t.metadata_id
            WHERE ml.mood_tags LIKE ?
            ORDER BY COALESCE(ml.danceability, 0) DESC,
                     COALESCE(mf.energy, 0) DESC
            """,
            (f"%{mood}%",),
        ).fetchall()

        rows = _dedup_candidates(rows)
        track_ids = [r["metadata_id"] for r in rows[:limit]]
        pid = generate_metadata_id()
        return _save_playlist(conn, pid, name, description, self.strategy_name, params, track_ids)


class CustomPlaylist(PlaylistStrategy):
    """Multi-filter playlist builder combining genre, mood, BPM, energy,
    year range, danceability, and popularity with configurable sort."""

    strategy_name = "custom"

    def generate(
        self,
        conn: sqlite3.Connection,
        name: str,
        params: dict,
        *,
        limit: int = 50,
        description: Optional[str] = None,
    ) -> PlaylistResult:
        seed_id = str(params.get("seed_id", "")).strip()
        seed_ids_raw = str(params.get("seed_ids", "")).strip()
        seed_ids: list[str] = []
        if seed_ids_raw:
            for part in seed_ids_raw.split(","):
                mid = part.strip()
                if mid and mid not in seed_ids:
                    seed_ids.append(mid)
        if seed_id and seed_id not in seed_ids:
            seed_ids.insert(0, seed_id)
        seed_facets = _parse_seed_facets(str(params.get("seed_facets", "")))
        seed_year_window_raw = params.get("seed_year_window")
        seed_year_override_raw = params.get("seed_year_override")
        seed_year_window: int | None = None
        seed_year_override: int | None = None
        if seed_year_window_raw not in (None, ""):
            try:
                seed_year_window = max(0, int(seed_year_window_raw))
            except (TypeError, ValueError):
                seed_year_window = None
        if seed_year_override_raw not in (None, ""):
            try:
                seed_year_override = int(seed_year_override_raw)
            except (TypeError, ValueError):
                seed_year_override = None
        clauses: list[str] = []
        sql_params: list = []
        playlist_signal_cols = {
            r["name"] for r in conn.execute("PRAGMA table_info(playlist_signals)").fetchall()
        }

        # Year range
        year_min = params.get("year_min")
        year_max = params.get("year_max")
        if year_min:
            clauses.append("CAST(SUBSTR(t.year, 1, 4) AS INTEGER) >= ?")
            sql_params.append(int(year_min))
        if year_max:
            clauses.append("CAST(SUBSTR(t.year, 1, 4) AS INTEGER) <= ?")
            sql_params.append(int(year_max))
        if year_min or year_max:
            clauses.append("t.year IS NOT NULL AND t.year != ''")

        # BPM range
        bpm_min = params.get("bpm_min")
        bpm_max = params.get("bpm_max")
        if bpm_min:
            clauses.append("mf.bpm_final >= ?")
            sql_params.append(float(bpm_min))
        if bpm_max:
            clauses.append("mf.bpm_final <= ?")
            sql_params.append(float(bpm_max))

        # Energy range
        energy_min = params.get("energy_min")
        energy_max = params.get("energy_max")
        if energy_min:
            clauses.append("mf.energy >= ?")
            sql_params.append(float(energy_min))
        if energy_max:
            clauses.append("mf.energy <= ?")
            sql_params.append(float(energy_max))

        # Genres (comma-separated list)
        genres_str = params.get("genres", "")
        genres = [g.strip() for g in genres_str.split(",") if g.strip()] if genres_str else []
        if genres:
            placeholders = ",".join("?" * len(genres))
            clauses.append(f"ml.genre_primary IN ({placeholders})")
            sql_params.extend(genres)

        # Mood include (comma-separated)
        moods_str = params.get("moods", "")
        moods = [m.strip() for m in moods_str.split(",") if m.strip()] if moods_str else []
        if moods:
            mood_clauses = ["ml.mood_tags LIKE ?" for _ in moods]
            clauses.append(f"({' OR '.join(mood_clauses)})")
            sql_params.extend(f"%{m}%" for m in moods)

        # Mood exclude (comma-separated)
        mood_exclude_str = params.get("mood_exclude", "")
        mood_excludes = [m.strip() for m in mood_exclude_str.split(",") if m.strip()] if mood_exclude_str else []
        for m in mood_excludes:
            clauses.append("(ml.mood_tags NOT LIKE ? OR ml.mood_tags IS NULL)")
            sql_params.append(f"%{m}%")

        # Danceability minimum
        dance_min = params.get("danceability_min")
        if dance_min:
            clauses.append("ml.danceability >= ?")
            sql_params.append(float(dance_min))

        # Genre confidence minimum
        conf_min = params.get("confidence_min")
        if conf_min:
            clauses.append("ml.genre_confidence >= ?")
            sql_params.append(float(conf_min))

        # Popularity minimum (Last.fm listener_count)
        popularity_min = params.get("popularity_min")
        if popularity_min:
            popularity_metric = str(params.get("popularity_metric", "listeners"))
            pop_col = "ts.play_count" if popularity_metric == "play_count" else "ts.listener_count"
            clauses.append(f"COALESCE({pop_col}, 0) >= ?")
            sql_params.append(int(float(popularity_min)))

        where = " AND ".join(clauses) if clauses else "1=1"

        # Sort
        sort_by = params.get("sort_by", "popularity")
        order_map = {
            "popularity": "COALESCE(ts.listener_count, 0) DESC",
            "bpm": "COALESCE(mf.bpm_final, 9999) ASC",
            "energy_asc": "COALESCE(mf.energy, 0) ASC",
            "energy_desc": "COALESCE(mf.energy, 0) DESC",
            "year": "t.year ASC, COALESCE(mf.bpm_final, 9999) ASC",
            "danceability": "COALESCE(ml.danceability, 0) DESC",
            "random": "RANDOM()",
        }
        order = order_map.get(sort_by, order_map["popularity"])

        duplicate_expr = (
            "ps.duplicate_group AS duplicate_group"
            if "duplicate_group" in playlist_signal_cols
            else "NULL AS duplicate_group"
        )
        remix_expr = (
            "ps.remix_group AS remix_group"
            if "remix_group" in playlist_signal_cols
            else "NULL AS remix_group"
        )

        rows = conn.execute(
            f"""
            SELECT
                t.metadata_id,
                t.year,
                mf.bpm_final,
                mf.energy,
                ps.camelot_key,
                ml.genre_primary,
                ml.mood_tags,
                (
                    SELECT e.vector
                    FROM embeddings e
                    WHERE e.metadata_id = t.metadata_id
                      AND e.scope = 'global'
                    ORDER BY e.id DESC
                    LIMIT 1
                ) AS embedding_vec,
                tf.mfcc_mean AS timbre_vec,
                {duplicate_expr},
                {remix_expr}
            FROM tracks t
            LEFT JOIN musical_features mf ON mf.metadata_id = t.metadata_id
            LEFT JOIN ml_features ml ON ml.metadata_id = t.metadata_id
            LEFT JOIN playlist_signals ps ON ps.metadata_id = t.metadata_id
            LEFT JOIN timbre_features tf ON tf.metadata_id = t.metadata_id
            LEFT JOIN track_stats ts ON ts.metadata_id = t.metadata_id AND ts.source = 'lastfm'
            WHERE {where}
            ORDER BY {order}
            """,
            sql_params,
        ).fetchall()

        if seed_ids:
            existing_ids = {row["metadata_id"] for row in rows}
            missing_seed_ids = [mid for mid in seed_ids if mid not in existing_ids]
            if missing_seed_ids:
                placeholders = ",".join("?" for _ in missing_seed_ids)
                seed_candidates = conn.execute(
                    f"""
                    SELECT
                        t.metadata_id,
                        t.year,
                        mf.bpm_final,
                        mf.energy,
                        ps.camelot_key,
                        ml.genre_primary,
                        ml.mood_tags,
                        (
                            SELECT e.vector
                            FROM embeddings e
                            WHERE e.metadata_id = t.metadata_id
                              AND e.scope = 'global'
                            ORDER BY e.id DESC
                            LIMIT 1
                        ) AS embedding_vec,
                        tf.mfcc_mean AS timbre_vec,
                        {duplicate_expr},
                        {remix_expr}
                    FROM tracks t
                    LEFT JOIN musical_features mf ON mf.metadata_id = t.metadata_id
                    LEFT JOIN ml_features ml ON ml.metadata_id = t.metadata_id
                    LEFT JOIN playlist_signals ps ON ps.metadata_id = t.metadata_id
                    LEFT JOIN timbre_features tf ON tf.metadata_id = t.metadata_id
                    WHERE t.metadata_id IN ({placeholders})
                    """,
                    missing_seed_ids,
                ).fetchall()
                rows = [*seed_candidates, *rows]

            seed_rank = {mid: idx for idx, mid in enumerate(seed_ids)}
            rows = sorted(rows, key=lambda row: seed_rank.get(row["metadata_id"], len(seed_ids)))

        rows = _dedup_candidates(rows)

        use_seed_similarity = False
        if seed_ids and seed_facets:
            placeholders = ",".join("?" for _ in seed_ids)
            seed_rows = conn.execute(
                f"""
                SELECT
                    t.metadata_id,
                    t.year,
                    mf.bpm_final,
                    mf.energy,
                    ps.camelot_key,
                    ml.genre_primary,
                    ml.mood_tags,
                    (
                        SELECT e.vector
                        FROM embeddings e
                        WHERE e.metadata_id = t.metadata_id
                          AND e.scope = 'global'
                        ORDER BY e.id DESC
                        LIMIT 1
                    ) AS embedding_vec,
                    tf.mfcc_mean AS timbre_vec
                FROM tracks t
                LEFT JOIN musical_features mf ON mf.metadata_id = t.metadata_id
                LEFT JOIN ml_features ml ON ml.metadata_id = t.metadata_id
                LEFT JOIN playlist_signals ps ON ps.metadata_id = t.metadata_id
                LEFT JOIN timbre_features tf ON tf.metadata_id = t.metadata_id
                WHERE t.metadata_id IN ({placeholders})
                """,
                seed_ids,
            ).fetchall()
            seeds_data = [dict(row) for row in seed_rows]
            if seeds_data:
                if seed_year_override is not None:
                    for seed_data in seeds_data:
                        seed_data["year"] = str(seed_year_override)
                if "year" in seed_facets and seed_year_window is not None:
                    seed_years = [
                        year
                        for year in (_as_year(seed_data.get("year")) for seed_data in seeds_data)
                        if year is not None
                    ]
                    if seed_years:
                        rows = [
                            row
                            for row in rows
                            if row["metadata_id"] in seed_ids
                            or (
                                (cand_year := _as_year(dict(row).get("year"))) is not None
                                and any(abs(cand_year - seed_year) <= seed_year_window for seed_year in seed_years)
                            )
                        ]
                rows = sorted(
                    rows,
                    key=lambda row: _multi_seed_similarity_score(seeds_data, dict(row), seed_facets),
                    reverse=True,
                )
                seed_rank = {mid: idx for idx, mid in enumerate(seed_ids)}
                rows = sorted(
                    rows,
                    key=lambda row: (0, seed_rank[row["metadata_id"]]) if row["metadata_id"] in seed_rank else (1, 0),
                )
                use_seed_similarity = True

        # For harmonic sort, fetch Camelot keys and reorder in Python
        if use_seed_similarity:
            track_ids = [r["metadata_id"] for r in rows[:limit]]
        elif sort_by == "harmonic":
            track_ids = [r["metadata_id"] for r in rows[:limit * 3]]
            if track_ids:
                placeholders = ",".join("?" * len(track_ids))
                key_rows = conn.execute(
                    f"""
                    SELECT t.metadata_id, ps.camelot_key, mf.bpm_final
                    FROM tracks t
                    LEFT JOIN playlist_signals ps ON ps.metadata_id = t.metadata_id
                    LEFT JOIN musical_features mf ON mf.metadata_id = t.metadata_id
                    WHERE t.metadata_id IN ({placeholders})
                    """,
                    track_ids,
                ).fetchall()
                pool = {r["metadata_id"]: dict(r) for r in key_rows}
                ordered = _greedy_camelot_chain(pool, limit)
                track_ids = ordered
            else:
                track_ids = []
        elif sort_by == "energy_arc":
            candidate_ids = [r["metadata_id"] for r in rows[:limit]]
            if candidate_ids:
                placeholders = ",".join("?" * len(candidate_ids))
                e_rows = conn.execute(
                    f"SELECT metadata_id, energy FROM musical_features WHERE metadata_id IN ({placeholders})",
                    candidate_ids,
                ).fetchall()
                energy_map = {r["metadata_id"]: r["energy"] or 0 for r in e_rows}
                by_energy = sorted(candidate_ids, key=lambda mid: energy_map.get(mid, 0))
                peak = int(len(by_energy) * 0.6)
                track_ids = by_energy[:peak] + list(reversed(by_energy[peak:]))
            else:
                track_ids = []
        else:
            track_ids = [r["metadata_id"] for r in rows[:limit]]

        pid = generate_metadata_id()
        return _save_playlist(conn, pid, name, description, self.strategy_name, params, track_ids)


def _greedy_camelot_chain(pool: dict[str, dict], limit: int) -> list[str]:
    """Build a greedy harmonic chain from a pool of tracks with Camelot keys."""
    if not pool:
        return []
    remaining = dict(pool)
    first_id = next(iter(remaining))
    chain = [first_id]
    current = remaining.pop(first_id)
    current_key = current.get("camelot_key", "")

    while len(chain) < limit and remaining:
        best_id = None
        best_bpm_diff = 999.0
        for mid, info in remaining.items():
            ckey = info.get("camelot_key", "")
            if camelot_compatible(current_key, ckey):
                bpm_diff = abs((current.get("bpm_final") or 0) - (info.get("bpm_final") or 0))
                if bpm_diff < best_bpm_diff:
                    best_id = mid
                    best_bpm_diff = bpm_diff
        if best_id is None:
            best_id = min(remaining, key=lambda mid: abs((current.get("bpm_final") or 0) - (remaining[mid].get("bpm_final") or 0)))
        current = remaining.pop(best_id)
        current_key = current.get("camelot_key", "")
        chain.append(best_id)

    return chain


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

STRATEGY_MAP: dict[str, PlaylistStrategy] = {
    "genre": GenrePlaylist(),
    "bpm_range": BpmRangePlaylist(),
    "year_range": YearRangePlaylist(),
    "camelot_chain": CamelotChainPlaylist(),
    "energy_arc": EnergyArcPlaylist(),
    "decade": DecadePlaylist(),
    "mood": MoodPlaylist(),
    "custom": CustomPlaylist(),
}

# Lazy-register DJ flow strategy to avoid circular imports
def _register_dj_flow() -> None:
    from musesleuth.dj_optimizer import DjFlowPlaylist
    STRATEGY_MAP["dj_flow"] = DjFlowPlaylist()

_register_dj_flow()


def generate_playlist(
    conn: sqlite3.Connection,
    strategy: str,
    name: str,
    params: dict,
    *,
    limit: int = 50,
    description: Optional[str] = None,
) -> PlaylistResult:
    """Public entry point: pick a strategy and generate a playlist."""
    impl = STRATEGY_MAP.get(strategy)
    if impl is None:
        raise ValueError(f"Unknown strategy '{strategy}'. Choose from: {', '.join(STRATEGY_MAP)}")
    return impl.generate(conn, name, params, limit=limit, description=description)
