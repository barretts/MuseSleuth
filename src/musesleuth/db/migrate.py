"""Schema migration helpers — add columns missing from older databases."""
from __future__ import annotations

import sqlite3

from musesleuth.db.search import rebuild_search_index


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns that may be missing from older databases."""
    _add_column(conn, "artist_stats", "bio", "TEXT")
    _add_column(conn, "artist_stats", "url", "TEXT")
    _add_column(conn, "artist_stats", "disambiguation", "TEXT")
    _add_column(conn, "track_stats", "wiki", "TEXT")
    _add_column(conn, "track_stats", "url", "TEXT")
    _add_column(conn, "playlist_signals", "remix_group", "TEXT")
    _add_column(conn, "technical_features", "spectrogram_path", "TEXT")
    _add_column(conn, "technical_features", "lowpass_cutoff_hz", "REAL")
    _add_column(conn, "technical_features", "silence_ratio", "REAL")
    _add_column(conn, "technical_features", "clipping_ratio", "REAL")
    _add_column(conn, "technical_features", "spectrogram_generated_at", "TEXT")
    _add_column(conn, "subsonic_playlist_sync", "subsonic_playlist_name", "TEXT")
    _add_column(conn, "subsonic_playlist_sync", "last_synced_at", "TEXT")
    _add_column(conn, "subsonic_playlist_sync", "created_at", "TEXT")
    _add_column(conn, "subsonic_playlist_sync", "updated_at", "TEXT")
    _add_column(conn, "subsonic_song_cache", "title", "TEXT")
    _add_column(conn, "subsonic_song_cache", "artist", "TEXT")
    _add_column(conn, "subsonic_song_cache", "album", "TEXT")
    _add_column(conn, "subsonic_song_cache", "title_key", "TEXT")
    _add_column(conn, "subsonic_song_cache", "artist_key", "TEXT")
    _add_column(conn, "subsonic_song_cache", "media_folder_name", "TEXT")
    _add_column(conn, "subsonic_song_cache", "library_root", "TEXT")
    _add_column(conn, "subsonic_song_cache", "cached_at", "TEXT")
    # tracks: rich tag columns
    _add_column(conn, "tracks", "genre", "TEXT")
    _add_column(conn, "tracks", "album_artist", "TEXT")
    _add_column(conn, "tracks", "disc_number", "TEXT")
    _add_column(conn, "tracks", "total_tracks", "TEXT")
    _add_column(conn, "tracks", "original_year", "TEXT")
    _add_column(conn, "tracks", "label", "TEXT")
    # Phase 0: DJ pipeline columns
    _add_column(conn, "playlist_signals", "quality_verdict", "TEXT")
    token_count = conn.execute("SELECT COUNT(*) FROM track_search_tokens").fetchone()[0]
    if token_count == 0:
        rebuild_search_index(conn)
    conn.commit()


def _add_column(conn: sqlite3.Connection, table: str, column: str, col_type: str) -> None:
    """Add a column if it doesn't already exist. SQLite-safe."""
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
        conn.commit()
