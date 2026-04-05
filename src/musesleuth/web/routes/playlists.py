"""Playlist listing, generation, detail, and export API routes."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from musesleuth.db import generate_metadata_id

router = APIRouter(prefix="/playlists")

SORT_OPTIONS = [
    ("position", "Original Order"),
    ("bpm_asc", "BPM (Low \u2192 High)"),
    ("bpm_desc", "BPM (High \u2192 Low)"),
    ("energy_asc", "Energy (Build Up)"),
    ("energy_desc", "Energy (Cool Down)"),
    ("key", "Key (Camelot Wheel)"),
    ("genre", "Group by Genre"),
    ("artist", "Artist A\u2013Z"),
]


def _camelot_sort_key(camelot: str | None) -> tuple[int, str]:
    """Parse a Camelot key like '8A' into (8, 'A') for numeric ordering."""
    if not camelot:
        return (99, "Z")
    try:
        return (int(camelot[:-1]), camelot[-1])
    except (ValueError, IndexError):
        return (99, "Z")


def _sort_tracks(tracks: list[dict], sort: str) -> list[dict]:
    """Sort a track list by the chosen sort preset."""
    if sort == "bpm_asc":
        return sorted(tracks, key=lambda t: (t.get("bpm_final") or 9999,))
    if sort == "bpm_desc":
        return sorted(tracks, key=lambda t: (-(t.get("bpm_final") or 0),))
    if sort == "energy_asc":
        return sorted(tracks, key=lambda t: (t.get("energy") or 0,))
    if sort == "energy_desc":
        return sorted(tracks, key=lambda t: (-(t.get("energy") or 0),))
    if sort == "key":
        return sorted(tracks, key=lambda t: _camelot_sort_key(t.get("camelot_key")))
    if sort == "genre":
        return sorted(tracks, key=lambda t: (t.get("genre_primary") or "zzz", t.get("artist") or "", t.get("title") or ""))
    if sort == "artist":
        return sorted(tracks, key=lambda t: ((t.get("artist") or "zzz").lower(), (t.get("title") or "").lower()))
    return tracks

STRATEGY_CHOICES = ["custom", "genre", "bpm_range", "year_range", "camelot_chain", "energy_arc", "decade", "mood"]


@router.get("")
async def list_playlists(
    request: Request,
    q: str = "",
    strategy_filter: str = "",
):
    """Return playlists plus options for filters and generation."""
    db = request.app.state.db

    where_clauses: list[str] = []
    params: list[str] = []

    if q:
        where_clauses.append("name LIKE ?")
        params.append(f"%{q}%")

    if strategy_filter:
        where_clauses.append("strategy = ?")
        params.append(strategy_filter)

    where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

    playlists = db.execute(
        f"SELECT playlist_id, name, strategy, track_count, created_at "
        f"FROM playlists WHERE {where_sql} ORDER BY created_at DESC",
        params,
    ).fetchall()

    genres = _distinct(db, "ml_features", "genre_primary")
    decades = _distinct(db, "playlist_signals", "decade_bucket")
    strategies_used = _distinct(db, "playlists", "strategy")

    moods = ["energetic", "calm", "relaxed", "dark", "bright",
             "uplifting", "danceable", "melancholic", "dynamic", "monotoned"]

    return {
        "playlists": [dict(r) for r in playlists],
        "strategies": STRATEGY_CHOICES,
        "strategies_used": strategies_used,
        "genres": genres,
        "decades": decades,
        "moods": moods,
        "q": q,
        "strategy_filter": strategy_filter,
    }


@router.post("/generate")
async def generate_playlist_route(request: Request):
    """Generate a playlist from JSON payload and return its id."""
    from musesleuth.playlist_generator import generate_playlist

    db = request.app.state.db
    payload = await request.json()

    strategy = str(payload.get("strategy", "genre"))
    name = str(payload.get("name", "Untitled Playlist"))
    limit = int(payload.get("limit", 50))

    params: dict[str, str | float] = {}
    if strategy == "custom":
        for key in ("year_min", "year_max", "bpm_min", "bpm_max",
                     "energy_min", "energy_max", "danceability_min", "confidence_min",
                     "popularity_min", "popularity_metric"):
            val = payload.get(key, "")
            if val:
                params[key] = val
        genres_list = payload.get("genres", [])
        if genres_list:
            params["genres"] = ",".join(genres_list)
        moods_list = payload.get("moods", [])
        if moods_list:
            params["moods"] = ",".join(moods_list)
        mood_exclude_list = payload.get("mood_exclude", [])
        if mood_exclude_list:
            params["mood_exclude"] = ",".join(mood_exclude_list)
        params["sort_by"] = str(payload.get("sort_by", "popularity"))
    elif strategy == "genre":
        params["genre"] = str(payload.get("genre", ""))
    elif strategy == "bpm_range":
        params["bpm_min"] = float(payload.get("bpm_min", 0))
        params["bpm_max"] = float(payload.get("bpm_max", 999))
    elif strategy == "year_range":
        params["year_min"] = int(payload.get("year_min", 1900))
        params["year_max"] = int(payload.get("year_max", 2099))
        params["sort_by"] = str(payload.get("year_sort_by", "year"))
    elif strategy == "decade":
        params["decade"] = str(payload.get("decade", ""))
    elif strategy == "mood":
        params["mood"] = str(payload.get("mood", ""))
    elif strategy == "energy_arc":
        params["peak_position"] = float(payload.get("peak_position", 0.6))
    elif strategy == "camelot_chain":
        params["seed_id"] = str(payload.get("seed_id", ""))

    result = generate_playlist(db, strategy, name, params, limit=limit)
    return {"playlist_id": result.playlist_id}


@router.get("/{playlist_id}")
async def playlist_detail(request: Request, playlist_id: str, sort: str = "position"):
    """Return one playlist with tracks and summary stats."""
    db = request.app.state.db

    pl = db.execute(
        "SELECT * FROM playlists WHERE playlist_id = ?", (playlist_id,)
    ).fetchone()
    if not pl:
        raise HTTPException(status_code=404, detail="Playlist not found")

    tracks = db.execute(
        """
        SELECT pt.position, t.metadata_id, t.title, t.artist, t.album, t.year,
               mf.bpm_final, mf.energy, ps.camelot_key, ps.energy_tier,
               ml.genre_primary,
               tf.duration_ms
        FROM playlist_tracks pt
        JOIN tracks t ON t.metadata_id = pt.metadata_id
        LEFT JOIN musical_features mf ON mf.metadata_id = pt.metadata_id
        LEFT JOIN playlist_signals ps ON ps.metadata_id = pt.metadata_id
        LEFT JOIN ml_features ml ON ml.metadata_id = pt.metadata_id
        LEFT JOIN technical_features tf ON tf.metadata_id = pt.metadata_id
        WHERE pt.playlist_id = ?
        ORDER BY pt.position ASC
        """,
        (playlist_id,),
    ).fetchall()
    tracks_list = [dict(r) for r in tracks]

    if sort != "position":
        tracks_list = _sort_tracks(tracks_list, sort)

    durations = [t["duration_ms"] for t in tracks_list if t.get("duration_ms")]
    bpms = [t["bpm_final"] for t in tracks_list if t.get("bpm_final")]
    keys = [t["camelot_key"] for t in tracks_list if t.get("camelot_key")]

    total_duration_ms = sum(durations)
    total_mins = total_duration_ms // 60000
    total_secs = (total_duration_ms % 60000) // 1000

    key_counts: dict[str, int] = {}
    for k in keys:
        key_counts[k] = key_counts.get(k, 0) + 1
    dominant_keys = sorted(key_counts.items(), key=lambda x: -x[1])[:3]

    stats = {
        "total_duration": f"{total_mins}m {total_secs}s",
        "bpm_min": round(min(bpms)) if bpms else None,
        "bpm_max": round(max(bpms)) if bpms else None,
        "dominant_keys": dominant_keys,
    }

    return {
        "playlist": dict(pl),
        "tracks": tracks_list,
        "stats": stats,
        "sort": sort,
        "sort_options": SORT_OPTIONS,
    }


@router.get("/{playlist_id}/export")
async def export_playlist_m3u8(request: Request, playlist_id: str):
    """Download the playlist as an M3U8 file."""
    from musesleuth.playlist_export import export_m3u8

    db = request.app.state.db

    pl = db.execute(
        "SELECT name FROM playlists WHERE playlist_id = ?", (playlist_id,)
    ).fetchone()
    if not pl:
        raise HTTPException(status_code=404, detail="Playlist not found")

    content = export_m3u8(db, playlist_id)
    safe_name = "".join(c for c in pl["name"] if c.isalnum() or c in " _-").strip()
    filename = f"{safe_name or 'playlist'}.m3u8"

    return Response(
        content=content,
        media_type="audio/x-mpegurl",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/{playlist_id}")
async def delete_playlist(request: Request, playlist_id: str):
    """Delete a playlist and all of its track links."""
    from musesleuth.subsonic import delete_playlist_with_remote

    db = request.app.state.db
    pl = db.execute("SELECT playlist_id FROM playlists WHERE playlist_id = ?", (playlist_id,)).fetchone()
    if not pl:
        raise HTTPException(status_code=404, detail="Playlist not found")

    try:
        delete_playlist_with_remote(db, playlist_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ok": True}


@router.post("/{playlist_id}/clone")
async def clone_playlist(request: Request, playlist_id: str):
    """Clone a playlist and its current order into a new playlist."""
    db = request.app.state.db
    payload = await request.json()

    source = db.execute("SELECT * FROM playlists WHERE playlist_id = ?", (playlist_id,)).fetchone()
    if not source:
        raise HTTPException(status_code=404, detail="Playlist not found")

    tracks = db.execute(
        "SELECT metadata_id, position FROM playlist_tracks WHERE playlist_id = ? ORDER BY position",
        (playlist_id,),
    ).fetchall()
    new_playlist_id = generate_metadata_id()
    source_name = source["name"] or "Untitled Playlist"
    clone_name = str(payload.get("name", f"{source_name} (Copy)"))

    db.execute(
        """
        INSERT INTO playlists (playlist_id, name, description, strategy, strategy_params, track_count)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            new_playlist_id,
            clone_name,
            source["description"],
            source["strategy"],
            source["strategy_params"],
            len(tracks),
        ),
    )
    for row in tracks:
        db.execute(
            "INSERT INTO playlist_tracks (playlist_id, metadata_id, position) VALUES (?, ?, ?)",
            (new_playlist_id, row["metadata_id"], row["position"]),
        )
    db.commit()
    return {"playlist_id": new_playlist_id}


