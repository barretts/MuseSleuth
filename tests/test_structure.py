"""TDD tests for structural segmentation & intro/outro detection (Phase 4) -- written before implementation."""
from __future__ import annotations

import math
import sqlite3
import struct
from pathlib import Path

import pytest

from musesleuth.db import create_schema, generate_metadata_id
from tests.conftest import insert_dummy_track


def _make_two_tone_wav(tmp_path: Path, duration: float = 10.0) -> Path:
    """Create a WAV with distinct halves: 440 Hz then 880 Hz."""
    sr = 22050
    n_samples = int(sr * duration)
    half = n_samples // 2
    samples = []
    for i in range(n_samples):
        freq = 440.0 if i < half else 880.0
        samples.append(math.sin(2.0 * math.pi * freq * i / sr))

    wav_path = tmp_path / "two_tone.wav"
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


class TestDetectBoundaries:
    """Tests for boundary detection via novelty."""

    @pytest.mark.unit
    def test_detect_boundaries_novelty(self, tmp_path: Path) -> None:
        """Synthetic audio with distinct halves produces at least 1 boundary."""
        from musesleuth.structure import detect_boundaries

        wav_path = _make_two_tone_wav(tmp_path)
        segments = detect_boundaries(str(wav_path))

        assert segments is not None
        assert len(segments) >= 1  # at least one boundary detected

    @pytest.mark.unit
    def test_segments_have_required_fields(self, tmp_path: Path) -> None:
        """Each segment has start_s, end_s, kind, confidence."""
        from musesleuth.structure import detect_boundaries, Segment

        wav_path = _make_two_tone_wav(tmp_path)
        segments = detect_boundaries(str(wav_path))

        for seg in segments:
            assert isinstance(seg, Segment)
            assert seg.start_s is not None
            assert seg.end_s is not None
            assert seg.end_s > seg.start_s
            assert seg.kind is not None

    @pytest.mark.unit
    def test_segments_cover_full_track(self, tmp_path: Path) -> None:
        """Segments start at ~0 and end at ~duration."""
        from musesleuth.structure import detect_boundaries

        wav_path = _make_two_tone_wav(tmp_path, duration=10.0)
        segments = detect_boundaries(str(wav_path))

        assert segments[0].start_s < 1.0  # starts near beginning
        assert segments[-1].end_s > 8.0   # ends near track end

    @pytest.mark.unit
    def test_missing_file_returns_none(self) -> None:
        """Non-existent file returns None."""
        from musesleuth.structure import detect_boundaries

        result = detect_boundaries("/nonexistent/path.wav")
        assert result is None


class TestLabelIntroOutro:
    """Tests for intro/outro labeling heuristic."""

    @pytest.mark.unit
    def test_label_intro_outro(self, tmp_path: Path) -> None:
        """First and last segments labeled as intro and outro."""
        from musesleuth.structure import detect_boundaries, label_intro_outro

        wav_path = _make_two_tone_wav(tmp_path, duration=10.0)
        segments = detect_boundaries(str(wav_path))
        labeled = label_intro_outro(segments, duration_s=10.0)

        kinds = [s.kind for s in labeled]
        assert kinds[0] == "intro"
        assert kinds[-1] == "outro"


class TestIsRadioEdit:
    """Tests for radio-edit detection."""

    @pytest.mark.unit
    def test_is_radio_edit_short_intro(self) -> None:
        """Intro < 15s flagged as radio edit candidate."""
        from musesleuth.structure import Segment, is_radio_edit

        segments = [
            Segment(start_s=0.0, end_s=5.0, kind="intro"),
            Segment(start_s=5.0, end_s=150.0, kind="body"),
            Segment(start_s=150.0, end_s=180.0, kind="outro"),
        ]
        assert is_radio_edit(segments) is True

    @pytest.mark.unit
    def test_is_not_radio_edit_long_intro(self) -> None:
        """Intro >= 15s NOT flagged as radio edit."""
        from musesleuth.structure import Segment, is_radio_edit

        segments = [
            Segment(start_s=0.0, end_s=30.0, kind="intro"),
            Segment(start_s=30.0, end_s=300.0, kind="body"),
            Segment(start_s=300.0, end_s=360.0, kind="outro"),
        ]
        assert is_radio_edit(segments) is False

    @pytest.mark.unit
    def test_is_radio_edit_empty(self) -> None:
        """Empty segment list returns False."""
        from musesleuth.structure import is_radio_edit

        assert is_radio_edit([]) is False


class TestStoreStructure:
    """Tests for DB persistence of structure segments."""

    @pytest.mark.integration
    def test_store_structure_segments(
        self, in_memory_db: sqlite3.Connection, tmp_path: Path
    ) -> None:
        """Structure segments are stored and retrievable."""
        from musesleuth.structure import run_structure_for_track

        create_schema(in_memory_db)
        mid = generate_metadata_id()
        insert_dummy_track(in_memory_db, mid)

        wav_path = _make_two_tone_wav(tmp_path)
        success = run_structure_for_track(in_memory_db, mid, str(wav_path))
        assert success is True

        rows = in_memory_db.execute(
            "SELECT * FROM structure_segments WHERE metadata_id = ? ORDER BY segment_idx",
            (mid,),
        ).fetchall()
        assert len(rows) >= 1
        for row in rows:
            assert row["start_s"] is not None
            assert row["end_s"] is not None
            assert row["kind"] is not None
            assert row["analyzed_at"] is not None

    @pytest.mark.integration
    def test_store_replaces_on_rerun(
        self, in_memory_db: sqlite3.Connection, tmp_path: Path
    ) -> None:
        """Running structure twice doesn't duplicate segments."""
        from musesleuth.structure import run_structure_for_track

        create_schema(in_memory_db)
        mid = generate_metadata_id()
        insert_dummy_track(in_memory_db, mid)

        wav_path = _make_two_tone_wav(tmp_path)
        run_structure_for_track(in_memory_db, mid, str(wav_path))
        first_count = in_memory_db.execute(
            "SELECT COUNT(*) FROM structure_segments WHERE metadata_id = ?", (mid,)
        ).fetchone()[0]

        run_structure_for_track(in_memory_db, mid, str(wav_path))
        second_count = in_memory_db.execute(
            "SELECT COUNT(*) FROM structure_segments WHERE metadata_id = ?", (mid,)
        ).fetchone()[0]
        assert second_count == first_count

    @pytest.mark.integration
    def test_store_missing_file(self, in_memory_db: sqlite3.Connection) -> None:
        """Missing file returns False."""
        from musesleuth.structure import run_structure_for_track

        create_schema(in_memory_db)
        mid = generate_metadata_id()
        insert_dummy_track(in_memory_db, mid)

        success = run_structure_for_track(in_memory_db, mid, "/nonexistent/file.wav")
        assert success is False
