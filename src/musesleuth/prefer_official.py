"""Prefer official album tracks over covers / tribute acts in playlists.

Applied to strategy candidate lists *before* dedup so that when multiple
versions of a song are clustered into the same ``remix_group`` /
``duplicate_group``, the official release wins the one-per-group slot.

Two signals are used:

1. A **hard artist blocklist** of well-known tribute / cover-only acts.
   Tracks by these artists are dropped unconditionally. Low false-positive
   risk - these artists exist solely to produce covers.

2. A **soft regex** over artist + title. Matches (e.g. ``"8-Bit"``,
   ``"karaoke"``, ``"in the style of"``) are deprioritized within their
   remix / duplicate group and dropped entirely when the track's Last.fm
   ``listener_count`` is below ``popularity_floor``.

Call sites pass ``include_covers=True`` (or set the param on a strategy) to
bypass both filters for playlists that deliberately target covers.
"""
from __future__ import annotations

import re
import sqlite3
from typing import Iterable


# Lowercase exact-match artist names. Edit this list when a new tribute
# label shows up in your library.
HARD_ARTIST_BLOCKLIST: frozenset[str] = frozenset(
    {
        "8-bit arcade",
        "8 bit arcade",
        "8 bit universe",
        "8-bit universe",
        "vitamin string quartet",
        "rockabye baby!",
        "rockabye baby",
        "the karaoke channel",
        "karaoke version",
        "karaoke - ameritz",
        "ameritz karaoke",
        "the hit crew",
        "kidz bop kids",
        "kidz bop",
        "made famous by",
        "cover band",
        "twinkle twinkle little rock star",
        "the string quartet tribute",
        "rock n roll baby",
    }
)


# Soft cover/instrumental signals. Case-insensitive whole-word matches
# against artist and title strings.
SOFT_PATTERNS: re.Pattern[str] = re.compile(
    r"\b("
    r"8[- ]?bit|"
    r"karaoke|"
    r"instrumental version|"
    r"in the style of|"
    r"lullaby(?: rendition| version)?|"
    r"tribute to|"
    r"made famous by|"
    r"as made famous by"
    r")\b",
    re.IGNORECASE,
)


DEFAULT_POPULARITY_FLOOR = 1000


def is_hard_blocked(artist: str | None) -> bool:
    """True when *artist* matches the hard tribute-label blocklist."""
    if not artist:
        return False
    return artist.strip().lower() in HARD_ARTIST_BLOCKLIST


def has_soft_cover_signal(artist: str | None, title: str | None) -> bool:
    """True when artist or title matches the soft cover regex."""
    haystack = f"{artist or ''} {title or ''}"
    return bool(SOFT_PATTERNS.search(haystack))


def _load_quality_signals(
    conn: sqlite3.Connection, metadata_ids: Iterable[str]
) -> dict[str, tuple[str | None, str | None, int]]:
    """Batch-lookup ``(artist, title, listener_count)`` for each id.

    ``listener_count`` defaults to 0 when Last.fm data is missing.
    """
    ids = [mid for mid in metadata_ids if mid]
    if not ids:
        return {}
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"""
        SELECT
            t.metadata_id,
            t.artist,
            t.title,
            COALESCE(ts.listener_count, 0) AS listener_count
        FROM tracks t
        LEFT JOIN track_stats ts
            ON ts.metadata_id = t.metadata_id AND ts.source = 'lastfm'
        WHERE t.metadata_id IN ({placeholders})
        """,
        ids,
    ).fetchall()
    return {
        r["metadata_id"]: (r["artist"], r["title"], int(r["listener_count"] or 0))
        for r in rows
    }


def filter_and_prefer_official(
    conn: sqlite3.Connection,
    rows: list[sqlite3.Row],
    *,
    include_covers: bool = False,
    popularity_floor: int = DEFAULT_POPULARITY_FLOOR,
) -> list[sqlite3.Row]:
    """Drop hard-blocked tracks, drop soft-matched low-popularity tracks,
    and stably reorder rows so non-cover / more-popular versions come
    first within each ``remix_group`` / ``duplicate_group``.

    When *include_covers* is True the rows are returned unchanged.
    """
    if include_covers or not rows:
        return list(rows)

    signals = _load_quality_signals(conn, (r["metadata_id"] for r in rows))

    filtered: list[tuple[sqlite3.Row, tuple[int, int]]] = []
    for row in rows:
        artist, title, listeners = signals.get(row["metadata_id"], (None, None, 0))
        if is_hard_blocked(artist):
            continue
        soft = has_soft_cover_signal(artist, title)
        if soft and listeners < popularity_floor:
            continue
        # Sort key: (cover_penalty asc, -listener_count asc).
        # Lower is "more official".
        sort_key = (1 if soft else 0, -listeners)
        filtered.append((row, sort_key))

    # Stable sort preserves the strategy's outer ordering between groups;
    # the key only matters as a tiebreaker *within* the same group because
    # ``_dedup_candidates`` is "first wins".
    filtered.sort(key=lambda pair: pair[1])
    return [row for row, _ in filtered]
