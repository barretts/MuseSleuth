"""Spotify playlist import routes: preview and create."""
from __future__ import annotations

import re
import unicodedata

from fastapi import APIRouter, HTTPException, Request

from musesleuth.db import generate_metadata_id

router = APIRouter(prefix="/playlists/import-spotify")


def _extract_playlist_id(url_or_id: str) -> str | None:
    """Return a bare Spotify playlist ID from a URL or ID string."""
    s = url_or_id.strip()
    match = re.search(r"playlist[/:]([A-Za-z0-9]+)", s)
    if match:
        return match.group(1)
    if re.fullmatch(r"[A-Za-z0-9]+", s):
        return s
    return None


def _normalize(text: str | None) -> str:
    """Lower-case, strip accents, collapse whitespace for fuzzy comparison."""
    if not text:
        return ""
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_str = nfkd.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", ascii_str).strip().lower()


def _auto_match(db, title: str | None, artist: str | None) -> tuple[dict | None, str]:
    """Try to find a local track matching title+artist.

    Returns (track_dict | None, confidence) where confidence is
    'exact', 'fuzzy', or 'none'.
    """
    norm_title = _normalize(title)
    norm_artist = _normalize(artist)

    if norm_title and norm_artist:
        rows = db.execute(
            """
            SELECT t.metadata_id, t.title, t.artist, t.album
            FROM tracks t
            WHERE lower(t.title) = ? AND lower(t.artist) = ?
            LIMIT 1
            """,
            (norm_title, norm_artist),
        ).fetchall()
        if rows:
            return dict(rows[0]), "exact"

    if norm_title and norm_artist:
        rows = db.execute(
            """
            SELECT t.metadata_id, t.title, t.artist, t.album
            FROM tracks t
            WHERE t.title LIKE ? AND t.artist LIKE ?
            ORDER BY
              CASE WHEN lower(t.title) = ? THEN 0 ELSE 1 END,
              CASE WHEN lower(t.artist) = ? THEN 0 ELSE 1 END
            LIMIT 1
            """,
            (f"%{norm_title}%", f"%{norm_artist}%", norm_title, norm_artist),
        ).fetchall()
        if rows:
            return dict(rows[0]), "fuzzy"

    if norm_title:
        rows = db.execute(
            """
            SELECT t.metadata_id, t.title, t.artist, t.album
            FROM tracks t
            WHERE t.title LIKE ?
            ORDER BY CASE WHEN lower(t.title) = ? THEN 0 ELSE 1 END
            LIMIT 1
            """,
            (f"%{norm_title}%", norm_title),
        ).fetchall()
        if rows:
            return dict(rows[0]), "fuzzy"

    return None, "none"


def _get_adapter(request: Request):
    """Return the SpotifyAdapter or None if not configured."""
    return getattr(request.app.state, "spotify_adapter", None)


@router.post("/preview")
async def preview_import(request: Request):
    """Fetch a Spotify playlist and auto-match tracks to local library."""
    adapter = _get_adapter(request)
    if adapter is None:
        raise HTTPException(
            status_code=503,
            detail="Spotify credentials not configured. Set MUSESLEUTH_SPOTIFY_CLIENT_ID and MUSESLEUTH_SPOTIFY_CLIENT_SECRET.",
        )

    payload = await request.json()
    raw = str(payload.get("playlist_url", "")).strip()
    if not raw:
        raise HTTPException(status_code=400, detail="playlist_url is required")

    playlist_id = _extract_playlist_id(raw)
    if not playlist_id:
        raise HTTPException(status_code=400, detail="Could not parse a Spotify playlist ID from the provided URL")

    info = adapter.get_playlist_info(playlist_id)
    if info is None:
        raise HTTPException(status_code=404, detail="Spotify playlist not found or not accessible")

    spotify_tracks = adapter.get_playlist_tracks(playlist_id)
    if spotify_tracks is None:
        raise HTTPException(status_code=502, detail="Failed to fetch Spotify playlist tracks")

    db = request.app.state.db

    result_tracks = []
    for idx, st in enumerate(spotify_tracks):
        match, confidence = _auto_match(db, st.get("title"), st.get("artist"))
        result_tracks.append(
            {
                "spotify_index": idx,
                "spotify_title": st.get("title"),
                "spotify_artist": st.get("artist"),
                "spotify_album": st.get("album"),
                "spotify_track_id": st.get("spotify_track_id"),
                "duration_ms": st.get("duration_ms"),
                "match": match,
                "match_confidence": confidence,
            }
        )

    return {
        "spotify_playlist_name": info.get("name") or "Spotify Playlist",
        "spotify_playlist_id": playlist_id,
        "tracks": result_tracks,
    }


@router.post("/create")
async def create_import(request: Request):
    """Create a local playlist from confirmed Spotify import matches."""
    payload = await request.json()
    name = str(payload.get("name", "")).strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    tracks_payload: list[dict] = payload.get("tracks", [])
    confirmed = [
        t for t in tracks_payload
        if t.get("metadata_id") and isinstance(t.get("spotify_index"), int)
    ]
    if not confirmed:
        raise HTTPException(status_code=400, detail="No matched tracks to import")

    confirmed.sort(key=lambda t: t["spotify_index"])

    db = request.app.state.db

    valid_ids = {
        row["metadata_id"]
        for row in db.execute(
            f"SELECT metadata_id FROM tracks WHERE metadata_id IN ({','.join('?' * len(confirmed))})",
            [t["metadata_id"] for t in confirmed],
        ).fetchall()
    }

    playlist_id = generate_metadata_id()
    track_count = 0

    db.execute(
        """
        INSERT INTO playlists (playlist_id, name, strategy, strategy_params, track_count)
        VALUES (?, ?, 'spotify_import', NULL, 0)
        """,
        (playlist_id, name),
    )

    for position, track in enumerate(confirmed, start=1):
        mid = track["metadata_id"]
        if mid not in valid_ids:
            continue
        db.execute(
            "INSERT OR IGNORE INTO playlist_tracks (playlist_id, metadata_id, position) VALUES (?, ?, ?)",
            (playlist_id, mid, position),
        )
        track_count += 1

    db.execute(
        "UPDATE playlists SET track_count = ?, updated_at = datetime('now') WHERE playlist_id = ?",
        (track_count, playlist_id),
    )
    db.commit()

    return {"playlist_id": playlist_id}
