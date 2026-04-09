"""SQL DDL statements for MuseSleuth, split into logical groups."""

# ── Core track tables ────────────────────────────────────────────────
_TRACKS_SQL = """
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
    genre           TEXT,
    album_artist    TEXT,
    disc_number     TEXT,
    total_tracks    TEXT,
    original_year   TEXT,
    label           TEXT,
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
"""

# ── Analysis feature tables ──────────────────────────────────────────
_FEATURES_SQL = """
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

CREATE TABLE IF NOT EXISTS loudness_features (
    metadata_id         TEXT PRIMARY KEY REFERENCES tracks(metadata_id),
    lufs_integrated     REAL,
    lufs_short_intro    REAL,
    lufs_short_outro    REAL,
    lra                 REAL,
    true_peak_dbtp      REAL,
    crest_factor        REAL,
    analyzed_at         TEXT
);

CREATE TABLE IF NOT EXISTS timbre_features (
    metadata_id         TEXT PRIMARY KEY REFERENCES tracks(metadata_id),
    mfcc_mean           BLOB,
    mfcc_var            BLOB,
    centroid_mean       REAL,
    rolloff_mean        REAL,
    bandwidth_mean      REAL,
    flatness_mean       REAL,
    zcr_mean            REAL,
    analyzed_at         TEXT
);
"""

# ── Metadata enrichment tables ───────────────────────────────────────
_ENRICHMENT_SQL = """
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
"""

# ── Playlist / DJ tables ─────────────────────────────────────────────
_PLAYLIST_SQL = """
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
    quality_verdict     TEXT,
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
"""

# ── Subsonic sync tables ─────────────────────────────────────────────
_SUBSONIC_SQL = """
CREATE TABLE IF NOT EXISTS subsonic_playlist_sync (
    playlist_id           TEXT PRIMARY KEY REFERENCES playlists(playlist_id) ON DELETE CASCADE,
    subsonic_playlist_id  TEXT NOT NULL,
    subsonic_playlist_name TEXT,
    last_synced_at        TEXT,
    created_at            TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at            TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_subsonic_playlist_sync_remote_id
ON subsonic_playlist_sync(subsonic_playlist_id);

CREATE TABLE IF NOT EXISTS subsonic_song_cache (
    subsonic_song_id      TEXT PRIMARY KEY,
    path                  TEXT NOT NULL,
    title                 TEXT,
    artist                TEXT,
    album                 TEXT,
    title_key             TEXT,
    artist_key            TEXT,
    media_folder_name     TEXT,
    library_root          TEXT,
    cached_at             TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_subsonic_song_cache_path
ON subsonic_song_cache(path);

CREATE INDEX IF NOT EXISTS idx_subsonic_song_cache_title_key
ON subsonic_song_cache(title_key);
"""

# ── Infrastructure tables ────────────────────────────────────────────
_INFRA_SQL = """
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

CREATE TABLE IF NOT EXISTS track_lyrics (
    metadata_id   TEXT PRIMARY KEY REFERENCES tracks(metadata_id),
    lyrics_path   TEXT NOT NULL,
    lyrics_type   TEXT NOT NULL DEFAULT 'lrc',
    file_size     INTEGER,
    discovered_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tag_writeback_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    metadata_id     TEXT NOT NULL REFERENCES tracks(metadata_id),
    field_name      TEXT NOT NULL,
    old_value       TEXT,
    new_value       TEXT,
    written_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# ── Embedding / similarity tables ────────────────────────────────────
_SIMILARITY_SQL = """
CREATE TABLE IF NOT EXISTS embeddings (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    metadata_id         TEXT NOT NULL REFERENCES tracks(metadata_id),
    model               TEXT NOT NULL,
    scope               TEXT NOT NULL,
    dim                 INTEGER NOT NULL,
    vector              BLOB NOT NULL,
    hop_s               REAL,
    window_s            REAL,
    computed_at         TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(metadata_id, model, scope)
);

CREATE TABLE IF NOT EXISTS structure_segments (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    metadata_id         TEXT NOT NULL REFERENCES tracks(metadata_id),
    segment_idx         INTEGER NOT NULL,
    start_s             REAL NOT NULL,
    end_s               REAL NOT NULL,
    kind                TEXT,
    confidence          REAL,
    analyzed_at         TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(metadata_id, segment_idx)
);

