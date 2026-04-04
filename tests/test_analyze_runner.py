"""TDD tests for analyze stage runner -- written before implementation."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from musesleuth.db import create_schema, generate_metadata_id
from musesleuth.analyze_runner import run_analyze_for_track, AnalyzeStageResult
from musesleuth.sidecar import SidecarData, write_sidecar, read_sidecar
from musesleuth.bpm_analyzer import BpmResult, KeyResult, AnalysisResult, EnergyResult
from musesleuth.fingerprint import FingerprintResult


def _seed_track(conn: sqlite3.Connection, metadata_id: str, full_path: str) -> None:
    conn.execute(
        "INSERT INTO tracks (metadata_id, file_path, filename, full_path) VALUES (?, ?, ?, ?)",
        (metadata_id, str(Path(full_path).parent), Path(full_path).name, full_path),
    )
    conn.execute(
        "INSERT INTO jobs (metadata_id, stage, status) VALUES (?, 'analyze', 'pending')",
        (metadata_id,),
    )
    conn.commit()


def _make_fake_mp3(path: Path) -> Path:
    frame = b"\xff\xfb\x90\x00" + b"\x00" * 413
    path.write_bytes(frame * 10)
    return path


@pytest.fixture
def db(in_memory_db: sqlite3.Connection) -> sqlite3.Connection:
    create_schema(in_memory_db)
    return in_memory_db


class TestRunAnalyzeForTrack:
    """Tests for the analyze stage orchestration."""

    @pytest.mark.integration
    def test_returns_result(self, db: sqlite3.Connection, tmp_path: Path) -> None:
        mid = generate_metadata_id()
        audio = _make_fake_mp3(tmp_path / "song.mp3")
        _seed_track(db, mid, str(audio))

        with _mock_analyzers():
            result = run_analyze_for_track(db, mid, str(audio))

        assert isinstance(result, AnalyzeStageResult)
        assert result.success is True

    @pytest.mark.integration
    def test_stores_musical_features(self, db: sqlite3.Connection, tmp_path: Path) -> None:
        mid = generate_metadata_id()
        audio = _make_fake_mp3(tmp_path / "song.mp3")
        _seed_track(db, mid, str(audio))

        with _mock_analyzers():
            run_analyze_for_track(db, mid, str(audio))

        row = db.execute(
            "SELECT bpm_aubio, bpm_librosa, bpm_final, bpm_confidence, "
            "bpm_disagreement, key_name, key_mode FROM musical_features WHERE metadata_id = ?",
            (mid,),
        ).fetchone()
        assert row is not None
        assert row["bpm_aubio"] == 128.0
        assert row["bpm_librosa"] == 129.0
        assert row["bpm_final"] is not None
        assert row["bpm_disagreement"] == 0
        assert row["key_name"] == "A"
        assert row["key_mode"] == "minor"

    @pytest.mark.integration
    def test_stores_hash_in_sidecar_table(self, db: sqlite3.Connection, tmp_path: Path) -> None:
        mid = generate_metadata_id()
        audio = _make_fake_mp3(tmp_path / "song.mp3")
        _seed_track(db, mid, str(audio))

        with _mock_analyzers():
            run_analyze_for_track(db, mid, str(audio))

        row = db.execute(
            "SELECT hash_partial, hash_full, fingerprint FROM track_sidecars WHERE metadata_id = ?",
            (mid,),
        ).fetchone()
        assert row is not None
        assert row["hash_partial"] is not None
        assert row["hash_full"] is not None
        assert row["fingerprint"] == "AQABz0mock..."

    @pytest.mark.integration
    def test_updates_sidecar_file(self, db: sqlite3.Connection, tmp_path: Path) -> None:
        mid = generate_metadata_id()
        audio = _make_fake_mp3(tmp_path / "song.mp3")
        _seed_track(db, mid, str(audio))

        sc_data = SidecarData(metadata_id=mid, path=str(audio), size=0, mtime=0.0)
        write_sidecar(audio, sc_data)

        with _mock_analyzers():
            run_analyze_for_track(db, mid, str(audio))

        updated = read_sidecar(audio)
        assert updated is not None
        assert updated.hash_partial is not None
        assert updated.hash_full is not None
        assert updated.fingerprint == "AQABz0mock..."

    @pytest.mark.integration
    def test_handles_missing_file(self, db: sqlite3.Connection, tmp_path: Path) -> None:
        mid = generate_metadata_id()
        missing = str(tmp_path / "gone.mp3")
        _seed_track(db, mid, missing)

        result = run_analyze_for_track(db, mid, missing)
        assert result.success is False
        assert result.error is not None

    @pytest.mark.integration
    def test_flags_bpm_disagreement(self, db: sqlite3.Connection, tmp_path: Path) -> None:
        mid = generate_metadata_id()
        audio = _make_fake_mp3(tmp_path / "song.mp3")
        _seed_track(db, mid, str(audio))

        with _mock_analyzers(aubio_bpm=128.0, librosa_bpm=90.0):
            run_analyze_for_track(db, mid, str(audio))

        row = db.execute(
            "SELECT bpm_disagreement FROM musical_features WHERE metadata_id = ?",
            (mid,),
        ).fetchone()
        assert row is not None
        assert row["bpm_disagreement"] == 1


class _mock_analyzers:
    """Context manager that mocks all analyzer functions with sensible defaults."""

    def __init__(self, aubio_bpm=128.0, librosa_bpm=129.0, key="A", mode="minor"):
        self.aubio_bpm = aubio_bpm
        self.librosa_bpm = librosa_bpm
        self.key = key
        self.mode = mode

    def __enter__(self):
        self._p1 = patch("musesleuth.bpm_analyzer._aubio_tempo", return_value=self.aubio_bpm)
        self._p2 = patch("musesleuth.bpm_analyzer._librosa_tempo", return_value=self.librosa_bpm)
        self._p3 = patch("musesleuth.bpm_analyzer._librosa_key", return_value=(self.key, self.mode))
        self._p4 = patch("musesleuth.fingerprint._invoke_fpcalc",
                         return_value={"duration": 214, "fingerprint": "AQABz0mock..."})
        self._p5 = patch("musesleuth.bpm_analyzer._librosa_energy",
                         return_value=EnergyResult(energy=0.45, dynamic_range=6.2, onset_density=3.1, peak_rms=0.12))
        self._p1.start()
        self._p2.start()
        self._p3.start()
        self._p4.start()
        self._p5.start()
        return self

    def __exit__(self, *args):
        self._p1.stop()
        self._p2.stop()
        self._p3.stop()
        self._p4.stop()
        self._p5.stop()
