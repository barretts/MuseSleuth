"""TDD tests for timbre & spectral feature extraction (Phase 2) -- written before implementation."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from musesleuth.db import create_schema, generate_metadata_id
from tests.conftest import insert_dummy_track


class TestExtractTimbre:
    """Tests for core timbre feature extraction."""

    @pytest.mark.unit
    def test_extract_timbre_features(self, synthetic_audio: tuple) -> None:
        """Sine wave returns 20 MFCC coefficients + aggregate stats."""
        from musesleuth.timbre import extract_timbre, TimbreResult

        _samples, _sr, wav_path = synthetic_audio
        result = extract_timbre(str(wav_path))

        assert isinstance(result, TimbreResult)
        assert result.mfcc_mean is not None
        assert len(result.mfcc_mean) == 20
        assert result.mfcc_var is not None
        assert len(result.mfcc_var) == 20
        assert result.centroid_mean is not None
        assert isinstance(result.centroid_mean, float)
        assert result.rolloff_mean is not None
        assert result.bandwidth_mean is not None
        assert result.flatness_mean is not None
        assert result.zcr_mean is not None

    @pytest.mark.unit
    def test_extract_timbre_missing_file(self) -> None:
        """Non-existent file returns None."""
        from musesleuth.timbre import extract_timbre

        result = extract_timbre("/nonexistent/path.wav")
        assert result is None

    @pytest.mark.unit
    def test_mfcc_values_are_finite(self, synthetic_audio: tuple) -> None:
        """All MFCC coefficients are finite floats."""
        from musesleuth.timbre import extract_timbre
        import math

        _samples, _sr, wav_path = synthetic_audio
        result = extract_timbre(str(wav_path))

        for val in result.mfcc_mean:
            assert math.isfinite(val), f"Non-finite MFCC mean: {val}"
        for val in result.mfcc_var:
            assert math.isfinite(val), f"Non-finite MFCC var: {val}"


class TestTimbreSimilarity:
    """Tests for timbre cosine distance."""

    @pytest.mark.unit
    def test_identical_inputs_zero_distance(self, synthetic_audio: tuple) -> None:
        """Two identical inputs have distance ~0."""
        from musesleuth.timbre import extract_timbre, timbre_cosine_distance

        _samples, _sr, wav_path = synthetic_audio
        a = extract_timbre(str(wav_path))
        b = extract_timbre(str(wav_path))

        dist = timbre_cosine_distance(a, b)
        assert dist is not None
        assert abs(dist) < 1e-5  # essentially zero

    @pytest.mark.unit
    def test_distance_is_bounded(self, synthetic_audio: tuple) -> None:
        """Cosine distance is in [0, 2] range."""
        from musesleuth.timbre import extract_timbre, timbre_cosine_distance

        _samples, _sr, wav_path = synthetic_audio
        a = extract_timbre(str(wav_path))
        b = extract_timbre(str(wav_path))

        dist = timbre_cosine_distance(a, b)
        assert 0.0 <= dist <= 2.0

    @pytest.mark.unit
    def test_distance_none_on_missing_result(self) -> None:
        """Returns None if either result is None."""
        from musesleuth.timbre import timbre_cosine_distance

        assert timbre_cosine_distance(None, None) is None


class TestStoreTimbreFeatures:
    """Tests for DB persistence of timbre features."""

    @pytest.mark.integration
    def test_store_timbre_roundtrip(
        self, in_memory_db: sqlite3.Connection, synthetic_audio: tuple
    ) -> None:
        """Timbre features are stored and retrievable, BLOB deserialization works."""
        from musesleuth.timbre import run_timbre_for_track, deserialize_array

        create_schema(in_memory_db)
        mid = generate_metadata_id()
        insert_dummy_track(in_memory_db, mid)

        _samples, _sr, wav_path = synthetic_audio
        success = run_timbre_for_track(in_memory_db, mid, str(wav_path))
        assert success is True

        row = in_memory_db.execute(
            "SELECT * FROM timbre_features WHERE metadata_id = ?", (mid,)
        ).fetchone()
        assert row is not None
        assert row["centroid_mean"] is not None
        assert row["rolloff_mean"] is not None
        assert row["bandwidth_mean"] is not None
        assert row["flatness_mean"] is not None
        assert row["zcr_mean"] is not None
        assert row["analyzed_at"] is not None

        # Verify BLOB round-trip
        mfcc_mean = deserialize_array(row["mfcc_mean"])
        assert mfcc_mean is not None
        assert len(mfcc_mean) == 20

        mfcc_var = deserialize_array(row["mfcc_var"])
        assert mfcc_var is not None
        assert len(mfcc_var) == 20

    @pytest.mark.integration
    def test_store_timbre_replaces_on_rerun(
        self, in_memory_db: sqlite3.Connection, synthetic_audio: tuple
    ) -> None:
        """Running timbre twice for the same track replaces the row."""
        from musesleuth.timbre import run_timbre_for_track

        create_schema(in_memory_db)
        mid = generate_metadata_id()
        insert_dummy_track(in_memory_db, mid)

        _samples, _sr, wav_path = synthetic_audio
        run_timbre_for_track(in_memory_db, mid, str(wav_path))
        run_timbre_for_track(in_memory_db, mid, str(wav_path))

        count = in_memory_db.execute(
            "SELECT COUNT(*) FROM timbre_features WHERE metadata_id = ?", (mid,)
        ).fetchone()[0]
        assert count == 1

    @pytest.mark.integration
    def test_store_timbre_missing_file(
        self, in_memory_db: sqlite3.Connection
    ) -> None:
        """Missing file returns False, no row stored."""
        from musesleuth.timbre import run_timbre_for_track

        create_schema(in_memory_db)
        mid = generate_metadata_id()
        insert_dummy_track(in_memory_db, mid)

        success = run_timbre_for_track(in_memory_db, mid, "/nonexistent/file.wav")
        assert success is False
