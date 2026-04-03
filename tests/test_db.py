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
