"""Track search token index — incrementally updatable word index."""
from __future__ import annotations

from collections import defaultdict
import re
import sqlite3

_SPLIT_RE = re.compile(r"[^a-z0-9]+")
_MIN_TOKEN_LEN = 2


def tokenize(text: str) -> set[str]:
    """Split text into lowercase word tokens, dropping short noise."""
    if not text:
        return set()
    return {t for t in _SPLIT_RE.split(text.lower()) if len(t) >= _MIN_TOKEN_LEN}


def index_track(conn: sqlite3.Connection, metadata_id: str) -> None:
    """Reindex search tokens for a single track (incremental update)."""
    conn.execute(
        "DELETE FROM track_search_tokens WHERE metadata_id = ?", (metadata_id,)
    )
    row = conn.execute(
        "SELECT title, artist, album FROM tracks WHERE metadata_id = ?",
        (metadata_id,),
    ).fetchone()
    if not row:
        return
    rows: list[tuple[str, str, str]] = []
    for field in ("title", "artist", "album"):
        for token in tokenize(row[field] or ""):
            rows.append((token, metadata_id, field))
    if rows:
        conn.executemany(
            "INSERT OR IGNORE INTO track_search_tokens (token, metadata_id, field) VALUES (?, ?, ?)",
            rows,
        )


def rebuild_search_index(conn: sqlite3.Connection) -> None:
    """Full rebuild of the search token index from all tracks."""
    conn.execute("DELETE FROM track_search_tokens")
    cursor = conn.execute("SELECT metadata_id, title, artist, album FROM tracks")
    batch: list[tuple[str, str, str]] = []
    for mid, title, artist, album in cursor:
        for field, val in [("title", title), ("artist", artist), ("album", album)]:
            for token in tokenize(val or ""):
                batch.append((token, mid, field))
        if len(batch) >= 50_000:
            conn.executemany(
                "INSERT OR IGNORE INTO track_search_tokens (token, metadata_id, field) VALUES (?, ?, ?)",
                batch,
            )
            batch.clear()
    if batch:
        conn.executemany(
            "INSERT OR IGNORE INTO track_search_tokens (token, metadata_id, field) VALUES (?, ?, ?)",
            batch,
        )
    conn.commit()


def _prefix_upper_bound(token: str) -> str:
    """Return an upper bound string for prefix range queries."""
    return f"{token}\uffff"


def search_tracks(conn: sqlite3.Connection, query: str, limit: int = 500) -> list[str]:
    """Return ranked metadata_ids matching all query tokens with prefix support."""
    tokens = tokenize(query)
    if not tokens:
        return []

    token_list = sorted(tokens)
    token_hits: dict[str, dict[str, int]] = defaultdict(dict)
    scores: dict[str, int] = defaultdict(int)

    field_weights = {
        "title": 30,
        "artist": 20,
        "album": 10,
    }

    for tok in token_list:
        rows = conn.execute(
            """
            SELECT metadata_id, token, field
            FROM track_search_tokens
            WHERE token >= ? AND token < ?
            """,
            (tok, _prefix_upper_bound(tok)),
        ).fetchall()
        for row in rows:
            metadata_id = row[0]
            matched_token = row[1]
            field = row[2]
            token_hits[metadata_id][tok] = 1
            base = field_weights.get(field, 5)
            exact_bonus = 15 if matched_token == tok else 0
            prefix_bonus = max(0, 8 - max(0, len(matched_token) - len(tok)))
            scores[metadata_id] += base + exact_bonus + prefix_bonus

    matched = [
        metadata_id
        for metadata_id, seen_tokens in token_hits.items()
        if len(seen_tokens) == len(token_list)
    ]
    if not matched and len(token_list) > 1:
        min_tokens = max(1, len(token_list) - 1)
        matched = [
            metadata_id
            for metadata_id, seen_tokens in token_hits.items()
            if len(seen_tokens) >= min_tokens
        ]
    matched.sort(key=lambda metadata_id: (-scores[metadata_id], metadata_id))
    return matched[:limit]
