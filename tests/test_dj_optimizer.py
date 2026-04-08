"""TDD tests for DJ playlist optimizer (Phase 10) -- written before implementation."""
from __future__ import annotations

import sqlite3

import pytest

from musesleuth.db import create_schema, generate_metadata_id
from tests.conftest import insert_dummy_track


def _seed_track_with_features(
    conn: sqlite3.Connection,
    mid: str,
    bpm: float,
    camelot: str,
    energy: float,
    lufs: float = -8.0,
) -> None:
    """Insert a track with musical features, playlist signals, and loudness."""
    insert_dummy_track(conn, mid)
    conn.execute(
        "INSERT INTO musical_features (metadata_id, bpm_final, energy) VALUES (?, ?, ?)",
        (mid, bpm, energy),
    )
    conn.execute(
        "INSERT INTO playlist_signals (metadata_id, camelot_key, quality_verdict) VALUES (?, ?, 'pass')",
        (mid, camelot),
    )
    conn.execute(
        "INSERT INTO loudness_features (metadata_id, lufs_integrated, analyzed_at) VALUES (?, ?, datetime('now'))",
        (mid, lufs),
    )
    conn.commit()


@pytest.fixture
def db(in_memory_db: sqlite3.Connection) -> sqlite3.Connection:
    create_schema(in_memory_db)
    return in_memory_db


class TestGreedyOptimizer:
    """Tests for greedy nearest-neighbor DJ ordering."""

    @pytest.mark.integration
    def test_greedy_returns_all_candidates(self, db: sqlite3.Connection) -> None:
        """Greedy optimizer visits all candidate tracks exactly once."""
        from musesleuth.dj_optimizer import greedy_order

        mids = [generate_metadata_id() for _ in range(5)]
        bpms = [126, 128, 130, 125, 132]
        for mid, bpm in zip(mids, bpms):
            _seed_track_with_features(db, mid, bpm, "8A", 0.7)

        ordered = greedy_order(db, mids)
        assert set(ordered) == set(mids)
        assert len(ordered) == len(mids)

    @pytest.mark.integration
    def test_greedy_prefers_similar_bpm(self, db: sqlite3.Connection) -> None:
        """Starting from a 128 BPM track, next should be closer BPM, not 80."""
        from musesleuth.dj_optimizer import greedy_order

        m1 = generate_metadata_id()
        m_close = generate_metadata_id()
        m_far = generate_metadata_id()
        _seed_track_with_features(db, m1, 128.0, "8A", 0.7)
        _seed_track_with_features(db, m_close, 130.0, "8A", 0.7)
        _seed_track_with_features(db, m_far, 80.0, "8A", 0.7)

        ordered = greedy_order(db, [m1, m_close, m_far], seed_id=m1)
        # m_close should come before m_far
        assert ordered[0] == m1
        assert ordered.index(m_close) < ordered.index(m_far)

    @pytest.mark.integration
    def test_greedy_single_track(self, db: sqlite3.Connection) -> None:
        """Single track returns that track."""
        from musesleuth.dj_optimizer import greedy_order

        m1 = generate_metadata_id()
        _seed_track_with_features(db, m1, 128.0, "8A", 0.7)

        ordered = greedy_order(db, [m1])
        assert ordered == [m1]

    @pytest.mark.integration
    def test_greedy_empty(self, db: sqlite3.Connection) -> None:
        """Empty candidates returns empty list."""
        from musesleuth.dj_optimizer import greedy_order

        assert greedy_order(db, []) == []


class TestTwoOptRefinement:
    """Tests for 2-opt local search refinement."""

    @pytest.mark.integration
    def test_two_opt_does_not_increase_cost(self, db: sqlite3.Connection) -> None:
        """2-opt refinement should not increase total path cost."""
        from musesleuth.dj_optimizer import greedy_order, two_opt_refine, total_path_cost

        mids = [generate_metadata_id() for _ in range(8)]
        bpms = [120, 122, 140, 125, 160, 128, 135, 118]
        camelots = ["8A", "8B", "3B", "9A", "1A", "7A", "8A", "9B"]
        for mid, bpm, cam in zip(mids, bpms, camelots):
            _seed_track_with_features(db, mid, bpm, cam, 0.6)

        initial = greedy_order(db, mids)
        initial_cost = total_path_cost(db, initial)

        refined = two_opt_refine(db, initial, max_iterations=50)
        refined_cost = total_path_cost(db, refined)

        assert refined_cost <= initial_cost + 1e-6  # allow float rounding
        assert set(refined) == set(mids)

    @pytest.mark.integration
    def test_two_opt_preserves_all_tracks(self, db: sqlite3.Connection) -> None:
        """2-opt doesn't lose or duplicate tracks."""
        from musesleuth.dj_optimizer import greedy_order, two_opt_refine

        mids = [generate_metadata_id() for _ in range(6)]
        for i, mid in enumerate(mids):
            _seed_track_with_features(db, mid, 120 + i * 5, "8A", 0.5 + i * 0.05)

        initial = greedy_order(db, mids)
        refined = two_opt_refine(db, initial, max_iterations=20)
        assert set(refined) == set(mids)
        assert len(refined) == len(mids)


