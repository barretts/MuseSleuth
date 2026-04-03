"""Generate curated, multi-dimensional playlists.

Each playlist targets a specific environment, event, or activity by combining
genre, mood, BPM, energy, decade, danceability, and harmonic compatibility.
"""
from __future__ import annotations

import json
import random
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from musesleuth.db import get_connection, generate_metadata_id
from musesleuth.playlist_generator import camelot_compatible, _dedup_candidates, _save_playlist


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _query_pool(
    conn: sqlite3.Connection,
    *,
    genres: list[str] | None = None,
    moods: list[str] | None = None,
    mood_exclude: list[str] | None = None,
    bpm_min: float = 0,
    bpm_max: float = 999,
    energy_min: float = 0.0,
    energy_max: float = 1.0,
    decades: list[str] | None = None,
    danceability_min: float = 0.0,
    confidence_min: float = 0.0,
    lastfm_tags: list[str] | None = None,
    acousticness_min: float = 0.0,
    acousticness_max: float = 1.0,
) -> list[sqlite3.Row]:
    """Fetch a candidate pool with multi-dimensional filtering."""
    clauses = [
        "mf.bpm_final BETWEEN ? AND ?",
        "mf.energy BETWEEN ? AND ?",
    ]
    params: list = [bpm_min, bpm_max, energy_min, energy_max]

    if genres:
        placeholders = ",".join("?" * len(genres))
        clauses.append(f"ml.genre_primary IN ({placeholders})")
        params.extend(genres)

    if moods:
        mood_clauses = [f"ml.mood_tags LIKE ?" for _ in moods]
        clauses.append(f"({' OR '.join(mood_clauses)})")
        params.extend(f"%{m}%" for m in moods)

    if mood_exclude:
        for m in mood_exclude:
            clauses.append("(ml.mood_tags NOT LIKE ? OR ml.mood_tags IS NULL)")
            params.append(f"%{m}%")

    if decades:
        placeholders = ",".join("?" * len(decades))
        clauses.append(f"ps.decade_bucket IN ({placeholders})")
        params.extend(decades)

    if danceability_min > 0:
        clauses.append("ml.danceability >= ?")
        params.append(danceability_min)

    if confidence_min > 0:
        clauses.append("ml.genre_confidence >= ?")
        params.append(confidence_min)

    if acousticness_min > 0:
        clauses.append("ml.acousticness >= ?")
        params.append(acousticness_min)

    if acousticness_max < 1.0:
        clauses.append("ml.acousticness <= ?")
        params.append(acousticness_max)

    where = " AND ".join(clauses)

    sql = f"""
        SELECT t.metadata_id, t.title, t.artist,
               mf.bpm_final, mf.energy, mf.key_name,
               ps.camelot_key, ps.energy_tier, ps.decade_bucket, ps.duplicate_group, ps.remix_group,
               ml.genre_primary, ml.genre_confidence, ml.danceability, ml.mood_tags,
               ml.acousticness, ml.electronic_score,
               COALESCE(ast.listeners, 0) as listeners
        FROM tracks t
        JOIN musical_features mf ON mf.metadata_id = t.metadata_id
        JOIN ml_features ml ON ml.metadata_id = t.metadata_id
        LEFT JOIN playlist_signals ps ON ps.metadata_id = t.metadata_id
        LEFT JOIN artist_stats ast ON ast.metadata_id = t.metadata_id AND ast.source = 'lastfm'
        WHERE {where}
    """
    pool = conn.execute(sql, params).fetchall()

    # Optional: further filter by lastfm_artist tags
    if lastfm_tags:
        tag_mids = set()
        for tag in lastfm_tags:
            rows = conn.execute(
                "SELECT DISTINCT metadata_id FROM genres_tags WHERE source = 'lastfm_artist' AND tag_value = ? COLLATE NOCASE",
                (tag,),
            ).fetchall()
            tag_mids.update(r["metadata_id"] for r in rows)
        pool = [r for r in pool if r["metadata_id"] in tag_mids]

    return pool


