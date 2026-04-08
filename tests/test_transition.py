"""TDD tests for transition scoring function (Phase 9) -- written before implementation."""
from __future__ import annotations

import pytest


class TestTransitionCost:
    """Tests for the multi-factor transition cost function."""

    @pytest.mark.unit
    def test_identical_tracks_zero_cost(self) -> None:
        """Two identical feature dicts produce cost ~0."""
        from musesleuth.transition import transition_cost, TrackFeatures

        t = TrackFeatures(
            bpm=128.0, camelot_key="8A", energy=0.7, lufs=-8.0,
            timbre_vec=[1.0, 2.0, 3.0], embedding_dist=0.0,
        )
        cost = transition_cost(t, t)
        assert cost == pytest.approx(0.0, abs=0.01)

    @pytest.mark.unit
    def test_compatible_key_lower_cost(self) -> None:
        """Camelot-compatible keys have lower cost than incompatible."""
        from musesleuth.transition import transition_cost, TrackFeatures

        base = TrackFeatures(bpm=128.0, camelot_key="8A", energy=0.7, lufs=-8.0)
        compatible = TrackFeatures(bpm=128.0, camelot_key="8B", energy=0.7, lufs=-8.0)
        incompatible = TrackFeatures(bpm=128.0, camelot_key="3B", energy=0.7, lufs=-8.0)

        cost_compat = transition_cost(base, compatible)
        cost_incompat = transition_cost(base, incompatible)
        assert cost_compat < cost_incompat

    @pytest.mark.unit
    def test_bpm_penalty(self) -> None:
        """Large BPM difference increases cost."""
        from musesleuth.transition import transition_cost, TrackFeatures

        a = TrackFeatures(bpm=128.0, camelot_key="8A", energy=0.7, lufs=-8.0)
        close = TrackFeatures(bpm=130.0, camelot_key="8A", energy=0.7, lufs=-8.0)
        far = TrackFeatures(bpm=160.0, camelot_key="8A", energy=0.7, lufs=-8.0)

        cost_close = transition_cost(a, close)
        cost_far = transition_cost(a, far)
        assert cost_close < cost_far

    @pytest.mark.unit
    def test_energy_penalty(self) -> None:
        """Large energy jump increases cost."""
        from musesleuth.transition import transition_cost, TrackFeatures

        a = TrackFeatures(bpm=128.0, camelot_key="8A", energy=0.3, lufs=-8.0)
        smooth = TrackFeatures(bpm=128.0, camelot_key="8A", energy=0.35, lufs=-8.0)
        jarring = TrackFeatures(bpm=128.0, camelot_key="8A", energy=0.9, lufs=-8.0)

        cost_smooth = transition_cost(a, smooth)
        cost_jarring = transition_cost(a, jarring)
        assert cost_smooth < cost_jarring

    @pytest.mark.unit
    def test_loudness_penalty(self) -> None:
        """Large LUFS difference increases cost."""
        from musesleuth.transition import transition_cost, TrackFeatures

        a = TrackFeatures(bpm=128.0, camelot_key="8A", energy=0.7, lufs=-8.0)
        similar = TrackFeatures(bpm=128.0, camelot_key="8A", energy=0.7, lufs=-9.0)
        different = TrackFeatures(bpm=128.0, camelot_key="8A", energy=0.7, lufs=-20.0)

        cost_similar = transition_cost(a, similar)
        cost_different = transition_cost(a, different)
        assert cost_similar < cost_different

    @pytest.mark.unit
    def test_cost_always_non_negative(self) -> None:
        """Cost is always >= 0."""
        from musesleuth.transition import transition_cost, TrackFeatures

        a = TrackFeatures(bpm=80.0, camelot_key="1A", energy=0.1, lufs=-20.0)
        b = TrackFeatures(bpm=180.0, camelot_key="12B", energy=0.9, lufs=-5.0)

        assert transition_cost(a, b) >= 0.0

    @pytest.mark.unit
    def test_custom_weights(self) -> None:
        """Custom weights override defaults."""
        from musesleuth.transition import transition_cost, TrackFeatures, TransitionWeights

        a = TrackFeatures(bpm=128.0, camelot_key="8A", energy=0.7, lufs=-8.0)
        b = TrackFeatures(bpm=160.0, camelot_key="3B", energy=0.2, lufs=-20.0)

        # Zero all weights except BPM
        w = TransitionWeights(bpm=1.0, key=0.0, energy=0.0, loudness=0.0, timbre=0.0, embedding=0.0)
        cost_bpm_only = transition_cost(a, b, weights=w)

        # Zero all weights except key
        w2 = TransitionWeights(bpm=0.0, key=1.0, energy=0.0, loudness=0.0, timbre=0.0, embedding=0.0)
        cost_key_only = transition_cost(a, b, weights=w2)

        # They should be different (different dominant factors)
        assert cost_bpm_only != cost_key_only

    @pytest.mark.unit
    def test_none_values_handled(self) -> None:
        """Missing features (None) don't crash, just skip those terms."""
        from musesleuth.transition import transition_cost, TrackFeatures

        a = TrackFeatures(bpm=None, camelot_key=None, energy=None, lufs=None)
        b = TrackFeatures(bpm=128.0, camelot_key="8A", energy=0.7, lufs=-8.0)

        cost = transition_cost(a, b)
        assert cost >= 0.0

    @pytest.mark.unit
    def test_embedding_vector_similarity_affects_cost(self) -> None:
        """More similar embedding vectors should produce lower transition cost."""
        from musesleuth.transition import transition_cost, TrackFeatures

        base = TrackFeatures(
            bpm=128.0,
            camelot_key="8A",
            energy=0.7,
            lufs=-8.0,
            embedding_vec=[1.0, 0.0, 0.0],
        )
        similar = TrackFeatures(
            bpm=128.0,
            camelot_key="8A",
            energy=0.7,
            lufs=-8.0,
            embedding_vec=[0.99, 0.01, 0.0],
        )
        dissimilar = TrackFeatures(
            bpm=128.0,
            camelot_key="8A",
            energy=0.7,
            lufs=-8.0,
            embedding_vec=[0.0, 1.0, 0.0],
        )

        assert transition_cost(base, similar) < transition_cost(base, dissimilar)


