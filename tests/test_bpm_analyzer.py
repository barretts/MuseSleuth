"""TDD tests for BPM/key analysis with aubio+librosa cross-check -- written before implementation."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from musesleuth.bpm_analyzer import (
    BpmResult,
    KeyResult,
    AnalysisResult,
    analyze_bpm_aubio,
    analyze_bpm_librosa,
    analyze_key,
    cross_check_bpm,
    BPM_DISAGREEMENT_THRESHOLD,
)


class TestBpmResult:
    """Tests for BpmResult data class."""

    @pytest.mark.unit
    def test_basic_fields(self) -> None:
        r = BpmResult(bpm=128.0, confidence=0.9, source="aubio")
        assert r.bpm == 128.0
        assert r.confidence == 0.9
        assert r.source == "aubio"


class TestAnalyzeBpmAubio:
    """Tests for aubio BPM estimation (mocked)."""

    @pytest.mark.unit
    def test_returns_bpm_result(self, tmp_path) -> None:
        f = tmp_path / "song.wav"
        f.write_bytes(b"\x00" * 44100)
        with patch("musesleuth.bpm_analyzer._aubio_tempo") as mock:
            mock.return_value = 130.0
            result = analyze_bpm_aubio(str(f))
        assert isinstance(result, BpmResult)
        assert result.bpm == 130.0
        assert result.source == "aubio"

    @pytest.mark.unit
    def test_returns_none_bpm_on_failure(self, tmp_path) -> None:
        f = tmp_path / "bad.wav"
        f.write_bytes(b"\x00" * 100)
        with patch("musesleuth.bpm_analyzer._aubio_tempo") as mock:
            mock.side_effect = Exception("bad file")
            result = analyze_bpm_aubio(str(f))
        assert result.bpm is None
        assert result.error is not None


class TestAnalyzeBpmLibrosa:
    """Tests for librosa BPM estimation (mocked)."""

    @pytest.mark.unit
    def test_returns_bpm_result(self, tmp_path) -> None:
        f = tmp_path / "song.wav"
        f.write_bytes(b"\x00" * 44100)
        with patch("musesleuth.bpm_analyzer._librosa_tempo") as mock:
            mock.return_value = 128.5
            result = analyze_bpm_librosa(str(f))
        assert isinstance(result, BpmResult)
        assert result.bpm == 128.5
        assert result.source == "librosa"

    @pytest.mark.unit
    def test_returns_none_bpm_on_failure(self, tmp_path) -> None:
        f = tmp_path / "bad.wav"
        f.write_bytes(b"\x00" * 100)
        with patch("musesleuth.bpm_analyzer._librosa_tempo") as mock:
            mock.side_effect = Exception("bad file")
            result = analyze_bpm_librosa(str(f))
        assert result.bpm is None
        assert result.error is not None


class TestCrossCheckBpm:
    """Tests for BPM cross-check logic between aubio and librosa."""

    @pytest.mark.unit
    def test_agreement_averages(self) -> None:
        a = BpmResult(bpm=128.0, confidence=0.9, source="aubio")
        b = BpmResult(bpm=129.0, confidence=0.85, source="librosa")
        result = cross_check_bpm(a, b)
        assert result.bpm_final is not None
        assert 128.0 <= result.bpm_final <= 129.0
        assert result.disagreement is False

    @pytest.mark.unit
    def test_disagreement_flagged(self) -> None:
        a = BpmResult(bpm=128.0, confidence=0.9, source="aubio")
        b = BpmResult(bpm=90.0, confidence=0.85, source="librosa")
        result = cross_check_bpm(a, b)
        assert result.disagreement is True

    @pytest.mark.unit
    def test_one_none_uses_other(self) -> None:
        a = BpmResult(bpm=128.0, confidence=0.9, source="aubio")
        b = BpmResult(bpm=None, confidence=0.0, source="librosa", error="failed")
        result = cross_check_bpm(a, b)
        assert result.bpm_final == 128.0
        assert result.disagreement is False

    @pytest.mark.unit
    def test_both_none_returns_none(self) -> None:
        a = BpmResult(bpm=None, confidence=0.0, source="aubio", error="fail")
        b = BpmResult(bpm=None, confidence=0.0, source="librosa", error="fail")
        result = cross_check_bpm(a, b)
        assert result.bpm_final is None

    @pytest.mark.unit
    def test_half_double_not_flagged(self) -> None:
        """If one reports half/double the other, that's a known octave error, not a disagreement."""
        a = BpmResult(bpm=128.0, confidence=0.9, source="aubio")
        b = BpmResult(bpm=64.0, confidence=0.85, source="librosa")
        result = cross_check_bpm(a, b)
        assert result.disagreement is False
        assert result.bpm_final == 128.0  # prefer the higher one in octave ambiguity

    @pytest.mark.unit
    def test_confidence_from_agreement(self) -> None:
        a = BpmResult(bpm=125.0, confidence=0.9, source="aubio")
        b = BpmResult(bpm=125.5, confidence=0.85, source="librosa")
        result = cross_check_bpm(a, b)
        assert result.confidence >= 0.85


class TestAnalyzeKey:
    """Tests for key estimation (mocked)."""

    @pytest.mark.unit
    def test_returns_key_result(self, tmp_path) -> None:
        f = tmp_path / "song.wav"
        f.write_bytes(b"\x00" * 44100)
        with patch("musesleuth.bpm_analyzer._librosa_key") as mock:
            mock.return_value = ("C", "major")
            result = analyze_key(str(f))
        assert isinstance(result, KeyResult)
        assert result.key == "C"
        assert result.mode == "major"

    @pytest.mark.unit
    def test_returns_none_on_failure(self, tmp_path) -> None:
        f = tmp_path / "bad.wav"
        f.write_bytes(b"\x00" * 100)
        with patch("musesleuth.bpm_analyzer._librosa_key") as mock:
            mock.side_effect = Exception("bad file")
            result = analyze_key(str(f))
        assert result.key is None
        assert result.error is not None