CREATE TABLE IF NOT EXISTS version_groups (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id            TEXT NOT NULL,
    metadata_id         TEXT NOT NULL REFERENCES tracks(metadata_id),
    version_label       TEXT,
    confidence          REAL,
    method              TEXT,
    UNIQUE(group_id, metadata_id)
);

CREATE TABLE IF NOT EXISTS similarity_edges (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    src_id              TEXT NOT NULL REFERENCES tracks(metadata_id),
    dst_id              TEXT NOT NULL REFERENCES tracks(metadata_id),
    metric              TEXT NOT NULL,
    value               REAL NOT NULL,
    computed_at         TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(src_id, dst_id, metric)
);

CREATE TABLE IF NOT EXISTS beat_grids (
    metadata_id             TEXT PRIMARY KEY REFERENCES tracks(metadata_id),
    beats_json              TEXT,
    downbeats_json          TEXT,
    phrase_boundaries_json  TEXT,
    time_signature          TEXT,
    confidence              REAL,
    analyzed_at             TEXT
);
"""

# ── Indexes ──────────────────────────────────────────────────────────
_INDEXES_SQL = """
CREATE INDEX IF NOT EXISTS idx_jobs_stage_status ON jobs(stage, status);
CREATE INDEX IF NOT EXISTS idx_jobs_metadata_id ON jobs(metadata_id);
CREATE INDEX IF NOT EXISTS idx_tracks_full_path ON tracks(full_path);
CREATE INDEX IF NOT EXISTS idx_external_ids_metadata ON external_ids(metadata_id);
CREATE INDEX IF NOT EXISTS idx_genres_tags_metadata ON genres_tags(metadata_id);
CREATE INDEX IF NOT EXISTS idx_embeddings_metadata ON embeddings(metadata_id);
CREATE INDEX IF NOT EXISTS idx_structure_segments_metadata ON structure_segments(metadata_id);
CREATE INDEX IF NOT EXISTS idx_version_groups_group ON version_groups(group_id);
CREATE INDEX IF NOT EXISTS idx_similarity_edges_src ON similarity_edges(src_id);
CREATE INDEX IF NOT EXISTS idx_similarity_edges_dst ON similarity_edges(dst_id);

-- Web UI performance: sort and filter indexes
CREATE INDEX IF NOT EXISTS idx_tracks_title ON tracks(title);
CREATE INDEX IF NOT EXISTS idx_tracks_artist ON tracks(artist);
CREATE INDEX IF NOT EXISTS idx_ps_decade ON playlist_signals(decade_bucket);
CREATE INDEX IF NOT EXISTS idx_ps_bpm_bucket ON playlist_signals(bpm_bucket);
CREATE INDEX IF NOT EXISTS idx_ps_energy_tier ON playlist_signals(energy_tier);
CREATE INDEX IF NOT EXISTS idx_ps_camelot ON playlist_signals(camelot_key);
CREATE INDEX IF NOT EXISTS idx_ml_genre ON ml_features(genre_primary);
CREATE INDEX IF NOT EXISTS idx_ml_vocal ON ml_features(vocal_type);
"""

# ── Search token index ───────────────────────────────────────────────
_SEARCH_SQL = """
CREATE TABLE IF NOT EXISTS track_search_tokens (
    token       TEXT NOT NULL,
    metadata_id TEXT NOT NULL REFERENCES tracks(metadata_id) ON DELETE CASCADE,
    field       TEXT NOT NULL,
    PRIMARY KEY (token, metadata_id, field)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_search_tokens_mid ON track_search_tokens(metadata_id);
"""

# ── Combined schema (executed by create_schema) ──────────────────────
SCHEMA_PARTS = [
    _TRACKS_SQL,
    _FEATURES_SQL,
    _ENRICHMENT_SQL,
    _PLAYLIST_SQL,
    _SUBSONIC_SQL,
    _INFRA_SQL,
    _SIMILARITY_SQL,
    _INDEXES_SQL,
    _SEARCH_SQL,
]

SCHEMA_SQL = "\n".join(SCHEMA_PARTS)

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
    "subsonic_playlist_sync",
    "subsonic_song_cache",
    "scraper_cache",
    "jobs",
    "track_lyrics",
    "tag_writeback_log",
    "loudness_features",
    "timbre_features",
    "embeddings",
    "structure_segments",
    "version_groups",
    "similarity_edges",
    "beat_grids",
    "track_search_tokens",
]
