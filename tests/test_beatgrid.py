"""TDD tests for beat grid & downbeat tracking (Phase 5) -- written before implementation."""
from __future__ import annotations

import json
import math
import sqlite3
import struct
from pathlib import Path

import pytest

from musesleuth.db import create_schema, generate_metadata_id
from tests.conftest import insert_dummy_track


def _make_click_track(tmp_path: Path, bpm: float = 120.0, duration: float = 10.0) -> Path:
    """Create a WAV with periodic clicks at the given BPM for beat detection."""
    sr = 22050
    n_samples = int(sr * duration)
    beat_interval = 60.0 / bpm
    click_len = int(0.005 * sr)  # 5ms click

    samples = [0.0] * n_samples
    t = 0.0
    while t < duration:
        idx = int(t * sr)
        for j in range(click_len):
            if idx + j < n_samples:
                # Short decaying click
                samples[idx + j] = 0.8 * math.exp(-j / (click_len * 0.3))
        t += beat_interval

    wav_path = tmp_path / "click_track.wav"
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
    return wav_path


class TestDetectBeats:
    """Tests for beat and downbeat detection."""

    @pytest.mark.unit
    def test_detect_beats_returns_result(self, synthetic_audio: tuple) -> None:
        """Basic sine wave returns a BeatGridResult."""
        from musesleuth.beatgrid import detect_beats, BeatGridResult

        _samples, _sr, wav_path = synthetic_audio
        result = detect_beats(str(wav_path))

        assert isinstance(result, BeatGridResult)
        assert result.beats is not None
        assert isinstance(result.beats, list)

    @pytest.mark.unit
    def test_click_track_beats_near_bpm(self, tmp_path: Path) -> None:
        """Click track at 120 BPM produces beats roughly every 0.5s."""
        from musesleuth.beatgrid import detect_beats

        wav_path = _make_click_track(tmp_path, bpm=120.0, duration=10.0)
        result = detect_beats(str(wav_path))

        assert result is not None
        assert len(result.beats) >= 5  # at least some beats detected

        # Check median inter-beat interval is roughly 0.5s (120 BPM)
        if len(result.beats) >= 3:
            intervals = [
                result.beats[i + 1] - result.beats[i]
                for i in range(len(result.beats) - 1)
            ]
            median_ibi = sorted(intervals)[len(intervals) // 2]
            assert 0.3 < median_ibi < 0.8, f"Median IBI {median_ibi} too far from 0.5"

    @pytest.mark.unit
    def test_detect_beats_missing_file(self) -> None:
        """Non-existent file returns None."""
        from musesleuth.beatgrid import detect_beats

        result = detect_beats("/nonexistent/path.wav")
        assert result is None

    @pytest.mark.unit
    def test_beat_grid_result_fields(self, synthetic_audio: tuple) -> None:
        """BeatGridResult has all expected fields."""
        from musesleuth.beatgrid import detect_beats, BeatGridResult

        _samples, _sr, wav_path = synthetic_audio
        result = detect_beats(str(wav_path))

        assert hasattr(result, "beats")
        assert hasattr(result, "downbeats")
        assert hasattr(result, "phrase_boundaries")
        assert hasattr(result, "time_signature")
        assert hasattr(result, "confidence")


class TestPhraseBoundaries:
    """Tests for phrase boundary derivation from beats."""

    @pytest.mark.unit
    def test_phrase_boundaries_from_beats(self) -> None:
        """Phrase boundaries are derived every N bars from beat positions."""
        from musesleuth.beatgrid import derive_phrase_boundaries

        # 20 beats at 120 BPM = 10s
        beats = [i * 0.5 for i in range(20)]
        phrases = derive_phrase_boundaries(beats, beats_per_bar=4, bars_per_phrase=4)

        # 20 beats / 4 beats_per_bar = 5 bars, / 4 bars_per_phrase = 1+ phrases
        assert len(phrases) >= 1
        # First phrase boundary at beat 0
        assert phrases[0] == pytest.approx(0.0)

    @pytest.mark.unit
    def test_phrase_boundaries_empty_beats(self) -> None:
        """Empty beats list produces empty phrase boundaries."""
        from musesleuth.beatgrid import derive_phrase_boundaries

        assert derive_phrase_boundaries([], beats_per_bar=4, bars_per_phrase=4) == []


class TestStoreBeatGrid:
    """Tests for DB persistence of beat grid data."""

    @pytest.mark.integration
    def test_store_beat_grid_roundtrip(
        self, in_memory_db: sqlite3.Connection, synthetic_audio: tuple
    ) -> None:
        """Beat grid data stored as JSON and retrievable."""
        from musesleuth.beatgrid import run_beatgrid_for_track

        create_schema(in_memory_db)
        mid = generate_metadata_id()
        insert_dummy_track(in_memory_db, mid)

        _samples, _sr, wav_path = synthetic_audio
        success = run_beatgrid_for_track(in_memory_db, mid, str(wav_path))
        assert success is True

        row = in_memory_db.execute(
            "SELECT * FROM beat_grids WHERE metadata_id = ?", (mid,)
        ).fetchone()
        assert row is not None
        assert row["beats_json"] is not None
        assert row["analyzed_at"] is not None

        # JSON round-trip
        beats = json.loads(row["beats_json"])
        assert isinstance(beats, list)

    @pytest.mark.integration
    def test_store_replaces_on_rerun(
        self, in_memory_db: sqlite3.Connection, synthetic_audio: tuple
    ) -> None:
        """Running beatgrid twice replaces existing row."""
        from musesleuth.beatgrid import run_beatgrid_for_track

        create_schema(in_memory_db)
        mid = generate_metadata_id()
        insert_dummy_track(in_memory_db, mid)

        _samples, _sr, wav_path = synthetic_audio
        run_beatgrid_for_track(in_memory_db, mid, str(wav_path))
        run_beatgrid_for_track(in_memory_db, mid, str(wav_path))

        count = in_memory_db.execute(
            "SELECT COUNT(*) FROM beat_grids WHERE metadata_id = ?", (mid,)
        ).fetchone()[0]
        assert count == 1

    @pytest.mark.integration
    def test_store_missing_file(self, in_memory_db: sqlite3.Connection) -> None:
        """Missing file returns False."""
        from musesleuth.beatgrid import run_beatgrid_for_track

        create_schema(in_memory_db)
        mid = generate_metadata_id()
        insert_dummy_track(in_memory_db, mid)

        success = run_beatgrid_for_track(in_memory_db, mid, "/nonexistent/file.wav")
        assert success is False
