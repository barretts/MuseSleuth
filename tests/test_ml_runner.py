"""Tests for ML classification stage runner."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from musesleuth.db import create_schema, generate_metadata_id
from musesleuth.ml_runner import (
    run_ml_classify_for_track,
    MLClassificationResult,
    _classify_genre,
    _detect_mood,
    _compute_danceability,
    _compute_acousticness,
    _detect_vocal_type,
    _predict_era,
    _categorize_tempo,
    _categorize_energy,
    _assess_quality,
)


def _seed_track_with_features(conn: sqlite3.Connection, metadata_id: str, full_path: str) -> None:
    conn.execute(
        "INSERT INTO tracks (metadata_id, file_path, filename, full_path) VALUES (?, ?, ?, ?)",
        (metadata_id, str(Path(full_path).parent), Path(full_path).name, full_path),
    )
    conn.execute(
        """INSERT INTO musical_features 
           (metadata_id, bpm_final, energy, dynamic_range, onset_density, peak_rms, key_name, key_mode)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (metadata_id, 128.0, 0.75, 8.5, 4.2, 0.35, "C", "major"),
    )
    conn.execute(
        "INSERT INTO jobs (metadata_id, stage, status) VALUES (?, 'ml_classify', 'pending')",
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


class TestRunMlClassifyForTrack:
    """Tests for the ML classification stage orchestration."""

    @pytest.mark.integration
    def test_returns_result(self, db: sqlite3.Connection, tmp_path: Path) -> None:
        mid = generate_metadata_id()
        audio = _make_fake_mp3(tmp_path / "song.mp3")
        _seed_track_with_features(db, mid, str(audio))

        with _mock_spectral_analysis():
            result = run_ml_classify_for_track(db, mid, str(audio))

        assert isinstance(result, MLClassificationResult)
        assert result.success is True

    @pytest.mark.integration
    def test_stores_ml_features(self, db: sqlite3.Connection, tmp_path: Path) -> None:
        mid = generate_metadata_id()
        audio = _make_fake_mp3(tmp_path / "song.mp3")
        _seed_track_with_features(db, mid, str(audio))

        with _mock_spectral_analysis():
            run_ml_classify_for_track(db, mid, str(audio))

        row = db.execute(
            """SELECT genre_primary, danceability, acousticness, vocal_type, tempo_category, energy_category
               FROM ml_features WHERE metadata_id = ?""",
            (mid,),
        ).fetchone()
        assert row is not None
        assert row["genre_primary"] is not None
        assert 0.0 <= row["danceability"] <= 1.0
        assert 0.0 <= row["acousticness"] <= 1.0
        assert row["vocal_type"] in ("vocal", "instrumental", "spoken")
        assert row["tempo_category"] is not None
        assert row["energy_category"] is not None

    @pytest.mark.integration
    def test_stores_mood_tags(self, db: sqlite3.Connection, tmp_path: Path) -> None:
        mid = generate_metadata_id()
        audio = _make_fake_mp3(tmp_path / "song.mp3")
        _seed_track_with_features(db, mid, str(audio))

        with _mock_spectral_analysis():
            run_ml_classify_for_track(db, mid, str(audio))

        row = db.execute(
            "SELECT mood_tags FROM ml_features WHERE metadata_id = ?",
            (mid,),
        ).fetchone()
        assert row is not None
        moods = json.loads(row["mood_tags"])
        assert isinstance(moods, list)
        assert len(moods) > 0

    @pytest.mark.integration
    def test_handles_missing_file(self, db: sqlite3.Connection, tmp_path: Path) -> None:
        mid = generate_metadata_id()
        missing = str(tmp_path / "gone.mp3")
        _seed_track_with_features(db, mid, missing)

        result = run_ml_classify_for_track(db, mid, missing)
        assert result.success is False
        assert result.error is not None

    @pytest.mark.integration
    def test_handles_missing_musical_features(self, db: sqlite3.Connection, tmp_path: Path) -> None:
        mid = generate_metadata_id()
        audio = _make_fake_mp3(tmp_path / "song.mp3")
        db.execute(
            "INSERT INTO tracks (metadata_id, file_path, filename, full_path) VALUES (?, ?, ?, ?)",
            (mid, str(tmp_path), "song.mp3", str(audio)),
        )
        db.commit()

        result = run_ml_classify_for_track(db, mid, str(audio))
        assert result.success is False
        assert "No musical features" in result.error

    @pytest.mark.integration
    def test_stores_quality_assessment(self, db: sqlite3.Connection, tmp_path: Path) -> None:
        mid = generate_metadata_id()
        audio = _make_fake_mp3(tmp_path / "song.mp3")
        _seed_track_with_features(db, mid, str(audio))

        with _mock_spectral_analysis():
            run_ml_classify_for_track(db, mid, str(audio))

        row = db.execute(
            "SELECT quality_score, quality_issues FROM ml_features WHERE metadata_id = ?",
            (mid,),
        ).fetchone()
        assert row is not None
        assert 0.0 <= row["quality_score"] <= 1.0
        issues = json.loads(row["quality_issues"])
        assert isinstance(issues, list)


class TestGenreClassification:
    """Tests for genre classification logic."""

    def test_dubstep_classification(self):
        result = _classify_genre(70, 0.3, {"spectral_flatness": 0.2, "spectral_centroid": 3000, "zero_crossing_rate": 0.08})
        assert result["primary"] == "dubstep"

    def test_drumnbass_classification(self):
        result = _classify_genre(170, 0.3, {"spectral_flatness": 0.1, "spectral_centroid": 4000, "zero_crossing_rate": 0.1})
        assert result["primary"] == "drumnbass"

    def test_techno_classification(self):
        result = _classify_genre(130, 0.2, {"spectral_flatness": 0.1, "spectral_centroid": 5000, "zero_crossing_rate": 0.1})
        assert result["primary"] == "techno"


class TestMoodDetection:
    """Tests for mood detection logic."""

    def test_energetic_mood(self):
        moods = _detect_mood(140, 0.85, 10.0, {})
        assert "energetic" in moods

    def test_calm_mood(self):
        moods = _detect_mood(70, 0.1, 3.0, {})
        assert "calm" in moods

    def test_bright_mood(self):
        moods = _detect_mood(120, 0.5, 6.0, {"spectral_centroid": 6000})
        assert "bright" in moods


class TestDanceability:
    """Tests for danceability computation."""

    def test_danceability_in_range(self):
        score = _compute_danceability(125, 0.8, 5.0, {"spectral_flatness": 0.1})
        assert 0.0 <= score <= 1.0


class TestAcousticness:
    """Tests for acousticness computation."""

    def test_acousticness_in_range(self):
        score = _compute_acousticness({"spectral_flatness": 0.5, "spectral_centroid": 1500, "zero_crossing_rate": 0.03}, 8.0)
        assert 0.0 <= score <= 1.0


class TestVocalDetection:
    """Tests for vocal type detection."""

    def test_instrumental_detection(self):
        result = _detect_vocal_type("/fake/path.mp3", {"spectral_flatness": 0.5, "spectral_centroid": 2000, "zero_crossing_rate": 0.05})
        assert result["type"] == "instrumental"

    def test_vocal_detection(self):
        result = _detect_vocal_type("/fake/path.mp3", {"spectral_flatness": 0.1, "spectral_centroid": 3000, "zero_crossing_rate": 0.08})
        assert result["type"] == "vocal"


class TestEraPrediction:
    """Tests for era prediction."""

    def test_modern_era_prediction(self):
        result = _predict_era(128, {"spectral_centroid": 6500}, 0.8)
        assert result["era"] in ("2020s", "2010s", "2000s", "1990s", "unknown")


class TestTempoCategorization:
    """Tests for tempo categorization."""

    def test_slow_tempo(self):
        assert _categorize_tempo(65) == "slow"

    def test_fast_tempo(self):
        assert _categorize_tempo(160) == "fast"


class TestEnergyCategorization:
    """Tests for energy categorization."""

    def test_low_energy(self):
        assert _categorize_energy(0.25) == "low"

    def test_high_energy(self):
        assert _categorize_energy(0.7) == "high"


class TestQualityAssessment:
    """Tests for audio quality assessment."""

    def test_quality_with_issues(self):
        result = _assess_quality({"spectral_flatness": 0.7, "spectral_centroid": 400, "zero_crossing_rate": 0.3}, 2.0, 0.98)
        assert result["score"] < 0.8
        assert len(result["issues"]) > 0


class _mock_spectral_analysis:
    """Context manager that mocks spectral analysis."""

    def __init__(self):
        pass

    def __enter__(self):
        self._p1 = patch(
            "musesleuth.ml_runner._analyze_spectral_features",
            return_value={
                "spectral_centroid": 3000.0,
                "spectral_bandwidth": 2000.0,
                "spectral_rolloff": 5000.0,
                "spectral_flatness": 0.15,
                "zero_crossing_rate": 0.08,
                "tempo_librosa": 128.0,
            },
        )
        self._p1.start()
        return self

    def __exit__(self, *args):
        self._p1.stop()