class TestLoadTrackFeatures:
    """Tests for loading TrackFeatures from the DB."""

    @pytest.mark.integration
    def test_load_features_from_db(self, in_memory_db) -> None:
        """TrackFeatures loaded from DB contain expected fields."""
        from musesleuth.transition import load_track_features, TrackFeatures
        from musesleuth.db import create_schema, generate_metadata_id
        from tests.conftest import insert_dummy_track

        create_schema(in_memory_db)
        mid = generate_metadata_id()
        insert_dummy_track(in_memory_db, mid)

        # Seed some features
        in_memory_db.execute(
            "INSERT INTO musical_features (metadata_id, bpm_final, energy) VALUES (?, 128.0, 0.7)",
            (mid,),
        )
        in_memory_db.execute(
            "INSERT INTO playlist_signals (metadata_id, camelot_key) VALUES (?, '8A')",
            (mid,),
        )
        in_memory_db.execute(
            "INSERT INTO loudness_features (metadata_id, lufs_integrated) VALUES (?, -8.5)",
            (mid,),
        )
        in_memory_db.commit()

        feat = load_track_features(in_memory_db, mid)
        assert isinstance(feat, TrackFeatures)
        assert feat.bpm == pytest.approx(128.0)
        assert feat.camelot_key == "8A"
        assert feat.energy == pytest.approx(0.7)
        assert feat.lufs == pytest.approx(-8.5)

    @pytest.mark.integration
    def test_load_features_includes_timbre_and_embedding_vectors(self, in_memory_db) -> None:
        """Serialized timbre and embedding blobs are loaded into feature vectors."""
        from musesleuth.transition import load_track_features
        from musesleuth.db import create_schema, generate_metadata_id
        from musesleuth.timbre import serialize_array
        from tests.conftest import insert_dummy_track

        create_schema(in_memory_db)
        mid = generate_metadata_id()
        insert_dummy_track(in_memory_db, mid)

        in_memory_db.execute(
            "INSERT INTO timbre_features (metadata_id, mfcc_mean) VALUES (?, ?)",
            (mid, serialize_array([0.1, 0.2, 0.3])),
        )
        in_memory_db.execute(
            "INSERT INTO embeddings (metadata_id, model, scope, dim, vector) VALUES (?, 'mfcc_stat', 'global', 3, ?)",
            (mid, serialize_array([0.4, 0.5, 0.6])),
        )
        in_memory_db.commit()

        feat = load_track_features(in_memory_db, mid)
        assert feat is not None
        assert feat.timbre_vec is not None
        assert feat.embedding_vec is not None
        assert len(feat.timbre_vec) == 3
        assert len(feat.embedding_vec) == 3

    @pytest.mark.integration
    def test_load_features_missing_track(self, in_memory_db) -> None:
        """Unknown metadata_id returns None."""
        from musesleuth.transition import load_track_features
        from musesleuth.db import create_schema

        create_schema(in_memory_db)
        feat = load_track_features(in_memory_db, "nonexistent")
        assert feat is None