def _sort_by_energy_arc(tracks: list[dict], peak_pos: float = 0.6) -> list[dict]:
    """Reorder tracks in an energy arc: build up -> peak -> cool down."""
    by_energy = sorted(tracks, key=lambda t: t["energy"] or 0)
    n = len(by_energy)
    peak_idx = int(n * peak_pos)
    ascending = by_energy[:peak_idx]
    descending = list(reversed(by_energy[peak_idx:]))
    return ascending + descending


def _sort_by_bpm_smooth(tracks: list[dict]) -> list[dict]:
    """Sort by BPM for smooth transitions."""
    return sorted(tracks, key=lambda t: t["bpm_final"] or 0)


def _sort_harmonic_chain(tracks: list[dict]) -> list[dict]:
    """Greedy nearest-neighbor Camelot chain for harmonic mixing."""
    if not tracks:
        return tracks
    remaining = list(tracks)
    chain = [remaining.pop(0)]
    while remaining:
        current_key = chain[-1].get("camelot_key", "")
        best_idx = None
        best_bpm_diff = 999
        for i, t in enumerate(remaining):
            if camelot_compatible(current_key, t.get("camelot_key", "")):
                bpm_diff = abs((chain[-1].get("bpm_final") or 0) - (t.get("bpm_final") or 0))
                if bpm_diff < best_bpm_diff:
                    best_idx = i
                    best_bpm_diff = bpm_diff
        if best_idx is not None:
            chain.append(remaining.pop(best_idx))
        else:
            # no harmonic match, pick closest BPM
            remaining.sort(key=lambda t: abs((chain[-1].get("bpm_final") or 0) - (t.get("bpm_final") or 0)))
            chain.append(remaining.pop(0))
    return chain


def _select_diverse(pool: list[sqlite3.Row], limit: int, *, favor_popular: bool = False) -> list[dict]:
    """Deduplicate and select tracks, optionally favoring popular ones."""
    pool = _dedup_candidates(pool)
    items = [dict(r) for r in pool]
    if favor_popular:
        items.sort(key=lambda t: t.get("listeners", 0), reverse=True)
    else:
        # Blend: top half by confidence, shuffle for variety
        items.sort(key=lambda t: t.get("genre_confidence", 0), reverse=True)
        top = items[:limit * 3]
        random.shuffle(top)
        items = top
    return items[:limit]


# ---------------------------------------------------------------------------
# Playlist definitions
# ---------------------------------------------------------------------------

