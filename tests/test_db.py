"""TDD tests for database schema and ULID generation -- written before implementation."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from musesleuth.db import create_schema, get_connection, TABLE_NAMES
from musesleuth.db import generate_metadata_id


class TestGenerateMetadataId:
    """Tests for ULID-based metadata_id generation."""

    @pytest.mark.unit
    def test_returns_string(self) -> None:
        mid = generate_metadata_id()
        assert isinstance(mid, str)

    @pytest.mark.unit
    def test_length_26_chars(self) -> None:
        """ULID canonical string is 26 characters."""
        mid = generate_metadata_id()
        assert len(mid) == 26

    @pytest.mark.unit
    def test_uniqueness(self) -> None:
        ids = {generate_metadata_id() for _ in range(1000)}
        assert len(ids) == 1000

    @pytest.mark.unit
    def test_sortable_by_time(self) -> None:
        import time
        id1 = generate_metadata_id()
        time.sleep(0.002)
        id2 = generate_metadata_id()
        assert id1 < id2


class TestCreateSchema:
    """Tests for SQLite schema creation."""

    @pytest.mark.integration
    def test_creates_all_tables(self, in_memory_db: sqlite3.Connection) -> None:
        create_schema(in_memory_db)
        cursor = in_memory_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row["name"] for row in cursor.fetchall()}
        for expected in TABLE_NAMES:
            assert expected in tables, f"Missing table: {expected}"

    @pytest.mark.integration
    def test_tracks_table_has_metadata_id_column(
        self, in_memory_db: sqlite3.Connection
    ) -> None:
        create_schema(in_memory_db)
        cursor = in_memory_db.execute("PRAGMA table_info(tracks)")
        columns = {row["name"] for row in cursor.fetchall()}
        assert "metadata_id" in columns

    @pytest.mark.integration
    def test_schema_is_idempotent(self, in_memory_db: sqlite3.Connection) -> None:
        create_schema(in_memory_db)
        create_schema(in_memory_db)  # should not raise

    @pytest.mark.integration
    def test_foreign_keys_enabled(self, in_memory_db: sqlite3.Connection) -> None:
        create_schema(in_memory_db)
        fk = in_memory_db.execute("PRAGMA foreign_keys").fetchone()
        assert fk[0] == 1


class TestDjPipelineTables:
    """Tests for Phase 0 DJ pipeline tables added to the schema."""

    NEW_TABLES = [
        "loudness_features",
        "timbre_features",
        "embeddings",
        "structure_segments",
        "version_groups",
        "similarity_edges",
        "beat_grids",
    ]

    @pytest.mark.integration
    def test_new_tables_exist_after_create_schema(
        self, in_memory_db: sqlite3.Connection
    ) -> None:
        create_schema(in_memory_db)
        cursor = in_memory_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row["name"] for row in cursor.fetchall()}
        for expected in self.NEW_TABLES:
            assert expected in tables, f"Missing table: {expected}"

    @pytest.mark.integration
    def test_new_tables_in_table_names_constant(self) -> None:
        for expected in self.NEW_TABLES:
            assert expected in TABLE_NAMES, f"{expected} not in TABLE_NAMES"

    @pytest.mark.integration
    def test_loudness_features_columns(self, in_memory_db: sqlite3.Connection) -> None:
        create_schema(in_memory_db)
        cols = {row["name"] for row in in_memory_db.execute("PRAGMA table_info(loudness_features)").fetchall()}
        for expected in ("metadata_id", "lufs_integrated", "lufs_short_intro", "lufs_short_outro",
                         "lra", "true_peak_dbtp", "crest_factor", "analyzed_at"):
            assert expected in cols, f"Missing column: {expected}"

    @pytest.mark.integration
    def test_timbre_features_columns(self, in_memory_db: sqlite3.Connection) -> None:
        create_schema(in_memory_db)
        cols = {row["name"] for row in in_memory_db.execute("PRAGMA table_info(timbre_features)").fetchall()}
        for expected in ("metadata_id", "mfcc_mean", "mfcc_var", "centroid_mean",
                         "rolloff_mean", "bandwidth_mean", "flatness_mean", "zcr_mean", "analyzed_at"):
            assert expected in cols, f"Missing column: {expected}"

    @pytest.mark.integration
    def test_embeddings_columns(self, in_memory_db: sqlite3.Connection) -> None:
        create_schema(in_memory_db)
        cols = {row["name"] for row in in_memory_db.execute("PRAGMA table_info(embeddings)").fetchall()}
        for expected in ("id", "metadata_id", "model", "scope", "dim", "vector",
                         "hop_s", "window_s", "computed_at"):
            assert expected in cols, f"Missing column: {expected}"

    @pytest.mark.integration
    def test_structure_segments_columns(self, in_memory_db: sqlite3.Connection) -> None:
        create_schema(in_memory_db)
        cols = {row["name"] for row in in_memory_db.execute("PRAGMA table_info(structure_segments)").fetchall()}
        for expected in ("id", "metadata_id", "segment_idx", "start_s", "end_s",
                         "kind", "confidence", "analyzed_at"):
            assert expected in cols, f"Missing column: {expected}"

    @pytest.mark.integration
    def test_version_groups_columns(self, in_memory_db: sqlite3.Connection) -> None:
        create_schema(in_memory_db)
        cols = {row["name"] for row in in_memory_db.execute("PRAGMA table_info(version_groups)").fetchall()}
        for expected in ("id", "group_id", "metadata_id", "version_label", "confidence", "method"):
            assert expected in cols, f"Missing column: {expected}"

    @pytest.mark.integration
    def test_similarity_edges_columns(self, in_memory_db: sqlite3.Connection) -> None:
        create_schema(in_memory_db)
        cols = {row["name"] for row in in_memory_db.execute("PRAGMA table_info(similarity_edges)").fetchall()}
        for expected in ("id", "src_id", "dst_id", "metric", "value", "computed_at"):
            assert expected in cols, f"Missing column: {expected}"

    @pytest.mark.integration
    def test_beat_grids_columns(self, in_memory_db: sqlite3.Connection) -> None:
        create_schema(in_memory_db)
        cols = {row["name"] for row in in_memory_db.execute("PRAGMA table_info(beat_grids)").fetchall()}
        for expected in ("metadata_id", "beats_json", "downbeats_json",
                         "phrase_boundaries_json", "time_signature", "confidence", "analyzed_at"):
            assert expected in cols, f"Missing column: {expected}"

    @pytest.mark.integration
    def test_playlist_signals_quality_verdict_column(
        self, in_memory_db: sqlite3.Connection
    ) -> None:
        create_schema(in_memory_db)
        cols = {row["name"] for row in in_memory_db.execute("PRAGMA table_info(playlist_signals)").fetchall()}
        assert "quality_verdict" in cols, "Missing column: quality_verdict"

    @pytest.mark.integration
    def test_schema_idempotent_with_new_tables(
        self, in_memory_db: sqlite3.Connection
    ) -> None:
        create_schema(in_memory_db)
        create_schema(in_memory_db)  # should not raise


class TestGetConnection:
    """Tests for connection factory."""

    @pytest.mark.integration
    def test_creates_db_file(self, tmp_db_path: Path) -> None:
        conn = get_connection(tmp_db_path)
        assert tmp_db_path.exists()
        conn.close()

    @pytest.mark.integration
    def test_wal_mode(self, tmp_db_path: Path) -> None:
        conn = get_connection(tmp_db_path)
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"
        conn.close()
