from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

from musesleuth.db import create_schema, get_connection


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "upgrade_music_db.py"
SPEC = importlib.util.spec_from_file_location("upgrade_music_db", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
upgrade_music_db = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = upgrade_music_db
SPEC.loader.exec_module(upgrade_music_db)


def _create_legacy_db(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE tracks (
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
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE technical_features (
            metadata_id      TEXT PRIMARY KEY REFERENCES tracks(metadata_id),
            codec            TEXT,
            bitrate          INTEGER,
            sample_rate      INTEGER,
            channels         INTEGER,
            duration_ms      INTEGER,
            loudness_db      REAL,
            replaygain       REAL,
            raw_ffprobe      TEXT,
            raw_mediainfo    TEXT,
            integrity_ok     INTEGER,
            integrity_errors TEXT,
            was_repaired     INTEGER,
            analyzed_at      TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE musical_features (
            metadata_id      TEXT PRIMARY KEY REFERENCES tracks(metadata_id),
            bpm_aubio        REAL,
            bpm_librosa      REAL,
            bpm_final        REAL,
            bpm_confidence   REAL,
            bpm_disagreement INTEGER NOT NULL DEFAULT 0,
            key_name         TEXT,
            key_mode         TEXT,
            energy           REAL,
            dynamic_range    REAL,
            onset_density    REAL,
            peak_rms         REAL,
            analyzed_at      TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE artist_stats (
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
            fetched_at      TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(metadata_id, source)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE track_stats (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            metadata_id      TEXT NOT NULL REFERENCES tracks(metadata_id),
            source           TEXT NOT NULL,
            popularity       INTEGER,
            listener_count   INTEGER,
            play_count       INTEGER,
            dj_play_count    INTEGER,
            community_rating REAL,
            rank_position    INTEGER,
            fetched_at       TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(metadata_id, source)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE playlist_signals (
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
            computed_at         TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO tracks (
            metadata_id, title, artist, album, file_path, filename, full_path
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "01HXTEST000000000000000001",
            "Legacy Song",
            "Legacy Artist",
            "Legacy Album",
            "C:\\legacy\\",
            "legacy.mp3",
            "C:\\legacy\\legacy.mp3",
        ),
    )
    conn.commit()
    conn.close()


def _all_table_names(db_path: Path) -> set[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        return {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
    finally:
        conn.close()


class TestUpgradeMusicDb:
    @pytest.mark.integration
    def test_upgrade_database_adds_missing_schema_and_backup(self, tmp_dir: Path) -> None:
        db_path = tmp_dir / "music.db"
        _create_legacy_db(db_path)

        report = upgrade_music_db.upgrade_database(db_path)

        assert report.before.missing_tables
        assert "track_search_tokens" in report.before.missing_tables
        assert report.before.missing_columns["tracks"] == (
            "album_artist",
            "disc_number",
            "genre",
            "label",
            "original_year",
            "total_tracks",
        )
        assert report.after.missing_tables == ()
        assert report.after.missing_indexes == ()
        assert report.after.missing_columns == {}
        assert report.changed is True
        assert report.backup_path is not None
        assert report.backup_path.exists()

        conn = get_connection(db_path)
        track_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(tracks)").fetchall()
        }
        assert "genre" in track_columns
        assert "album_artist" in track_columns
        token_count = conn.execute("SELECT COUNT(*) FROM track_search_tokens").fetchone()[0]
        conn.close()
        assert token_count > 0

    @pytest.mark.integration
    def test_upgrade_database_dry_run_does_not_modify_schema(self, tmp_dir: Path) -> None:
        db_path = tmp_dir / "music.db"
        _create_legacy_db(db_path)

        before_tables = _all_table_names(db_path)
        report = upgrade_music_db.upgrade_database(db_path, dry_run=True)
        after_tables = _all_table_names(db_path)

        assert report.dry_run is True
        assert report.backup_path is None
        assert report.changed is False
        assert report.before.missing_tables
        assert report.after == report.before
        assert before_tables == after_tables
        assert "track_search_tokens" not in after_tables

    @pytest.mark.integration
    def test_upgrade_database_is_idempotent_on_current_schema(self, tmp_dir: Path) -> None:
        db_path = tmp_dir / "music.db"
        conn = get_connection(db_path)
        create_schema(conn)
        conn.close()

        report = upgrade_music_db.upgrade_database(db_path, make_backup=False)

        assert report.before.missing_tables == ()
        assert report.before.missing_indexes == ()
        assert report.before.missing_columns == {}
        assert report.after.missing_tables == ()
        assert report.after.missing_indexes == ()
        assert report.after.missing_columns == {}
        assert report.backup_path is None
        assert report.changed is False