PLAYLISTS = [
    {
        "name": "Late Night Psy Journey",
        "description": "Deep psychedelic trance for the after-hours. Dark, hypnotic, relentless 140+ BPM. Eyes closed, bass heavy, lost in the groove.",
        "strategy": "genre",
        "params": {
            "genres": ["psy_trance"],
            "moods": ["dark"],
            "bpm_min": 138, "bpm_max": 148,
            "confidence_min": 0.7,
        },
        "limit": 60,
        "sort": "harmonic",
    },
    {
        "name": "90s Eurodance Revival",
        "description": "Pure 90s nostalgia. Eurodance anthems with bouncing basslines and catchy hooks. Perfect for themed nights and throwback parties.",
        "strategy": "decade",
        "params": {
            "decades": ["1990s"],
            "lastfm_tags": ["eurodance"],
            "bpm_min": 125, "bpm_max": 155,
        },
        "limit": 50,
        "sort": "bpm",
        "favor_popular": True,
    },
    {
        "name": "Warehouse Minimal Session",
        "description": "Stripped-back minimal techno for concrete spaces. Dark, repetitive, industrial. Let the kick drum do the talking.",
        "strategy": "genre",
        "params": {
            "genres": ["minimal_techno", "dub_techno"],
            "moods": ["dark", "monotoned"],
            "bpm_min": 122, "bpm_max": 134,
            "energy_max": 0.4,
            "confidence_min": 0.6,
        },
        "limit": 50,
        "sort": "harmonic",
    },
    {
        "name": "Progressive Sunrise Set",
        "description": "Uplifting progressive trance for golden hour. Melodic layers building toward daybreak. The soundtrack to watching the sun come up at a festival.",
        "strategy": "genre",
        "params": {
            "genres": ["progressive_trance", "uplifting_trance"],
            "moods": ["uplifting"],
            "bpm_min": 128, "bpm_max": 142,
        },
        "limit": 50,
        "sort": "energy_arc",
    },
    {
        "name": "Downtempo Lounge",
        "description": "Ambient downtempo for cocktail bars and chill spaces. Low energy, high texture, smooth BPMs. Background music that rewards attention.",
        "strategy": "genre",
        "params": {
            "genres": ["downtempo"],
            "moods": ["calm", "relaxed"],
            "bpm_min": 80, "bpm_max": 120,
            "energy_max": 0.25,
            "acousticness_min": 0.4,
        },
        "limit": 50,
        "sort": "bpm",
    },
    {
        "name": "Peak Hour Bangers",
        "description": "Maximum energy, maximum danceability. The 2 AM moment when the floor is packed and the DJ goes all in. Genre-agnostic mayhem.",
        "strategy": "mood",
        "params": {
            "moods": ["energetic", "danceable"],
            "bpm_min": 130, "bpm_max": 150,
            "energy_min": 0.35,
            "danceability_min": 0.75,
        },
        "limit": 60,
        "sort": "energy_arc",
        "favor_popular": True,
    },
    {
        "name": "Dark & Hypnotic",
        "description": "Monotonous, dark, minimal. For headphone listening at 3 AM or driving empty highways. Repetition as meditation.",
        "strategy": "mood",
        "params": {
            "genres": ["minimal_techno", "dub_techno", "psy_trance"],
            "moods": ["dark", "monotoned"],
            "mood_exclude": ["uplifting", "danceable"],
            "bpm_min": 120, "bpm_max": 140,
            "energy_max": 0.3,
        },
        "limit": 50,
        "sort": "bpm",
    },
    {
        "name": "2000s Trance Classics",
        "description": "The golden era of trance. 2000s anthems that defined a generation of ravers. Euphoric, melodic, unforgettable.",
        "strategy": "decade",
        "params": {
            "genres": ["psy_trance", "progressive_trance", "uplifting_trance"],
            "decades": ["2000s"],
            "bpm_min": 130, "bpm_max": 145,
        },
        "limit": 60,
        "sort": "harmonic",
        "favor_popular": True,
    },
    {
        "name": "Liquid DnB Smooth Ride",
        "description": "Silky liquid drum & bass for long drives and late work sessions. Rolling basslines, jazzy samples, effortless flow.",
        "strategy": "genre",
        "params": {
            "genres": ["liquid"],
            "bpm_min": 160, "bpm_max": 180,
        },
        "limit": 50,
        "sort": "harmonic",
    },
    {
        "name": "Bass Weight",
        "description": "Heavy bass music: dubstep, bass, trap. Sub-rattling low end for sound system culture. Not for the faint-hearted.",
        "strategy": "genre",
        "params": {
            "genres": ["dubstep", "bass", "trap"],
            "moods": ["dark", "energetic"],
            "bpm_min": 130, "bpm_max": 155,
        },
        "limit": 50,
        "sort": "energy_arc",
    },
    {
        "name": "Festival Warm-Up",
        "description": "The opening set. Progressive builds, teasing energy, setting the mood before the headliners. Patient and purposeful.",
        "strategy": "mood",
        "params": {
            "moods": ["uplifting"],
            "mood_exclude": ["monotoned"],
            "bpm_min": 124, "bpm_max": 138,
            "energy_min": 0.15,
            "energy_max": 0.45,
            "danceability_min": 0.65,
        },
        "limit": 50,
        "sort": "energy_arc",
    },
    {
        "name": "2010s Electronic Anthems",
        "description": "The decade that took EDM mainstream. Festival-ready, big-room energy, from progressive house to trance. The sound of main stages worldwide.",
        "strategy": "decade",
        "params": {
            "decades": ["2010s"],
            "moods": ["energetic", "uplifting", "danceable"],
            "bpm_min": 126, "bpm_max": 140,
            "danceability_min": 0.7,
        },
        "limit": 60,
        "sort": "harmonic",
        "favor_popular": True,
    },
    {
        "name": "Sunday Morning Recovery",
        "description": "Gentle, calm, low-energy. For the morning after. Downtempo and ambient textures that don't ask anything of you.",
        "strategy": "mood",
        "params": {
            "genres": ["downtempo"],
            "moods": ["calm", "relaxed"],
            "mood_exclude": ["energetic", "danceable"],
            "bpm_min": 60, "bpm_max": 115,
            "energy_max": 0.2,
        },
        "limit": 40,
        "sort": "bpm",
    },
    {
        "name": "Harmonic Mix: 8A Trance Flow",
        "description": "A harmonically perfect DJ mix starting from 8A (the most popular key in the collection). Every transition is Camelot-compatible. Pure trance.",
        "strategy": "camelot_chain",
        "params": {
            "genres": ["psy_trance", "progressive_trance"],
            "bpm_min": 134, "bpm_max": 144,
            "start_key": "8A",
            "confidence_min": 0.6,
        },
        "limit": 40,
        "sort": "harmonic",
    },
    {
        "name": "High-Danceability Floor Fillers",
        "description": "Tracks with the highest danceability scores across all genres. When the only metric that matters is whether people move.",
        "strategy": "mood",
        "params": {
            "danceability_min": 0.82,
            "bpm_min": 120, "bpm_max": 145,
        },
        "limit": 50,
        "sort": "harmonic",
        "favor_popular": True,
    },
]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    db_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("music_new.db")
    conn = get_connection(db_path)
    random.seed(42)

    created = 0
    for spec in PLAYLISTS:
        p = spec["params"]
        pool = _query_pool(
            conn,
            genres=p.get("genres"),
            moods=p.get("moods"),
            mood_exclude=p.get("mood_exclude"),
            bpm_min=p.get("bpm_min", 0),
            bpm_max=p.get("bpm_max", 999),
            energy_min=p.get("energy_min", 0.0),
            energy_max=p.get("energy_max", 1.0),
            decades=p.get("decades"),
            danceability_min=p.get("danceability_min", 0.0),
            confidence_min=p.get("confidence_min", 0.0),
            lastfm_tags=p.get("lastfm_tags"),
            acousticness_min=p.get("acousticness_min", 0.0),
            acousticness_max=p.get("acousticness_max", 1.0),
        )

        if not pool:
            print(f"  SKIP  {spec['name']} -- no matching tracks")
            continue

        tracks = _select_diverse(pool, spec["limit"], favor_popular=spec.get("favor_popular", False))

        sort_method = spec.get("sort", "bpm")
        if sort_method == "harmonic":
            tracks = _sort_harmonic_chain(tracks)
        elif sort_method == "energy_arc":
            tracks = _sort_by_energy_arc(tracks)
        else:
            tracks = _sort_by_bpm_smooth(tracks)

        track_ids = [t["metadata_id"] for t in tracks]
        pid = generate_metadata_id()
        result = _save_playlist(
            conn, pid, spec["name"], spec.get("description"),
            spec.get("strategy", "genre"), spec["params"], track_ids,
        )

        # Summary stats
        bpms = [t["bpm_final"] for t in tracks if t.get("bpm_final")]
        energies = [t["energy"] for t in tracks if t.get("energy")]
        keys = [t["camelot_key"] for t in tracks if t.get("camelot_key")]
        genres_dist: dict[str, int] = {}
        for t in tracks:
            g = t.get("genre_primary", "?")
            genres_dist[g] = genres_dist.get(g, 0) + 1
        top_genres = sorted(genres_dist.items(), key=lambda x: -x[1])[:3]

        bpm_range = f"{min(bpms):.0f}-{max(bpms):.0f}" if bpms else "?"
        energy_range = f"{min(energies):.2f}-{max(energies):.2f}" if energies else "?"
        unique_keys = len(set(keys))

        print(f"  [OK]  {result.name}")
        print(f"        {result.track_count} tracks | BPM {bpm_range} | Energy {energy_range} | {unique_keys} keys")
        print(f"        Genres: {', '.join(f'{g}({c})' for g, c in top_genres)}")
        print(f"        Pool size: {len(pool)}")
        print()
        created += 1

    print(f"Done. {created} playlists created.")
    conn.close()


if __name__ == "__main__":
    main()
