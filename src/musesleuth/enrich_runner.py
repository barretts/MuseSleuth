"""Enrich stage runner -- orchestrates adapter calls and stores enrichment data."""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from musesleuth.adapters.base import AdapterResult
from musesleuth.db.search import index_track

log = logging.getLogger(__name__)

@dataclass
class EnrichResult:
    """Result of the enrich stage for a single track."""

    success: bool = True
    metadata_id: str = ""
    adapters_succeeded: int = 0
    adapters_failed: int = 0
    error: Optional[str] = None


def run_enrich_for_track(
    conn: sqlite3.Connection,
    metadata_id: str,
) -> EnrichResult:
    """Run the enrich stage for a single track.

    1. Fetch track info from each adapter
    2. Fetch artist info from each adapter
    3. Store track stats, artist stats, genres/tags, external IDs
    """
    log.debug("enrich: mid=%s", metadata_id)
    result = EnrichResult(metadata_id=metadata_id)

    # Get track info from DB
    track = conn.execute(
        "SELECT title, artist FROM tracks WHERE metadata_id = ?",
        (metadata_id,),
    ).fetchone()

    if not track:
        log.warning("enrich: track not found mid=%s", metadata_id)
        result.success = False
        result.error = f"Track not found: {metadata_id}"
        return result

    title = track["title"] or ""
    artist = track["artist"] or ""

    # Check if embedded MusicBrainz IDs already exist from scan-time tag extraction
    has_mb_recording = conn.execute(
        "SELECT 1 FROM external_ids WHERE metadata_id = ? AND source = 'musicbrainz' LIMIT 1",
        (metadata_id,),
    ).fetchone() is not None
    has_mb_artist = conn.execute(
        "SELECT 1 FROM external_ids WHERE metadata_id = ? AND source = 'musicbrainz_artist' LIMIT 1",
        (metadata_id,),
    ).fetchone() is not None

    # Fetch from all adapters (skip MusicBrainz if embedded IDs already present)
    lfm_track = _fetch_lastfm_track(artist, title, conn)
    lfm_artist = _fetch_lastfm_artist(artist, conn)
    if has_mb_recording:
        mb_track = AdapterResult(source="musicbrainz", success=False, data={}, error="skipped: embedded recording ID exists")
    else:
        mb_track = _fetch_mb_track(artist, title, conn)
    if has_mb_artist:
        mb_artist = AdapterResult(source="musicbrainz", success=False, data={}, error="skipped: embedded artist ID exists")
    else:
        mb_artist = _fetch_mb_artist(artist, conn)

    adapter_results = [lfm_track, lfm_artist, mb_track, mb_artist]

    for ar in adapter_results:
        if ar.success:
            result.adapters_succeeded += 1
        else:
            result.adapters_failed += 1

    # ------------------------------------------------------------------
    # Last.fm track: stats, wiki, tags with confidence, duration, album
    # ------------------------------------------------------------------
    if lfm_track.success:
        conn.execute(
            """
            INSERT OR REPLACE INTO track_stats
                (metadata_id, source, listener_count, play_count, wiki, url)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                metadata_id,
                "lastfm",
                lfm_track.data.get("listeners"),
                lfm_track.data.get("play_count"),
                lfm_track.data.get("wiki"),
                lfm_track.data.get("url"),
            ),
        )

        for tag_info in lfm_track.data.get("tags", []):
            tag_name = tag_info["name"] if isinstance(tag_info, dict) else tag_info
            tag_count = tag_info.get("count") if isinstance(tag_info, dict) else None
            confidence = float(tag_count) / 100.0 if tag_count else None
            conn.execute(
                """
                INSERT INTO genres_tags
                    (metadata_id, tag_type, tag_value, source, confidence)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(metadata_id, tag_type, tag_value, source)
                DO UPDATE SET confidence = excluded.confidence
                """,
                (metadata_id, "genre", tag_name, "lastfm", confidence),
            )

        # Cross-validate duration from Last.fm
        lfm_duration = lfm_track.data.get("duration_ms")
        if lfm_duration and lfm_duration > 0:
            conn.execute(
                """
                UPDATE tracks SET length_seconds = ?, updated_at = datetime('now')
                WHERE metadata_id = ? AND (length_seconds IS NULL OR length_seconds = '')
                """,
                (str(lfm_duration // 1000), metadata_id),
            )

        lfm_album = lfm_track.data.get("album")
        if lfm_album:
            conn.execute(
                """
                UPDATE tracks SET album = ?, updated_at = datetime('now')
                WHERE metadata_id = ? AND (album IS NULL OR album = '')
                """,
                (lfm_album, metadata_id),
            )
            if conn.total_changes:
                index_track(conn, metadata_id)

    # ------------------------------------------------------------------
    # Last.fm artist: stats, bio, similar artists, genres
    # ------------------------------------------------------------------
    if lfm_artist.success:
        similar = json.dumps(lfm_artist.data.get("similar_artists", []))
        genres_list = lfm_artist.data.get("genres", [])
        genres = json.dumps(genres_list)
        conn.execute(
            """
            INSERT OR REPLACE INTO artist_stats
                (metadata_id, source, listeners, play_count, similar_artists,
                 genres, bio, url)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                metadata_id,
                "lastfm",
                lfm_artist.data.get("listeners"),
                lfm_artist.data.get("play_count"),
                similar,
                genres,
                lfm_artist.data.get("bio"),
                lfm_artist.data.get("url"),
            ),
        )

        for tag in genres_list:
            conn.execute(
                """
                INSERT OR IGNORE INTO genres_tags
                    (metadata_id, tag_type, tag_value, source)
                VALUES (?, ?, ?, ?)
                """,
                (metadata_id, "genre", tag, "lastfm_artist"),
            )

    # ------------------------------------------------------------------
    # MusicBrainz recording: IDs, ISRCs, tags, release info, year, album
    # ------------------------------------------------------------------
    if mb_track.success:
        recording_id = mb_track.data.get("recording_id")
        if recording_id:
            conn.execute(
                """
                INSERT OR IGNORE INTO external_ids
                    (metadata_id, source, external_id, confidence)
                VALUES (?, ?, ?, ?)
                """,
                (metadata_id, "musicbrainz", recording_id, 1.0),
            )

        mb_artist_id = mb_track.data.get("artist_id")
        if mb_artist_id:
            conn.execute(
                """
                INSERT OR IGNORE INTO external_ids
                    (metadata_id, source, external_id, confidence)
                VALUES (?, ?, ?, ?)
                """,
                (metadata_id, "musicbrainz_artist", mb_artist_id, 1.0),
            )

        for isrc in mb_track.data.get("isrcs", []):
            conn.execute(
                """
                INSERT OR IGNORE INTO external_ids
                    (metadata_id, source, external_id, confidence)
                VALUES (?, ?, ?, ?)
                """,
                (metadata_id, "isrc", isrc, 1.0),
            )

        for tag in mb_track.data.get("tags", []):
            conn.execute(
                """
                INSERT OR IGNORE INTO genres_tags
                    (metadata_id, tag_type, tag_value, source)
                VALUES (?, ?, ?, ?)
                """,
                (metadata_id, "genre", tag, "musicbrainz"),
            )

        release_date = mb_track.data.get("first_release_date") or mb_track.data.get("release_date")
        if release_date:
            year = str(release_date)[:4]
            if year.isdigit():
                conn.execute(
                    """
                    UPDATE tracks SET year = ?, updated_at = datetime('now')
                    WHERE metadata_id = ? AND (year IS NULL OR year = '')
                    """,
                    (year, metadata_id),
                )

        # Backfill album from MusicBrainz release title
        mb_album = mb_track.data.get("release_title")
        if mb_album:
            conn.execute(
                """
                UPDATE tracks SET album = ?, updated_at = datetime('now')
                WHERE metadata_id = ? AND (album IS NULL OR album = '')
                """,
                (mb_album, metadata_id),
            )
            if conn.total_changes:
                index_track(conn, metadata_id)

        # Store recording-level artist info + release metadata
        conn.execute(
            """
            INSERT OR REPLACE INTO artist_stats
                (metadata_id, source, country, disambiguation)
            VALUES (?, ?, ?, ?)
            """,
            (
                metadata_id,
                "musicbrainz",
                mb_track.data.get("artist_country"),
                mb_track.data.get("artist_disambiguation"),
            ),
        )

    # ------------------------------------------------------------------
    # MusicBrainz artist: active_years, type, country, artist-level tags
    # ------------------------------------------------------------------
    if mb_artist.success:
        mb_a_id = mb_artist.data.get("artist_id")
        if mb_a_id:
            conn.execute(
                """
                INSERT OR IGNORE INTO external_ids
                    (metadata_id, source, external_id, confidence)
                VALUES (?, ?, ?, ?)
                """,
                (metadata_id, "musicbrainz_artist", mb_a_id, 1.0),
            )

        active_years = mb_artist.data.get("active_years")
        artist_type = mb_artist.data.get("type")
        mb_a_country = mb_artist.data.get("country")

        if active_years or artist_type:
            conn.execute(
                """
                UPDATE artist_stats
                SET active_years = COALESCE(?, active_years),
                    country = COALESCE(?, country)
                WHERE metadata_id = ? AND source = 'musicbrainz'
                """,
                (active_years, mb_a_country, metadata_id),
            )

        for tag in mb_artist.data.get("tags", []):
            conn.execute(
                """
                INSERT OR IGNORE INTO genres_tags
                    (metadata_id, tag_type, tag_value, source)
                VALUES (?, ?, ?, ?)
                """,
                (metadata_id, "genre", tag, "musicbrainz_artist"),
            )

    conn.commit()
    return result


