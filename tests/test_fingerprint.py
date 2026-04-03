"""TDD tests for Chromaprint fingerprinting -- written before implementation."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from musesleuth.fingerprint import FingerprintResult, run_fpcalc


class TestRunFpcalc:
    """Tests for fpcalc wrapper (mocked subprocess)."""

    @pytest.mark.unit
    def test_returns_fingerprint_result(self, tmp_path) -> None:
        f = tmp_path / "song.mp3"
        f.write_bytes(b"\xff\xfb\x90\x00" * 1000)
        with patch("musesleuth.fingerprint._invoke_fpcalc") as mock:
            mock.return_value = {"duration": 214, "fingerprint": "AQABz0qUkZ..."}
            result = run_fpcalc(str(f))
        assert isinstance(result, FingerprintResult)
        assert result.fingerprint == "AQABz0qUkZ..."
        assert result.duration == 214

    @pytest.mark.unit
    def test_returns_none_on_failure(self, tmp_path) -> None:
        f = tmp_path / "bad.mp3"
        f.write_bytes(b"\x00" * 100)
        with patch("musesleuth.fingerprint._invoke_fpcalc") as mock:
            mock.side_effect = Exception("fpcalc not found")
            result = run_fpcalc(str(f))
        assert result.fingerprint is None
        assert result.error is not None

    @pytest.mark.unit
    def test_returns_none_for_missing_file(self, tmp_path) -> None:
        result = run_fpcalc(str(tmp_path / "nonexistent.mp3"))
        assert result.fingerprint is None
        assert result.error is not None
