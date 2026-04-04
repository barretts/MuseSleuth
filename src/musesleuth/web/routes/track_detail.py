"""Single track detail API view."""
from __future__ import annotations

import json

from fastapi import APIRouter, Request, HTTPException

router = APIRouter()


def _safe_json(val: str | None) -> list | dict:
    if not val:
        return []
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return []


@router.get("/tracks/{metadata_id}")
async def track_detail(request: Request, metadata_id: str):
    """Return the full detail payload for one track."""
    db = request.app.state.db

    track = db.execute(
        "SELECT * FROM tracks WHERE metadata_id = ?", (metadata_id,)
    ).fetchone()
    if track is None:
        raise HTTPException(status_code=404, detail="Track not found")

    tech = db.execute(
        "SELECT * FROM technical_features WHERE metadata_id = ?", (metadata_id,)
    ).fetchone()

    musical = db.execute(
        "SELECT * FROM musical_features WHERE metadata_id = ?", (metadata_id,)
    ).fetchone()

    signals = db.execute(
        "SELECT * FROM playlist_signals WHERE metadata_id = ?", (metadata_id,)
    ).fetchone()

    ml = db.execute(
        "SELECT * FROM ml_features WHERE metadata_id = ?", (metadata_id,)
    ).fetchone()

    genres = db.execute(
        "SELECT tag_type, tag_value, source, confidence FROM genres_tags WHERE metadata_id = ?",
        (metadata_id,),
    ).fetchall()

    ext_ids = db.execute(
        "SELECT source, external_id, confidence FROM external_ids WHERE metadata_id = ?",
        (metadata_id,),
    ).fetchall()

    # Fetch all artist_stats rows (lastfm + musicbrainz)
    artist_stats_rows = db.execute(
        "SELECT * FROM artist_stats WHERE metadata_id = ?", (metadata_id,)
    ).fetchall()

    # Fetch all track_stats rows
    track_stats_rows = db.execute(
        "SELECT * FROM track_stats WHERE metadata_id = ?", (metadata_id,)
    ).fetchall()

    # Build structured artist data from both sources
    artist_data: dict = {
        "bio": None,
        "disambiguation": None,
        "country": None,
        "url": None,
        "similar_artists": [],
        "genres": [],
        "listeners": None,
        "play_count": None,
    }
    for row in artist_stats_rows:
        r = dict(row)
        if r.get("source") == "lastfm":
            artist_data["bio"] = r.get("bio")
            artist_data["url"] = r.get("url")
            artist_data["listeners"] = r.get("listeners")
            artist_data["play_count"] = r.get("play_count")
            artist_data["similar_artists"] = _safe_json(r.get("similar_artists"))
            artist_data["genres"] = _safe_json(r.get("genres"))
        if r.get("source") == "musicbrainz":
            artist_data["disambiguation"] = r.get("disambiguation")
            if not artist_data["country"]:
                artist_data["country"] = r.get("country")

    # Build structured track stats
    track_data: dict = {
        "wiki": None,
        "url": None,
        "listeners": None,
        "play_count": None,
    }
    for row in track_stats_rows:
        r = dict(row)
        if r.get("source") == "lastfm":
            track_data["wiki"] = r.get("wiki")
            track_data["url"] = r.get("url")
            track_data["listeners"] = r.get("listener_count")
            track_data["play_count"] = r.get("play_count")

    # Group genres by source
    genres_by_source: dict[str, list[dict]] = {}
    for g in genres:
        gd = dict(g)
        src = gd["source"]
        genres_by_source.setdefault(src, []).append(gd)

    return {
        "track": dict(track),
        "tech": dict(tech) if tech else None,
        "musical": dict(musical) if musical else None,
        "signals": dict(signals) if signals else None,
        "ml": dict(ml) if ml else None,
        "genres": [dict(g) for g in genres],
        "genres_by_source": genres_by_source,
        "ext_ids": [dict(e) for e in ext_ids],
        "artist": artist_data,
        "track_ext": track_data,
        "artist_stats_rows": [dict(r) for r in artist_stats_rows],
        "track_stats_rows": [dict(r) for r in track_stats_rows],
    }