@router.get("/{playlist_id}/track-candidates")
async def playlist_track_candidates(
    request: Request,
    playlist_id: str,
    q: str = "",
    limit: int = 20,
):
    """Search tracks that are not already in the playlist."""
    db = request.app.state.db
    pl = db.execute("SELECT playlist_id FROM playlists WHERE playlist_id = ?", (playlist_id,)).fetchone()
    if not pl:
        raise HTTPException(status_code=404, detail="Playlist not found")

    search = f"%{q.strip()}%" if q.strip() else "%"
    rows = db.execute(
        """
        SELECT t.metadata_id, t.title, t.artist, t.album, mf.bpm_final, ps.camelot_key
        FROM tracks t
        LEFT JOIN musical_features mf ON mf.metadata_id = t.metadata_id
        LEFT JOIN playlist_signals ps ON ps.metadata_id = t.metadata_id
        WHERE t.metadata_id NOT IN (
            SELECT metadata_id FROM playlist_tracks WHERE playlist_id = ?
        )
          AND (
              ? = '%'
              OR t.title LIKE ?
              OR t.artist LIKE ?
              OR t.album LIKE ?
              OR t.metadata_id LIKE ?
          )
        ORDER BY
          CASE WHEN t.title IS NULL OR t.title = '' THEN 1 ELSE 0 END,
          t.title ASC
        LIMIT ?
        """,
        (playlist_id, search, search, search, search, search, max(1, min(limit, 100))),
    ).fetchall()
    return {"tracks": [dict(r) for r in rows]}


