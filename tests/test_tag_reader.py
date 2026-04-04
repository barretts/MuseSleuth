"""TDD tests for embedded tag reading -- written before implementation."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from musesleuth.tag_reader import read_tags, TagSnapshot


class TestReadTags:
    """Tests for reading embedded tags from audio files via mutagen."""

    @pytest.mark.unit
    def test_returns_tag_snapshot(self, tmp_path: Path) -> None:
        """A valid MP3 file should return a TagSnapshot."""
        mp3 = _make_minimal_mp3(tmp_path / "tagged.mp3")
        result = read_tags(mp3)
        assert isinstance(result, TagSnapshot)

    @pytest.mark.unit
    def test_contains_raw_json(self, tmp_path: Path) -> None:
        mp3 = _make_minimal_mp3(tmp_path / "tagged.mp3")
        result = read_tags(mp3)
        assert result.raw_json is not None
        parsed = json.loads(result.raw_json)
        assert isinstance(parsed, dict)

    @pytest.mark.unit
    def test_returns_empty_snapshot_for_untagged(self, tmp_path: Path) -> None:
        """A file with no tags should still return a TagSnapshot with empty fields."""
        f = tmp_path / "noise.bin"
        f.write_bytes(b"\x00" * 1000)
        result = read_tags(f)
        assert isinstance(result, TagSnapshot)
        assert result.tags == {}

    @pytest.mark.unit
    def test_returns_empty_snapshot_for_missing_file(self, tmp_path: Path) -> None:
        result = read_tags(tmp_path / "nonexistent.mp3")
        assert isinstance(result, TagSnapshot)
        assert result.tags == {}
        assert result.error is not None

    @pytest.mark.unit
    def test_extracts_title_from_mp3(self, tmp_path: Path) -> None:
        mp3 = _make_tagged_mp3(tmp_path / "song.mp3", title="Test Title")
        result = read_tags(mp3)
        assert result.tags.get("title") == "Test Title"

    @pytest.mark.unit
    def test_extracts_artist_from_mp3(self, tmp_path: Path) -> None:
        mp3 = _make_tagged_mp3(tmp_path / "song.mp3", artist="Test Artist")
        result = read_tags(mp3)
        assert result.tags.get("artist") == "Test Artist"


def _make_minimal_mp3(path: Path) -> Path:
    """Create a minimal valid MP3 file with an ID3 header."""
    from mutagen.mp3 import MP3
    from mutagen.id3 import ID3, TIT2

    # Write a minimal MPEG frame header + padding so mutagen can open it
    # Sync word 0xFFE0 + MPEG1 Layer3 128kbps 44100Hz stereo
    frame = b"\xff\xfb\x90\x00" + b"\x00" * 413  # ~417 bytes per frame at 128kbps
    path.write_bytes(frame * 10)

    try:
        audio = MP3(str(path))
        audio.add_tags()
        audio.save()
    except Exception:
        pass
    return path


def _make_tagged_mp3(path: Path, title: str = "", artist: str = "") -> Path:
    """Create a minimal MP3 with specific ID3 tags."""
    from mutagen.mp3 import MP3
    from mutagen.id3 import ID3, TIT2, TPE1

    frame = b"\xff\xfb\x90\x00" + b"\x00" * 413
    path.write_bytes(frame * 10)

    try:
        audio = MP3(str(path))
        audio.add_tags()
        if title:
            audio.tags.add(TIT2(encoding=3, text=[title]))
        if artist:
            audio.tags.add(TPE1(encoding=3, text=[artist]))
        audio.save()
    except Exception:
        pass
    return path
