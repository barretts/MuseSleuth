"""TDD tests for similarity graph & ANN index (Phase 8) -- written before implementation."""
from __future__ import annotations

import sqlite3

import pytest

from musesleuth.db import create_schema, generate_metadata_id
from tests.conftest import insert_dummy_track


def _seed_with_embedding(
    conn: sqlite3.Connection, mid: str, vec: list[float]
) -> None:
    """Insert a track + a global embedding for similarity tests."""
    from musesleuth.timbre import serialize_array

    insert_dummy_track(conn, mid)
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


class TestBuildSimilarityGraph:
    """Tests for building k-NN similarity edges from embeddings."""

    @pytest.mark.integration
    def test_builds_edges_for_similar_tracks(self, db: sqlite3.Connection) -> None:
        """Two very similar embeddings produce a similarity edge."""
        from musesleuth.similarity import build_similarity_graph

        m1, m2 = generate_metadata_id(), generate_metadata_id()
        _seed_with_embedding(db, m1, [1.0, 2.0, 3.0, 4.0])
        _seed_with_embedding(db, m2, [1.1, 2.1, 3.1, 4.1])

        count = build_similarity_graph(db, k=5)
        assert count >= 1

        rows = db.execute("SELECT * FROM similarity_edges").fetchall()
        assert len(rows) >= 1
        mids_in_edges = set()
        for r in rows:
            mids_in_edges.add(r["src_id"])
            mids_in_edges.add(r["dst_id"])
        assert m1 in mids_in_edges or m2 in mids_in_edges

    @pytest.mark.integration
    def test_edges_have_required_fields(self, db: sqlite3.Connection) -> None:
        """Each edge has src_id, dst_id, metric, value, computed_at."""
        from musesleuth.similarity import build_similarity_graph

        m1, m2 = generate_metadata_id(), generate_metadata_id()
        _seed_with_embedding(db, m1, [1.0, 0.0, 0.0])
        _seed_with_embedding(db, m2, [0.9, 0.1, 0.0])

        build_similarity_graph(db, k=5)

        row = db.execute("SELECT * FROM similarity_edges LIMIT 1").fetchone()
        assert row is not None
        assert row["src_id"] is not None
        assert row["dst_id"] is not None
        assert row["metric"] is not None
        assert row["value"] is not None
        assert row["computed_at"] is not None

    @pytest.mark.integration
    def test_no_self_edges(self, db: sqlite3.Connection) -> None:
        """No edge should have src_id == dst_id."""
        from musesleuth.similarity import build_similarity_graph

        m1, m2, m3 = (generate_metadata_id() for _ in range(3))
        _seed_with_embedding(db, m1, [1.0, 0.0])
        _seed_with_embedding(db, m2, [0.0, 1.0])
        _seed_with_embedding(db, m3, [0.5, 0.5])

        build_similarity_graph(db, k=5)

        self_edges = db.execute(
            "SELECT COUNT(*) FROM similarity_edges WHERE src_id = dst_id"
        ).fetchone()[0]
        assert self_edges == 0

    @pytest.mark.integration
    def test_replaces_on_rebuild(self, db: sqlite3.Connection) -> None:
        """Rebuilding the graph replaces existing edges."""
        from musesleuth.similarity import build_similarity_graph

        m1, m2 = generate_metadata_id(), generate_metadata_id()
        _seed_with_embedding(db, m1, [1.0, 2.0])
        _seed_with_embedding(db, m2, [1.1, 2.1])

        build_similarity_graph(db, k=5)
        first_count = db.execute("SELECT COUNT(*) FROM similarity_edges").fetchone()[0]

        build_similarity_graph(db, k=5)
        second_count = db.execute("SELECT COUNT(*) FROM similarity_edges").fetchone()[0]

        assert second_count == first_count

    @pytest.mark.integration
    def test_empty_embeddings_returns_zero(self, db: sqlite3.Connection) -> None:
        """No embeddings -> returns 0 edges."""
        from musesleuth.similarity import build_similarity_graph

        count = build_similarity_graph(db, k=5)
        assert count == 0

    @pytest.mark.integration
    def test_single_track_returns_zero(self, db: sqlite3.Connection) -> None:
        """Single track has no neighbors."""
        from musesleuth.similarity import build_similarity_graph

        m1 = generate_metadata_id()
        _seed_with_embedding(db, m1, [1.0, 2.0, 3.0])

        count = build_similarity_graph(db, k=5)
        assert count == 0


class TestFindNeighbors:
    """Tests for querying the k nearest neighbors of a given track."""

    @pytest.mark.integration
    def test_find_neighbors_returns_sorted(self, db: sqlite3.Connection) -> None:
        """Neighbors are returned sorted by ascending distance."""
        from musesleuth.similarity import build_similarity_graph, find_neighbors

        mids = [generate_metadata_id() for _ in range(5)]
        vecs = [
            [1.0, 0.0, 0.0],
            [0.9, 0.1, 0.0],
            [0.5, 0.5, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 1.0],
        ]
        for mid, vec in zip(mids, vecs):
            _seed_with_embedding(db, mid, vec)

        build_similarity_graph(db, k=4)
        neighbors = find_neighbors(db, mids[0], k=3)

        assert len(neighbors) <= 3
        # Check sorted by value (ascending = most similar first)
        for i in range(len(neighbors) - 1):
            assert neighbors[i]["value"] <= neighbors[i + 1]["value"]

    @pytest.mark.integration
    def test_find_neighbors_excludes_self(self, db: sqlite3.Connection) -> None:
        """Query track doesn't appear in its own neighbors."""
        from musesleuth.similarity import build_similarity_graph, find_neighbors

        m1, m2 = generate_metadata_id(), generate_metadata_id()
        _seed_with_embedding(db, m1, [1.0, 0.0])
        _seed_with_embedding(db, m2, [0.9, 0.1])

        build_similarity_graph(db, k=5)
        neighbors = find_neighbors(db, m1, k=5)

        neighbor_ids = [n["metadata_id"] for n in neighbors]
        assert m1 not in neighbor_ids

    @pytest.mark.integration
    def test_find_neighbors_unknown_track(self, db: sqlite3.Connection) -> None:
        """Unknown track returns empty list."""
        from musesleuth.similarity import find_neighbors

        neighbors = find_neighbors(db, "nonexistent-id", k=5)
        assert neighbors == []
