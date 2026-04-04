"""Track listing, search, and filter API routes."""
from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()

PAGE_SIZE = 50

_SORT_COLS = {
    "title", "artist", "album", "year", "bpm_final",
    "camelot_key", "energy_tier", "decade_bucket",
    "genre", "vocal_type",
}


@router.get("/tracks")
async def list_tracks(
    request: Request,
    q: str = "",
    decade: str = "",
    bpm_bucket: str = "",
    energy_tier: str = "",
    camelot_key: str = "",
    genre: str = "",
    vocal_type: str = "",
    sort: str = "title",
    order: str = "asc",
    page: int = 1,
):
    """Return paginated tracks with optional filters."""
    db = request.app.state.db

    where_clauses: list[str] = []
    params: list[str] = []

    if q:
        where_clauses.append(
            "(t.title LIKE ? OR t.artist LIKE ? OR t.album LIKE ?)"
        )
        like = f"%{q}%"
        params.extend([like, like, like])

    if decade:
        where_clauses.append("ps.decade_bucket = ?")
        params.append(decade)

    if bpm_bucket:
        where_clauses.append("ps.bpm_bucket = ?")
        params.append(bpm_bucket)

    if energy_tier:
        where_clauses.append("ps.energy_tier = ?")
        params.append(energy_tier)

    if camelot_key:
        where_clauses.append("ps.camelot_key = ?")
        params.append(camelot_key)

    if genre:
        where_clauses.append("ml.genre_primary = ?")
        params.append(genre)

    if vocal_type:
        where_clauses.append("ml.vocal_type = ?")
        params.append(vocal_type)

    where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

    sort_col = sort if sort in _SORT_COLS else "title"
    if sort_col in ("title", "artist", "album", "year"):
        sort_expr = f"t.{sort_col}"
    elif sort_col == "bpm_final":
        sort_expr = "mf.bpm_final"
    elif sort_col == "genre":
        sort_expr = "ml.genre_primary"
    elif sort_col == "vocal_type":
        sort_expr = "ml.vocal_type"
    else:
        sort_expr = f"ps.{sort_col}"
    sort_dir = "DESC" if order.lower() == "desc" else "ASC"

    offset = (max(page, 1) - 1) * PAGE_SIZE

    count_sql = f"""
        SELECT COUNT(*) as cnt
        FROM tracks t
        LEFT JOIN playlist_signals ps ON ps.metadata_id = t.metadata_id
        LEFT JOIN ml_features ml ON ml.metadata_id = t.metadata_id
        WHERE {where_sql}
    """
    total = db.execute(count_sql, params).fetchone()["cnt"]
    total_pages = max((total + PAGE_SIZE - 1) // PAGE_SIZE, 1)

    query_sql = f"""
        SELECT t.metadata_id, t.title, t.artist, t.album, t.year, t.filename,
               mf.bpm_final, mf.key_name, mf.key_mode, mf.energy,
               ps.camelot_key, ps.energy_tier, ps.decade_bucket, ps.bpm_bucket,
               ml.genre_primary, ml.vocal_type, ml.mood_tags
        FROM tracks t
        LEFT JOIN musical_features mf ON mf.metadata_id = t.metadata_id
        LEFT JOIN playlist_signals ps ON ps.metadata_id = t.metadata_id
        LEFT JOIN ml_features ml ON ml.metadata_id = t.metadata_id
        WHERE {where_sql}
        ORDER BY CASE WHEN {sort_expr} IS NULL OR {sort_expr} = '' THEN 1 ELSE 0 END,
                 {sort_expr} {sort_dir}
        LIMIT ? OFFSET ?
    """
    rows = db.execute(query_sql, [*params, PAGE_SIZE, offset]).fetchall()
    tracks = [dict(r) for r in rows]

    decades = _distinct(db, "playlist_signals", "decade_bucket")
    bpm_buckets = _distinct(db, "playlist_signals", "bpm_bucket")
    energy_tiers = _distinct(db, "playlist_signals", "energy_tier")
    camelot_keys = _distinct(db, "playlist_signals", "camelot_key")
    genres = _distinct(db, "ml_features", "genre_primary")
    vocal_types = _distinct(db, "ml_features", "vocal_type")

    return {
        "tracks": tracks,
        "q": q,
        "decade": decade,
        "bpm_bucket": bpm_bucket,
        "energy_tier": energy_tier,
        "camelot_key": camelot_key,
        "genre": genre,
        "vocal_type": vocal_type,
        "sort": sort_col,
        "order": order,
        "page": page,
        "total_pages": total_pages,
        "total": total,
        "decades": decades,
        "bpm_buckets": bpm_buckets,
        "energy_tiers": energy_tiers,
        "camelot_keys": camelot_keys,
        "genres": genres,
        "vocal_types": vocal_types,
    }


def _distinct(db, table: str, col: str) -> list[str]:
    """Return sorted non-null distinct values for a column."""
    rows = db.execute(
        f"SELECT DISTINCT {col} FROM {table} WHERE {col} IS NOT NULL ORDER BY {col}"
    ).fetchall()
    return [r[0] for r in rows]
