"""TDD tests for duplicate detection -- written before implementation."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from musesleuth.db import create_schema, generate_metadata_id, get_connection
from musesleuth.dedup import (
    find_hash_duplicates,
    find_fuzzy_duplicates,
    find_remix_groups,
    assign_duplicate_groups,
    DuplicateGroup,
)


def _seed_track(conn: sqlite3.Connection, mid: str, title: str, artist: str,
                partial_hash: str | None = None, full_hash: str | None = None,
                duration_ms: int | None = None) -> None:
    conn.execute(
        "INSERT INTO tracks (metadata_id, title, artist, file_path, filename, full_path) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (mid, title, artist, "C:\\t\\", f"{mid}.mp3", f"C:\\t\\{mid}.mp3"),
    )
    if partial_hash or full_hash:
        conn.execute(
            "INSERT INTO track_sidecars (metadata_id, sidecar_path, hash_partial, hash_full) "
            "VALUES (?, ?, ?, ?)",
            (mid, f"C:\\t\\{mid}.mp3.dlpmeta", partial_hash, full_hash),
        )
    if duration_ms is not None:
        conn.execute(
            "INSERT INTO technical_features (metadata_id, duration_ms) VALUES (?, ?)",
            (mid, duration_ms),
        )
    conn.commit()


@pytest.fixture
def db(in_memory_db: sqlite3.Connection) -> sqlite3.Connection:
    create_schema(in_memory_db)
    return in_memory_db


class TestFindHashDuplicates:
    """Tests for exact-match duplicate detection via BLAKE3 hashes."""

    @pytest.mark.integration
    def test_finds_identical_full_hash(self, db: sqlite3.Connection) -> None:
        m1, m2 = generate_metadata_id(), generate_metadata_id()
        _seed_track(db, m1, "Song A", "Artist", full_hash="abc123")
        _seed_track(db, m2, "Song A Copy", "Artist", full_hash="abc123")

        groups = find_hash_duplicates(db)
        assert len(groups) == 1
        assert len(groups[0].metadata_ids) == 2

    @pytest.mark.integration
    def test_finds_identical_partial_hash(self, db: sqlite3.Connection) -> None:
        m1, m2 = generate_metadata_id(), generate_metadata_id()
        _seed_track(db, m1, "Song A", "Artist", partial_hash="def456")
        _seed_track(db, m2, "Song A 2", "Artist", partial_hash="def456")

        groups = find_hash_duplicates(db)
        assert len(groups) >= 1

    @pytest.mark.integration
    def test_no_duplicates_returns_empty(self, db: sqlite3.Connection) -> None:
        m1, m2 = generate_metadata_id(), generate_metadata_id()
        _seed_track(db, m1, "Song A", "Artist", full_hash="aaa")
        _seed_track(db, m2, "Song B", "Artist", full_hash="bbb")

        groups = find_hash_duplicates(db)
        assert len(groups) == 0

    @pytest.mark.integration
    def test_three_way_duplicate(self, db: sqlite3.Connection) -> None:
        m1, m2, m3 = generate_metadata_id(), generate_metadata_id(), generate_metadata_id()
        _seed_track(db, m1, "Song", "Art", full_hash="same")
        _seed_track(db, m2, "Song", "Art", full_hash="same")
        _seed_track(db, m3, "Song", "Art", full_hash="same")

        groups = find_hash_duplicates(db)
        assert len(groups) == 1
        assert len(groups[0].metadata_ids) == 3


class TestFindFuzzyDuplicates:
    """Tests for fuzzy title+artist duplicate detection."""

    @pytest.mark.integration
    def test_finds_similar_titles(self, db: sqlite3.Connection) -> None:
        m1, m2 = generate_metadata_id(), generate_metadata_id()
        _seed_track(db, m1, "Into The Inner Space", "Attention", duration_ms=214000)
        _seed_track(db, m2, "Into The Inner Space (Radio Edit)", "Attention", duration_ms=210000)

        groups = find_fuzzy_duplicates(db, title_threshold=0.75, duration_tolerance_ms=10000)
        assert len(groups) >= 1

    @pytest.mark.integration
    def test_ignores_different_artists(self, db: sqlite3.Connection) -> None:
        m1, m2 = generate_metadata_id(), generate_metadata_id()
        _seed_track(db, m1, "Hello", "Adele", duration_ms=300000)
        _seed_track(db, m2, "Hello", "Lionel Richie", duration_ms=280000)

        groups = find_fuzzy_duplicates(db, title_threshold=0.75, duration_tolerance_ms=30000)
        assert len(groups) == 0

    @pytest.mark.integration
    def test_no_fuzzy_dupes(self, db: sqlite3.Connection) -> None:
        m1, m2 = generate_metadata_id(), generate_metadata_id()
        _seed_track(db, m1, "Completely Different", "ArtA", duration_ms=180000)
        _seed_track(db, m2, "Nothing Alike", "ArtB", duration_ms=300000)

        groups = find_fuzzy_duplicates(db, title_threshold=0.75)
        assert len(groups) == 0


class TestAssignDuplicateGroups:
    """Tests for writing duplicate_group IDs into playlist_signals."""

    @pytest.mark.integration
    def test_assigns_group_ids(self, db: sqlite3.Connection) -> None:
        m1, m2 = generate_metadata_id(), generate_metadata_id()
        _seed_track(db, m1, "Song", "Art", full_hash="same")
        _seed_track(db, m2, "Song", "Art", full_hash="same")
        # Ensure playlist_signals rows exist
        db.execute(
            "INSERT INTO playlist_signals (metadata_id) VALUES (?)", (m1,)
        )
        db.execute(
            "INSERT INTO playlist_signals (metadata_id) VALUES (?)", (m2,)
        )
        db.commit()

        dup_count, _remix_count = assign_duplicate_groups(db)
        assert dup_count >= 1

        rows = db.execute(
            "SELECT duplicate_group FROM playlist_signals WHERE metadata_id IN (?, ?)",
            (m1, m2),
        ).fetchall()
        groups = [r["duplicate_group"] for r in rows if r["duplicate_group"]]
        assert len(groups) == 2
        assert groups[0] == groups[1]  # same group

    @pytest.mark.integration
    def test_no_dupes_assigns_nothing(self, db: sqlite3.Connection) -> None:
        m1 = generate_metadata_id()
        _seed_track(db, m1, "Unique", "Art", full_hash="unique")
        db.execute(
            "INSERT INTO playlist_signals (metadata_id) VALUES (?)", (m1,)
        )
        db.commit()

        dup_count, _remix_count = assign_duplicate_groups(db)
        assert dup_count == 0


class TestFindRemixGroups:
    """Tests for remix-family grouping via base title extraction."""

    @pytest.mark.integration
    def test_groups_same_title_different_mixes(self, db: sqlite3.Connection) -> None:
        m1, m2, m3 = generate_metadata_id(), generate_metadata_id(), generate_metadata_id()
        _seed_track(db, m1, "Sandstorm (Original Mix)", "Darude")
        _seed_track(db, m2, "Sandstorm (Extended Mix)", "Darude")
        _seed_track(db, m3, "Sandstorm (Radio Edit)", "Darude")

        groups = find_remix_groups(db)
        assert len(groups) == 1
        assert len(groups[0].metadata_ids) == 3
        assert groups[0].match_type == "remix"

    @pytest.mark.integration
    def test_different_artists_not_grouped(self, db: sqlite3.Connection) -> None:
        m1, m2 = generate_metadata_id(), generate_metadata_id()
        _seed_track(db, m1, "Levels (Original Mix)", "Avicii")
        _seed_track(db, m2, "Levels (Club Mix)", "Other DJ")

        groups = find_remix_groups(db)
        assert len(groups) == 0

    @pytest.mark.integration
    def test_different_songs_not_grouped(self, db: sqlite3.Connection) -> None:
        m1, m2 = generate_metadata_id(), generate_metadata_id()
        _seed_track(db, m1, "Alpha (Extended Mix)", "Artist")
        _seed_track(db, m2, "Beta (Extended Mix)", "Artist")

        groups = find_remix_groups(db)
        assert len(groups) == 0

    @pytest.mark.integration
    def test_no_remix_suffix_no_false_group(self, db: sqlite3.Connection) -> None:
        m1, m2 = generate_metadata_id(), generate_metadata_id()
        _seed_track(db, m1, "Unique Song", "ArtA")
        _seed_track(db, m2, "Another Song", "ArtB")

        groups = find_remix_groups(db)
        assert len(groups) == 0

    @pytest.mark.integration
    def test_named_remix_grouped_with_original(self, db: sqlite3.Connection) -> None:
        m1, m2 = generate_metadata_id(), generate_metadata_id()
        _seed_track(db, m1, "Play On (Eddie Thoneick Remix)", "Todd Terry")
        _seed_track(db, m2, "Play On (Original Mix)", "Todd Terry")

        groups = find_remix_groups(db)
        assert len(groups) == 1
        assert set(groups[0].metadata_ids) == {m1, m2}


class TestAssignRemixGroups:
    """Tests for remix_group column being written by assign_duplicate_groups."""

    @pytest.mark.integration
    def test_assigns_remix_group_ids(self, db: sqlite3.Connection) -> None:
        m1, m2 = generate_metadata_id(), generate_metadata_id()
        _seed_track(db, m1, "Flow (Rave Mix)", "Vinylgroover")
        _seed_track(db, m2, "Flow (Club Mix)", "Vinylgroover")
        db.execute("INSERT INTO playlist_signals (metadata_id) VALUES (?)", (m1,))
        db.execute("INSERT INTO playlist_signals (metadata_id) VALUES (?)", (m2,))
        db.commit()

        _dup_count, remix_count = assign_duplicate_groups(db)
        assert remix_count >= 1

        rows = db.execute(
            "SELECT remix_group FROM playlist_signals WHERE metadata_id IN (?, ?)",
            (m1, m2),
        ).fetchall()
        groups = [r["remix_group"] for r in rows if r["remix_group"]]
        assert len(groups) == 2
        assert groups[0] == groups[1]