class TestTotalPathCost:
    """Tests for total path cost computation."""

    @pytest.mark.integration
    def test_single_track_zero_cost(self, db: sqlite3.Connection) -> None:
        """Single-track path has zero cost."""
        from musesleuth.dj_optimizer import total_path_cost

        m1 = generate_metadata_id()
        _seed_track_with_features(db, m1, 128.0, "8A", 0.7)

        assert total_path_cost(db, [m1]) == 0.0

    @pytest.mark.integration
    def test_empty_path_zero_cost(self, db: sqlite3.Connection) -> None:
        """Empty path has zero cost."""
        from musesleuth.dj_optimizer import total_path_cost

        assert total_path_cost(db, []) == 0.0

    @pytest.mark.integration
    def test_cost_increases_with_dissimilar_tracks(self, db: sqlite3.Connection) -> None:
        """Path through dissimilar tracks costs more than similar ones."""
        from musesleuth.dj_optimizer import total_path_cost

        m1 = generate_metadata_id()
        m2 = generate_metadata_id()
        m3 = generate_metadata_id()
        _seed_track_with_features(db, m1, 128.0, "8A", 0.7)
        _seed_track_with_features(db, m2, 130.0, "8A", 0.7)
        _seed_track_with_features(db, m3, 180.0, "3B", 0.2)

        cost_similar = total_path_cost(db, [m1, m2])
        cost_dissimilar = total_path_cost(db, [m1, m3])
        assert cost_similar < cost_dissimilar


class TestDjFlowPlaylistStrategy:
    """Tests for the full DJ flow playlist strategy integration."""

    @pytest.mark.integration
    def test_generate_dj_flow_playlist(self, db: sqlite3.Connection) -> None:
        """DJ flow strategy produces a valid playlist."""
        from musesleuth.dj_optimizer import DjFlowPlaylist

        mids = [generate_metadata_id() for _ in range(6)]
        bpms = [126, 128, 130, 125, 132, 127]
        for mid, bpm in zip(mids, bpms):
            _seed_track_with_features(db, mid, bpm, "8A", 0.7)

        strategy = DjFlowPlaylist()
        result = strategy.generate(db, "Test DJ Mix", {}, limit=6)

        assert result.track_count == 6
        assert result.name == "Test DJ Mix"
        assert result.playlist_id is not None

    @pytest.mark.integration
    def test_dj_flow_filters_non_pass_tracks(self, db: sqlite3.Connection) -> None:
        """DJ flow excludes tracks with quality_verdict != 'pass'."""
        from musesleuth.dj_optimizer import DjFlowPlaylist

        m_good = generate_metadata_id()
        m_live = generate_metadata_id()
        _seed_track_with_features(db, m_good, 128.0, "8A", 0.7)
        _seed_track_with_features(db, m_live, 128.0, "8A", 0.7)
        db.execute(
            "UPDATE playlist_signals SET quality_verdict = 'live' WHERE metadata_id = ?",
            (m_live,),
        )
        db.commit()

        strategy = DjFlowPlaylist()
        result = strategy.generate(db, "Clean Mix", {}, limit=10)

        # Only m_good should be in the playlist
        tracks = db.execute(
            "SELECT metadata_id FROM playlist_tracks WHERE playlist_id = ? ORDER BY position",
            (result.playlist_id,),
        ).fetchall()
        track_ids = [r["metadata_id"] for r in tracks]
        assert m_good in track_ids
        assert m_live not in track_ids

    @pytest.mark.integration
    def test_dj_flow_empty_db(self, db: sqlite3.Connection) -> None:
        """DJ flow with no eligible tracks produces an empty playlist."""
        from musesleuth.dj_optimizer import DjFlowPlaylist

        strategy = DjFlowPlaylist()
        result = strategy.generate(db, "Empty Mix", {}, limit=10)
        assert result.track_count == 0
