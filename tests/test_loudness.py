"""TDD tests for loudness analysis module (Phase 1) -- written before implementation."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from musesleuth.db import create_schema, generate_metadata_id
from tests.conftest import insert_dummy_track


class TestComputeLoudness:
    """Tests for core loudness measurement functions."""

    @pytest.mark.unit
    def test_compute_loudness_basic(self, synthetic_audio: tuple) -> None:
        """Sine wave returns finite LUFS values."""
        from musesleuth.loudness import compute_loudness

        _samples, _sr, wav_path = synthetic_audio
        result = compute_loudness(str(wav_path))

        assert result is not None
        assert result.lufs_integrated is not None
        assert isinstance(result.lufs_integrated, float)
        assert result.lufs_integrated < 0  # LUFS is always negative for real signals
        assert result.lra is not None
        assert result.true_peak_dbtp is not None
        assert result.crest_factor is not None

    @pytest.mark.unit
    def test_lufs_intro_outro_segments(self, tmp_path: Path) -> None:
        """Verify intro/outro short-term LUFS are computed for asymmetric audio."""
        from musesleuth.loudness import compute_loudness
        import struct
        import math

        # Create a 10s WAV with volume ramp: quiet intro -> loud middle -> quiet outro
        sr = 22050
        n_samples = sr * 10
        samples = []
        for i in range(n_samples):
            t = i / sr
            # Amplitude envelope: ramp up 0-3s, full 3-7s, ramp down 7-10s
            if t < 3.0:
                amp = t / 3.0
            elif t < 7.0:
                amp = 1.0
            else:
                amp = (10.0 - t) / 3.0
            samples.append(amp * math.sin(2.0 * math.pi * 440.0 * i / sr))

        wav_path = tmp_path / "ramp_audio.wav"
        max_int16 = 32767
        raw_data = struct.pack(f"<{n_samples}h", *(int(s * max_int16) for s in samples))
        data_size = n_samples * 2
        with wav_path.open("wb") as f:
            f.write(b"RIFF")
            f.write(struct.pack("<I", 36 + data_size))
            f.write(b"WAVE")
            f.write(b"fmt ")
            f.write(struct.pack("<I", 16))
            f.write(struct.pack("<H", 1))
            f.write(struct.pack("<H", 1))
            f.write(struct.pack("<I", sr))
            f.write(struct.pack("<I", sr * 2))
            f.write(struct.pack("<H", 2))
            f.write(struct.pack("<H", 16))
            f.write(b"data")
            f.write(struct.pack("<I", data_size))
            f.write(raw_data)

        result = compute_loudness(str(wav_path), intro_s=3.0, outro_s=3.0)

        assert result.lufs_short_intro is not None
        assert result.lufs_short_outro is not None
        assert result.lufs_integrated is not None
        # Intro and outro are quieter than integrated (which includes the loud middle)
        assert result.lufs_short_intro < result.lufs_integrated or \
               result.lufs_short_outro < result.lufs_integrated

    @pytest.mark.unit
    def test_compute_loudness_missing_file(self) -> None:
        """Non-existent file returns None result gracefully."""
        from musesleuth.loudness import compute_loudness

        result = compute_loudness("/nonexistent/path.wav")
        assert result is None

    @pytest.mark.unit
    def test_loudness_result_fields(self, synthetic_audio: tuple) -> None:
        """LoudnessResult dataclass has all expected fields."""
        from musesleuth.loudness import compute_loudness, LoudnessResult

        _samples, _sr, wav_path = synthetic_audio
        result = compute_loudness(str(wav_path))

        assert isinstance(result, LoudnessResult)
        expected_fields = {
            "lufs_integrated", "lufs_short_intro", "lufs_short_outro",
            "lra", "true_peak_dbtp", "crest_factor",
        }
        actual_fields = set(vars(result).keys())
        for field in expected_fields:
            assert field in actual_fields, f"Missing field: {field}"


class TestDemoDetection:
    """Tests for demo/unmastered track detection heuristic."""

    @pytest.mark.unit
    def test_is_likely_demo_low_lufs_high_lra(self) -> None:
        """A -22 LUFS track with LRA > 12 is flagged as likely demo."""
        from musesleuth.loudness import is_likely_demo

        assert is_likely_demo(lufs_i=-22.0, lra=14.0) is True

    @pytest.mark.unit
    def test_is_not_demo_normal_mastered(self) -> None:
        """A -8 LUFS track with LRA 6 is NOT flagged as demo."""
        from musesleuth.loudness import is_likely_demo

        assert is_likely_demo(lufs_i=-8.0, lra=6.0) is False

    @pytest.mark.unit
    def test_is_likely_demo_none_values(self) -> None:
        """None values return False (can't determine)."""
        from musesleuth.loudness import is_likely_demo

        assert is_likely_demo(lufs_i=None, lra=None) is False
        assert is_likely_demo(lufs_i=-22.0, lra=None) is False
        assert is_likely_demo(lufs_i=None, lra=14.0) is False


class TestStoreLoudnessFeatures:
    """Tests for DB persistence of loudness features."""

    @pytest.mark.integration
    def test_store_loudness_features_roundtrip(
        self, in_memory_db: sqlite3.Connection, synthetic_audio: tuple
    ) -> None:
        """Loudness features are stored and retrievable from DB."""
        from musesleuth.loudness import run_loudness_for_track

        create_schema(in_memory_db)
        mid = generate_metadata_id()
        insert_dummy_track(in_memory_db, mid)

        _samples, _sr, wav_path = synthetic_audio
        success = run_loudness_for_track(in_memory_db, mid, str(wav_path))
        assert success is True

        row = in_memory_db.execute(
            "SELECT * FROM loudness_features WHERE metadata_id = ?", (mid,)
        ).fetchone()
        assert row is not None
        assert row["lufs_integrated"] is not None
        assert isinstance(row["lufs_integrated"], float)
        assert row["lra"] is not None
        assert row["true_peak_dbtp"] is not None
        assert row["crest_factor"] is not None
        assert row["analyzed_at"] is not None

    @pytest.mark.integration
    def test_store_loudness_replaces_on_rerun(
        self, in_memory_db: sqlite3.Connection, synthetic_audio: tuple
    ) -> None:
        """Running loudness twice for the same track replaces the row."""
        from musesleuth.loudness import run_loudness_for_track

        create_schema(in_memory_db)
        mid = generate_metadata_id()
        insert_dummy_track(in_memory_db, mid)

        _samples, _sr, wav_path = synthetic_audio
        run_loudness_for_track(in_memory_db, mid, str(wav_path))
        run_loudness_for_track(in_memory_db, mid, str(wav_path))

        count = in_memory_db.execute(
            "SELECT COUNT(*) FROM loudness_features WHERE metadata_id = ?", (mid,)
        ).fetchone()[0]
        assert count == 1

    @pytest.mark.integration
    def test_store_loudness_missing_file(
        self, in_memory_db: sqlite3.Connection
    ) -> None:
        """Missing file returns False, no row stored."""
        from musesleuth.loudness import run_loudness_for_track

        create_schema(in_memory_db)
        mid = generate_metadata_id()
        insert_dummy_track(in_memory_db, mid)

        success = run_loudness_for_track(in_memory_db, mid, "/nonexistent/file.wav")
        assert success is False

        row = in_memory_db.execute(
            "SELECT * FROM loudness_features WHERE metadata_id = ?", (mid,)
        ).fetchone()
        assert row is None
