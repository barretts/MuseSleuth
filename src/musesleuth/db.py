"""SQLite database schema and connection management for MuseSleuth."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from ulid import ULID


TABLE_NAMES = [
    "tracks",
    "track_sidecars",
    "technical_features",
    "musical_features",
    "ml_features",
    "tag_snapshot_raw",
    "external_ids",
    "artist_stats",
    "track_stats",
    "genres_tags",
    "playlist_signals",
    "playlists",
    "playlist_tracks",
    "scraper_cache",
    "jobs",
    "tag_writeback_log",
]

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tracks (
    metadata_id     TEXT PRIMARY KEY,
    title           TEXT,
    artist          TEXT,
    album           TEXT,
    track_number    TEXT,
    year            TEXT,
    length_seconds  TEXT,
    file_size       TEXT,
    last_modified   TEXT,
    file_path       TEXT NOT NULL,
    filename        TEXT NOT NULL,
    full_path       TEXT NOT NULL UNIQUE,
    imported_at     TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS track_sidecars (
    metadata_id     TEXT PRIMARY KEY REFERENCES tracks(metadata_id),
    sidecar_path    TEXT NOT NULL,
    hash_partial    TEXT,
    hash_full       TEXT,
    fingerprint     TEXT,
    sidecar_version INTEGER NOT NULL DEFAULT 1,
    synced_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS technical_features (
    metadata_id     TEXT PRIMARY KEY REFERENCES tracks(metadata_id),
    codec           TEXT,
    bitrate         INTEGER,
    sample_rate     INTEGER,
    channels        INTEGER,
    duration_ms     INTEGER,
    loudness_db     REAL,
    replaygain      REAL,
    raw_ffprobe     TEXT,
    raw_mediainfo   TEXT,
    integrity_ok    INTEGER,
    integrity_errors TEXT,
    was_repaired    INTEGER,
    spectrogram_path TEXT,
    lowpass_cutoff_hz REAL,
    silence_ratio   REAL,
    clipping_ratio  REAL,
    spectrogram_generated_at TEXT,
    analyzed_at     TEXT
);

CREATE TABLE IF NOT EXISTS musical_features (
    metadata_id         TEXT PRIMARY KEY REFERENCES tracks(metadata_id),
    bpm_aubio           REAL,
    bpm_librosa        REAL,
    bpm_final           REAL,
    bpm_confidence      REAL,
    bpm_disagreement    INTEGER NOT NULL DEFAULT 0,
    key_name            TEXT,
    key_mode            TEXT,
    energy              REAL,
    dynamic_range       REAL,
    onset_density       REAL,
    peak_rms            REAL,
    analyzed_at         TEXT
);

CREATE TABLE IF NOT EXISTS ml_features (
    metadata_id             TEXT PRIMARY KEY REFERENCES tracks(metadata_id),
    genre_primary           TEXT,
    genre_secondary         TEXT,
    genre_confidence        REAL,
    mood_tags               TEXT,
    danceability            REAL,
    acousticness            REAL,
    electronic_score        REAL,
    vocal_type              TEXT,
    vocal_confidence        REAL,
    era_prediction          TEXT,
    era_confidence          REAL,
    tempo_category          TEXT,
    energy_category         TEXT,
    quality_score           REAL,
    quality_issues          TEXT,
    spectral_centroid       REAL,
    spectral_bandwidth      REAL,
    spectral_rolloff        REAL,
    spectral_flatness       REAL,
    zero_crossing_rate      REAL,
    mfcc_mean               TEXT,
    mfcc_std                TEXT,
    spectral_contrast       TEXT,
    harmonic_percussive_ratio REAL,
    classified_at           TEXT
);

CREATE TABLE IF NOT EXISTS tag_snapshot_raw (
    metadata_id     TEXT PRIMARY KEY REFERENCES tracks(metadata_id),
    tags_json       TEXT NOT NULL,
    captured_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS external_ids (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    metadata_id     TEXT NOT NULL REFERENCES tracks(metadata_id),
    source          TEXT NOT NULL,
    external_id     TEXT NOT NULL,
    confidence      REAL,
    fetched_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(metadata_id, source, external_id)
);

CREATE TABLE IF NOT EXISTS artist_stats (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    metadata_id     TEXT NOT NULL REFERENCES tracks(metadata_id),
    source          TEXT NOT NULL,
    listeners       INTEGER,
    play_count      INTEGER,
    followers       INTEGER,
    genres          TEXT,
    active_years    TEXT,
    country         TEXT,
    similar_artists TEXT,
    bio             TEXT,
    url             TEXT,
    disambiguation  TEXT,
    fetched_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(metadata_id, source)
);

CREATE TABLE IF NOT EXISTS track_stats (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    metadata_id     TEXT NOT NULL REFERENCES tracks(metadata_id),
    source          TEXT NOT NULL,
    popularity      INTEGER,
    listener_count  INTEGER,
    play_count      INTEGER,
    dj_play_count   INTEGER,
    community_rating REAL,
    rank_position   INTEGER,
    wiki            TEXT,
    url             TEXT,
    fetched_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(metadata_id, source)
);

CREATE TABLE IF NOT EXISTS genres_tags (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    metadata_id     TEXT NOT NULL REFERENCES tracks(metadata_id),
    tag_type        TEXT NOT NULL,
    tag_value       TEXT NOT NULL,
    source          TEXT NOT NULL,
    confidence      REAL,
    fetched_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(metadata_id, tag_type, tag_value, source)
);

CREATE TABLE IF NOT EXISTS playlist_signals (
    metadata_id         TEXT PRIMARY KEY REFERENCES tracks(metadata_id),
    decade_bucket       TEXT,
    year_bucket         TEXT,
    bpm_bucket          TEXT,
    camelot_key         TEXT,
    energy_tier         TEXT,
    popularity_tier     TEXT,
    vocal_instrumental  TEXT,
    set_time_hint       TEXT,
    duplicate_group     TEXT,
    remix_group         TEXT,
    computed_at         TEXT
);

CREATE TABLE IF NOT EXISTS playlists (
    playlist_id     TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT,
    strategy        TEXT NOT NULL,
    strategy_params TEXT,
    track_count     INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS playlist_tracks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    playlist_id     TEXT NOT NULL REFERENCES playlists(playlist_id) ON DELETE CASCADE,
    metadata_id     TEXT NOT NULL REFERENCES tracks(metadata_id),
    position        INTEGER NOT NULL,
    added_at        TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(playlist_id, metadata_id),
    UNIQUE(playlist_id, position)
);

CREATE INDEX IF NOT EXISTS idx_playlist_tracks_playlist ON playlist_tracks(playlist_id);

CREATE TABLE IF NOT EXISTS scraper_cache (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    adapter_name    TEXT NOT NULL,
    cache_key       TEXT NOT NULL,
    payload         TEXT NOT NULL,
    content_type    TEXT NOT NULL DEFAULT 'text/html',
    fetched_at      TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at      TEXT,
    UNIQUE(adapter_name, cache_key)
);

CREATE TABLE IF NOT EXISTS jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    metadata_id     TEXT NOT NULL REFERENCES tracks(metadata_id),
    stage           TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    worker_id       TEXT,
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT,
    claimed_at      TEXT,
    completed_at    TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(metadata_id, stage)
);

CREATE TABLE IF NOT EXISTS tag_writeback_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    metadata_id     TEXT NOT NULL REFERENCES tracks(metadata_id),
    field_name      TEXT NOT NULL,
    old_value       TEXT,
    new_value       TEXT,
    written_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_jobs_stage_status ON jobs(stage, status);
CREATE INDEX IF NOT EXISTS idx_jobs_metadata_id ON jobs(metadata_id);
CREATE INDEX IF NOT EXISTS idx_tracks_full_path ON tracks(full_path);
CREATE INDEX IF NOT EXISTS idx_external_ids_metadata ON external_ids(metadata_id);
CREATE INDEX IF NOT EXISTS idx_genres_tags_metadata ON genres_tags(metadata_id);
"""


def generate_metadata_id() -> str:
    """Generate a new ULID string for use as a metadata_id."""
    return str(ULID())


def get_connection(db_path: Path) -> sqlite3.Connection:
    """Create or open a SQLite connection with WAL mode and foreign keys."""
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    """Create all tables if they don't exist. Idempotent."""
    conn.executescript(_SCHEMA_SQL)
    _migrate(conn)


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


def _add_column(conn: sqlite3.Connection, table: str, column: str, col_type: str) -> None:
    """Add a column if it doesn't already exist. SQLite-safe."""
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
        conn.commit()