@router.post("/{playlist_id}/tracks/add")
async def add_track_to_playlist(request: Request, playlist_id: str):
    """Append a track to the end of the playlist if not present."""
    db = request.app.state.db
    payload = await request.json()
    metadata_id = str(payload.get("metadata_id", "")).strip()
    if not metadata_id:
        raise HTTPException(status_code=400, detail="metadata_id is required")

    pl = db.execute("SELECT playlist_id FROM playlists WHERE playlist_id = ?", (playlist_id,)).fetchone()
    if not pl:
        raise HTTPException(status_code=404, detail="Playlist not found")
    track = db.execute("SELECT metadata_id FROM tracks WHERE metadata_id = ?", (metadata_id,)).fetchone()
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")
    existing = db.execute(
        "SELECT 1 FROM playlist_tracks WHERE playlist_id = ? AND metadata_id = ?",
        (playlist_id, metadata_id),
    ).fetchone()
    if existing:
        raise HTTPException(status_code=409, detail="Track already in playlist")

    max_pos_row = db.execute(
        "SELECT COALESCE(MAX(position), 0) AS max_pos FROM playlist_tracks WHERE playlist_id = ?",
        (playlist_id,),
    ).fetchone()
    next_pos = int(max_pos_row["max_pos"]) + 1
    db.execute(
        "INSERT INTO playlist_tracks (playlist_id, metadata_id, position) VALUES (?, ?, ?)",
        (playlist_id, metadata_id, next_pos),
    )
    _refresh_playlist_track_count(db, playlist_id)
    db.commit()
    return {"ok": True}


