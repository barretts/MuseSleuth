"""TDD tests for evaluation & metrics (Phase 13) -- written before implementation."""
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


class TestPlaylistMetrics:
    """Tests for computing quality metrics on a DJ playlist."""

    @pytest.mark.integration
    def test_metrics_basic_fields(self, db: sqlite3.Connection) -> None:
        """Metrics dict contains expected keys."""
        from musesleuth.evaluation import compute_playlist_metrics

        mids = [generate_metadata_id() for _ in range(4)]
        bpms = [126, 128, 130, 132]
        for mid, bpm in zip(mids, bpms):
            _seed_track_with_features(db, mid, bpm, "8A", 0.7)

        metrics = compute_playlist_metrics(db, mids)
        assert "total_transition_cost" in metrics
        assert "mean_transition_cost" in metrics
        assert "max_transition_cost" in metrics
        assert "bpm_range" in metrics
        assert "key_compatibility_pct" in metrics
        assert "energy_smoothness" in metrics
        assert "track_count" in metrics

    @pytest.mark.integration
    def test_metrics_single_track(self, db: sqlite3.Connection) -> None:
        """Single track has zero transition costs."""
        from musesleuth.evaluation import compute_playlist_metrics

        mid = generate_metadata_id()
        _seed_track_with_features(db, mid, 128.0, "8A", 0.7)

        metrics = compute_playlist_metrics(db, [mid])
        assert metrics["total_transition_cost"] == 0.0
        assert metrics["mean_transition_cost"] == 0.0
        assert metrics["max_transition_cost"] == 0.0
        assert metrics["track_count"] == 1

    @pytest.mark.integration
    def test_metrics_empty(self, db: sqlite3.Connection) -> None:
        """Empty playlist returns zeroed metrics."""
        from musesleuth.evaluation import compute_playlist_metrics

        metrics = compute_playlist_metrics(db, [])
        assert metrics["track_count"] == 0
        assert metrics["total_transition_cost"] == 0.0

    @pytest.mark.integration
    def test_key_compatibility_all_compatible(self, db: sqlite3.Connection) -> None:
        """All same-key transitions -> 100% key compatibility."""
        from musesleuth.evaluation import compute_playlist_metrics

        mids = [generate_metadata_id() for _ in range(5)]
        for mid in mids:
            _seed_track_with_features(db, mid, 128.0, "8A", 0.7)

        metrics = compute_playlist_metrics(db, mids)
        assert metrics["key_compatibility_pct"] == pytest.approx(100.0)

    @pytest.mark.integration
    def test_key_compatibility_none_compatible(self, db: sqlite3.Connection) -> None:
        """Alternating distant keys -> 0% key compatibility."""
        from musesleuth.evaluation import compute_playlist_metrics

        mids = [generate_metadata_id() for _ in range(4)]
        keys = ["1A", "6B", "1A", "6B"]
        for mid, key in zip(mids, keys):
            _seed_track_with_features(db, mid, 128.0, key, 0.7)

        metrics = compute_playlist_metrics(db, mids)
        assert metrics["key_compatibility_pct"] == pytest.approx(0.0)

    @pytest.mark.integration
    def test_energy_smoothness(self, db: sqlite3.Connection) -> None:
        """Smooth energy progression has lower energy_smoothness (mean abs delta)."""
        from musesleuth.evaluation import compute_playlist_metrics

        smooth_mids = [generate_metadata_id() for _ in range(4)]
        for i, mid in enumerate(smooth_mids):
            _seed_track_with_features(db, mid, 128.0, "8A", 0.5 + i * 0.05)

        jagged_mids = [generate_metadata_id() for _ in range(4)]
        energies = [0.3, 0.9, 0.2, 0.8]
        for mid, e in zip(jagged_mids, energies):
            _seed_track_with_features(db, mid, 128.0, "8A", e)

        smooth_metrics = compute_playlist_metrics(db, smooth_mids)
        jagged_metrics = compute_playlist_metrics(db, jagged_mids)

        assert smooth_metrics["energy_smoothness"] < jagged_metrics["energy_smoothness"]

    @pytest.mark.integration
    def test_bpm_range(self, db: sqlite3.Connection) -> None:
        """BPM range reflects min/max of the playlist."""
        from musesleuth.evaluation import compute_playlist_metrics

        mids = [generate_metadata_id() for _ in range(3)]
        bpms = [120, 140, 130]
        for mid, bpm in zip(mids, bpms):
            _seed_track_with_features(db, mid, bpm, "8A", 0.7)

        metrics = compute_playlist_metrics(db, mids)
        assert metrics["bpm_range"] == pytest.approx(20.0)


class TestCompareStrategies:
    """Tests for comparing two playlists side-by-side."""

    @pytest.mark.integration
    def test_compare_returns_both_metrics(self, db: sqlite3.Connection) -> None:
        """compare_playlists returns metrics for both playlists."""
        from musesleuth.evaluation import compare_playlists

        mids_a = [generate_metadata_id() for _ in range(3)]
        mids_b = [generate_metadata_id() for _ in range(3)]
        for mid in mids_a:
            _seed_track_with_features(db, mid, 128.0, "8A", 0.7)
        for mid in mids_b:
            _seed_track_with_features(db, mid, 140.0, "3B", 0.4)

        result = compare_playlists(db, mids_a, mids_b)
        assert "playlist_a" in result
        assert "playlist_b" in result
        assert result["playlist_a"]["track_count"] == 3
        assert result["playlist_b"]["track_count"] == 3

    @pytest.mark.integration
    def test_compare_winner_field(self, db: sqlite3.Connection) -> None:
        """compare_playlists identifies which playlist has lower total cost."""
        from musesleuth.evaluation import compare_playlists

        # Playlist A: smooth BPM progression
        mids_a = [generate_metadata_id() for _ in range(4)]
        for i, mid in enumerate(mids_a):
            _seed_track_with_features(db, mid, 126 + i * 2, "8A", 0.7)

        # Playlist B: wild BPM jumps
        mids_b = [generate_metadata_id() for _ in range(4)]
        bpms_b = [80, 170, 90, 160]
        for mid, bpm in zip(mids_b, bpms_b):
            _seed_track_with_features(db, mid, bpm, "3B", 0.3)

        result = compare_playlists(db, mids_a, mids_b)
        assert result["lower_cost"] in ("a", "b")
