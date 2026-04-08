"""TDD tests for enhanced multi-tier version grouping (Phase 6) -- written before implementation."""
from __future__ import annotations

import sqlite3

import pytest

from musesleuth.db import create_schema, generate_metadata_id
from tests.conftest import insert_dummy_track


def _seed_with_embedding(
    conn: sqlite3.Connection, mid: str, title: str, artist: str, vec: list[float]
) -> None:
    """Insert a track + a global embedding row for version-grouping tests."""
    from musesleuth.timbre import serialize_array

    insert_dummy_track(conn, mid)
    conn.execute(
        "UPDATE tracks SET title = ?, artist = ? WHERE metadata_id = ?",
        (title, artist, mid),
    )
    blob = serialize_array(vec)
    conn.execute(
        """
        INSERT INTO embeddings (metadata_id, model, scope, dim, vector, computed_at)
        VALUES (?, 'mfcc_stat', 'global', ?, ?, datetime('now'))
        """,
        (mid, len(vec), blob),
    )
    conn.commit()


@pytest.fixture
def db(in_memory_db: sqlite3.Connection) -> sqlite3.Connection:
    create_schema(in_memory_db)
    return in_memory_db


class TestFindEmbeddingVersions:
    """Tests for embedding-similarity-based version grouping."""

    @pytest.mark.integration
    def test_identical_embeddings_grouped(self, db: sqlite3.Connection) -> None:
        """Two tracks with identical embeddings and same artist are grouped."""
        from musesleuth.dedup import find_embedding_versions

        m1, m2 = generate_metadata_id(), generate_metadata_id()
        vec = [1.0, 2.0, 3.0, 4.0, 5.0]
        _seed_with_embedding(db, m1, "Song A", "Artist", vec)
        _seed_with_embedding(db, m2, "Song A (Remix)", "Artist", vec)

        groups = find_embedding_versions(db, cosine_threshold=0.1)
        assert len(groups) >= 1
        found = any(
            set(g.metadata_ids) == {m1, m2} for g in groups
        )
        assert found, "Expected group containing both tracks"

    @pytest.mark.integration
    def test_different_embeddings_not_grouped(self, db: sqlite3.Connection) -> None:
        """Tracks with very different embeddings are NOT grouped."""
        from musesleuth.dedup import find_embedding_versions

        m1, m2 = generate_metadata_id(), generate_metadata_id()
        _seed_with_embedding(db, m1, "Song A", "Artist", [1.0, 0.0, 0.0, 0.0, 0.0])
        _seed_with_embedding(db, m2, "Song B", "Artist", [0.0, 0.0, 0.0, 0.0, 1.0])

        groups = find_embedding_versions(db, cosine_threshold=0.1)
        paired = any(
            set(g.metadata_ids) == {m1, m2} for g in groups
        )
        assert not paired

    @pytest.mark.integration
    def test_different_artists_not_grouped(self, db: sqlite3.Connection) -> None:
        """Same embedding but different artist -> NOT grouped."""
        from musesleuth.dedup import find_embedding_versions

        m1, m2 = generate_metadata_id(), generate_metadata_id()
        vec = [1.0, 2.0, 3.0, 4.0, 5.0]
        _seed_with_embedding(db, m1, "Song", "ArtistA", vec)
        _seed_with_embedding(db, m2, "Song", "ArtistB", vec)

        groups = find_embedding_versions(db, cosine_threshold=0.1)
        paired = any(
            set(g.metadata_ids) == {m1, m2} for g in groups
        )
        assert not paired

    @pytest.mark.integration
    def test_no_embeddings_returns_empty(self, db: sqlite3.Connection) -> None:
        """No tracks with embeddings -> empty list."""
        from musesleuth.dedup import find_embedding_versions

        m1 = generate_metadata_id()
        insert_dummy_track(db, m1)
        groups = find_embedding_versions(db)
        assert groups == []