@router.delete("/{playlist_id}/tracks/{metadata_id}")
async def remove_track_from_playlist(request: Request, playlist_id: str, metadata_id: str):
    """Remove a track from playlist and compact positions."""
    db = request.app.state.db
    row = db.execute(
        "SELECT position FROM playlist_tracks WHERE playlist_id = ? AND metadata_id = ?",
        (playlist_id, metadata_id),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Track not found in playlist")
    position = int(row["position"])

    db.execute(
        "DELETE FROM playlist_tracks WHERE playlist_id = ? AND metadata_id = ?",
        (playlist_id, metadata_id),
    )
    db.execute(
        "UPDATE playlist_tracks SET position = position - 1 WHERE playlist_id = ? AND position > ?",
        (playlist_id, position),
    )
    _refresh_playlist_track_count(db, playlist_id)
    db.commit()
    return {"ok": True}


@router.post("/{playlist_id}/tracks/{metadata_id}/move")
async def move_track_in_playlist(request: Request, playlist_id: str, metadata_id: str):
    """Move a track up/down one slot by swapping positions."""
    db = request.app.state.db
    payload = await request.json()
    direction = str(payload.get("direction", "")).lower()
    if direction not in {"up", "down"}:
        raise HTTPException(status_code=400, detail="direction must be 'up' or 'down'")

    current = db.execute(
        "SELECT position FROM playlist_tracks WHERE playlist_id = ? AND metadata_id = ?",
        (playlist_id, metadata_id),
    ).fetchone()
    if not current:
        raise HTTPException(status_code=404, detail="Track not found in playlist")
    cur_pos = int(current["position"])
    target_pos = cur_pos - 1 if direction == "up" else cur_pos + 1

    other = db.execute(
        "SELECT metadata_id FROM playlist_tracks WHERE playlist_id = ? AND position = ?",
        (playlist_id, target_pos),
    ).fetchone()
    if not other:
        return {"ok": True}  # already at boundary

    db.execute(
        "UPDATE playlist_tracks SET position = 0 WHERE playlist_id = ? AND metadata_id = ?",
        (playlist_id, metadata_id),
    )
    db.execute(
        "UPDATE playlist_tracks SET position = ? WHERE playlist_id = ? AND metadata_id = ?",
        (cur_pos, playlist_id, other["metadata_id"]),
    )
    db.execute(
        "UPDATE playlist_tracks SET position = ? WHERE playlist_id = ? AND metadata_id = ?",
        (target_pos, playlist_id, metadata_id),
    )
    db.commit()
    return {"ok": True}


@router.post("/{playlist_id}/tracks/{metadata_id}/reorder")
async def reorder_track_in_playlist(request: Request, playlist_id: str, metadata_id: str):
    """Move a track to an absolute position in the playlist."""
    db = request.app.state.db
    payload = await request.json()

    current = db.execute(
        "SELECT position FROM playlist_tracks WHERE playlist_id = ? AND metadata_id = ?",
        (playlist_id, metadata_id),
    ).fetchone()
    if not current:
        raise HTTPException(status_code=404, detail="Track not found in playlist")
    cur_pos = int(current["position"])

    max_row = db.execute(
        "SELECT COALESCE(MAX(position), 0) AS max_pos FROM playlist_tracks WHERE playlist_id = ?",
        (playlist_id,),
    ).fetchone()
    max_pos = int(max_row["max_pos"])
    target = int(payload.get("target_position", cur_pos))
    target = max(1, min(target, max_pos))

    if target == cur_pos:
        return {"ok": True}

    db.execute(
        "UPDATE playlist_tracks SET position = 0 WHERE playlist_id = ? AND metadata_id = ?",
        (playlist_id, metadata_id),
    )
    if target < cur_pos:
        db.execute(
            """
            UPDATE playlist_tracks
            SET position = position + 1
            WHERE playlist_id = ? AND position >= ? AND position < ?
            """,
            (playlist_id, target, cur_pos),
        )
    else:
        db.execute(
            """
            UPDATE playlist_tracks
            SET position = position - 1
            WHERE playlist_id = ? AND position > ? AND position <= ?
            """,
            (playlist_id, cur_pos, target),
        )
    db.execute(
        "UPDATE playlist_tracks SET position = ? WHERE playlist_id = ? AND metadata_id = ?",
        (target, playlist_id, metadata_id),
    )
    db.commit()
    return {"ok": True}


def _distinct(db, table: str, col: str) -> list[str]:
    rows = db.execute(
        f"SELECT DISTINCT {col} FROM {table} WHERE {col} IS NOT NULL ORDER BY {col}"
    ).fetchall()
    return [r[0] for r in rows]


def _refresh_playlist_track_count(db, playlist_id: str) -> None:
    row = db.execute(
        "SELECT COUNT(*) AS cnt FROM playlist_tracks WHERE playlist_id = ?",
        (playlist_id,),
    ).fetchone()
    db.execute(
        "UPDATE playlists SET track_count = ?, updated_at = datetime('now') WHERE playlist_id = ?",
        (int(row["cnt"]), playlist_id),
    )