def _load_env() -> None:
    """Load .env file into os.environ if LASTFM_API_KEY is not already set."""
    import os
    if os.environ.get("LASTFM_API_KEY"):
        return
    # Walk up from this file to find .env
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())


def _get_lastfm_adapter(conn: sqlite3.Connection) -> "LastFMAdapter":
    """Create a Last.fm adapter with cache and env-var API key."""
    import os
    from musesleuth.adapters.lastfm import LastFMAdapter
    from musesleuth.adapters.cache import ScraperCache
    _load_env()
    cache = ScraperCache(conn)
    api_key = os.environ.get("LASTFM_API_KEY", "")
    return LastFMAdapter(cache=cache, api_key=api_key)


def _get_mb_adapter(conn: sqlite3.Connection) -> "MusicBrainzAdapter":
    """Create a MusicBrainz adapter with cache."""
    from musesleuth.adapters.musicbrainz import MusicBrainzAdapter
    from musesleuth.adapters.cache import ScraperCache
    cache = ScraperCache(conn)
    return MusicBrainzAdapter(cache=cache)


def _fetch_lastfm_track(artist: str, title: str, conn: Optional[sqlite3.Connection] = None) -> AdapterResult:
    """Fetch track info from Last.fm. Tests mock this function."""
    if conn is None:
        raise NotImplementedError("Must be mocked in tests or called with conn")
    adapter = _get_lastfm_adapter(conn)
    return adapter.fetch_track_info(artist, title)


def _fetch_lastfm_artist(artist: str, conn: Optional[sqlite3.Connection] = None) -> AdapterResult:
    """Fetch artist info from Last.fm. Tests mock this function."""
    if conn is None:
        raise NotImplementedError("Must be mocked in tests or called with conn")
    adapter = _get_lastfm_adapter(conn)
    return adapter.fetch_artist_info(artist)


def _fetch_mb_track(artist: str, title: str, conn: Optional[sqlite3.Connection] = None) -> AdapterResult:
    """Fetch track info from MusicBrainz. Tests mock this function."""
    if conn is None:
        raise NotImplementedError("Must be mocked in tests or called with conn")
    adapter = _get_mb_adapter(conn)
    return adapter.fetch_track_info(artist, title)


def _fetch_mb_artist(artist: str, conn: Optional[sqlite3.Connection] = None) -> AdapterResult:
    """Fetch artist info from MusicBrainz. Tests mock this function."""
    if conn is None:
        raise NotImplementedError("Must be mocked in tests or called with conn")
    adapter = _get_mb_adapter(conn)
    return adapter.fetch_artist_info(artist)