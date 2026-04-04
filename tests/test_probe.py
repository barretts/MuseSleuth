"""TDD tests for ffprobe/mediainfo parsing -- written before implementation."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from musesleuth.probe import parse_ffprobe_output, TechnicalMetadata


FIXTURES_DIR = Path(__file__).parent / "fixtures"


class TestParseFFprobeOutput:
    """Tests for parsing ffprobe JSON output into TechnicalMetadata."""

    @pytest.fixture
    def mp3_ffprobe(self) -> dict:
        return json.loads((FIXTURES_DIR / "ffprobe_mp3.json").read_text())

    @pytest.fixture
    def flac_ffprobe(self) -> dict:
        return json.loads((FIXTURES_DIR / "ffprobe_flac.json").read_text())

    @pytest.mark.unit
    def test_parses_codec(self, mp3_ffprobe: dict) -> None:
        result = parse_ffprobe_output(mp3_ffprobe)
        assert result.codec == "mp3"

    @pytest.mark.unit
    def test_parses_bitrate(self, mp3_ffprobe: dict) -> None:
        result = parse_ffprobe_output(mp3_ffprobe)
        assert result.bitrate == 320000

    @pytest.mark.unit
    def test_parses_sample_rate(self, mp3_ffprobe: dict) -> None:
        result = parse_ffprobe_output(mp3_ffprobe)
        assert result.sample_rate == 44100

    @pytest.mark.unit
    def test_parses_channels(self, mp3_ffprobe: dict) -> None:
        result = parse_ffprobe_output(mp3_ffprobe)
        assert result.channels == 2

    @pytest.mark.unit
    def test_parses_duration_ms(self, mp3_ffprobe: dict) -> None:
        result = parse_ffprobe_output(mp3_ffprobe)
        assert result.duration_ms == 214123

    @pytest.mark.unit
    def test_parses_flac(self, flac_ffprobe: dict) -> None:
        result = parse_ffprobe_output(flac_ffprobe)
        assert result.codec == "flac"
        assert result.sample_rate == 48000
        assert result.duration_ms == 301555

    @pytest.mark.unit
    def test_returns_none_fields_on_empty_input(self) -> None:
        result = parse_ffprobe_output({})
        assert result.codec is None
        assert result.bitrate is None
        assert result.duration_ms is None

    @pytest.mark.unit
    def test_extracts_format_tags(self, mp3_ffprobe: dict) -> None:
        result = parse_ffprobe_output(mp3_ffprobe)
        assert result.format_tags is not None
        assert result.format_tags["title"] == "Into The Inner Space (Trance Radio Edit)"

    @pytest.mark.unit
    def test_preserves_raw_json(self, mp3_ffprobe: dict) -> None:
        result = parse_ffprobe_output(mp3_ffprobe)
        assert result.raw_json is not None
        reparsed = json.loads(result.raw_json)
        assert "streams" in reparsed