class TestPersistVersionGroups:
    """Tests for persisting multi-tier version groups to the version_groups table."""

    @pytest.mark.integration
    def test_persist_version_groups(self, db: sqlite3.Connection) -> None:
        """Version groups are written to the version_groups table."""
        from musesleuth.dedup import DuplicateGroup, persist_version_groups

        m1, m2 = generate_metadata_id(), generate_metadata_id()
        insert_dummy_track(db, m1)
        insert_dummy_track(db, m2)

        groups = [
            DuplicateGroup(group_id="grp-1", metadata_ids=[m1, m2], match_type="hash_full"),
        ]
        persist_version_groups(db, groups)

        rows = db.execute(
            "SELECT * FROM version_groups WHERE group_id = 'grp-1' ORDER BY metadata_id"
        ).fetchall()
        assert len(rows) == 2
        assert {rows[0]["metadata_id"], rows[1]["metadata_id"]} == {m1, m2}
        assert rows[0]["method"] == "hash_full"

    @pytest.mark.integration
    def test_persist_replaces_on_rerun(self, db: sqlite3.Connection) -> None:
        """Running persist twice for same group doesn't duplicate rows."""
        from musesleuth.dedup import DuplicateGroup, persist_version_groups

        m1, m2 = generate_metadata_id(), generate_metadata_id()
        insert_dummy_track(db, m1)
        insert_dummy_track(db, m2)

        groups = [
            DuplicateGroup(group_id="grp-2", metadata_ids=[m1, m2], match_type="fuzzy"),
        ]
        persist_version_groups(db, groups)
        persist_version_groups(db, groups)

        count = db.execute(
            "SELECT COUNT(*) FROM version_groups WHERE group_id = 'grp-2'"
        ).fetchone()[0]
        assert count == 2  # not 4

    @pytest.mark.integration
    def test_persist_multiple_groups(self, db: sqlite3.Connection) -> None:
        """Multiple groups persist independently."""
        from musesleuth.dedup import DuplicateGroup, persist_version_groups

        m1, m2, m3, m4 = (generate_metadata_id() for _ in range(4))
        for m in (m1, m2, m3, m4):
            insert_dummy_track(db, m)

        groups = [
            DuplicateGroup(group_id="g-a", metadata_ids=[m1, m2], match_type="hash_full"),
            DuplicateGroup(group_id="g-b", metadata_ids=[m3, m4], match_type="embedding"),
        ]
        persist_version_groups(db, groups)

        total = db.execute("SELECT COUNT(*) FROM version_groups").fetchone()[0]
        assert total == 4


class TestRunFullDedup:
    """Tests for the all-in-one dedup runner that combines all tiers."""

    @pytest.mark.integration
    def test_run_full_dedup_populates_version_groups(self, db: sqlite3.Connection) -> None:
        """run_full_dedup writes results to both playlist_signals and version_groups."""
        from musesleuth.dedup import run_full_dedup

        m1, m2 = generate_metadata_id(), generate_metadata_id()
        insert_dummy_track(db, m1)
        insert_dummy_track(db, m2)
        db.execute("UPDATE tracks SET title = 'Song', artist = 'Art' WHERE metadata_id = ?", (m1,))
        db.execute("UPDATE tracks SET title = 'Song', artist = 'Art' WHERE metadata_id = ?", (m2,))
        db.execute(
            "INSERT INTO track_sidecars (metadata_id, sidecar_path, hash_full) VALUES (?, 'x', 'same')",
            (m1,),
        )
        db.execute(
            "INSERT INTO track_sidecars (metadata_id, sidecar_path, hash_full) VALUES (?, 'y', 'same')",
            (m2,),
        )
        db.execute("INSERT INTO playlist_signals (metadata_id) VALUES (?)", (m1,))
        db.execute("INSERT INTO playlist_signals (metadata_id) VALUES (?)", (m2,))
        db.commit()

        stats = run_full_dedup(db)
        assert stats["duplicate_groups"] >= 1

        vg_count = db.execute("SELECT COUNT(*) FROM version_groups").fetchone()[0]
        assert vg_count >= 2
