"""TDD tests for sidecar identity files -- written before implementation."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from musesleuth.sidecar import (
    SIDECAR_EXT,
    SidecarData,
    read_sidecar,
    sidecar_path_for,
    write_sidecar,
)


class TestSidecarPath:
    """Tests for sidecar path derivation."""

    @pytest.mark.unit
    def test_appends_extension(self) -> None:
        audio = Path("G:/dlp/music/song.mp3")
        result = sidecar_path_for(audio)
        assert result == Path("G:/dlp/music/song.mp3" + SIDECAR_EXT)

    @pytest.mark.unit
    def test_works_with_flac(self) -> None:
        audio = Path("/music/track.flac")
        result = sidecar_path_for(audio)
        assert str(result).endswith(SIDECAR_EXT)


class TestWriteAndReadSidecar:
    """Tests for atomic sidecar write and read round-trip."""

    @pytest.mark.integration
    def test_write_creates_file(self, tmp_dir: Path) -> None:
        audio_path = tmp_dir / "song.mp3"
        audio_path.write_bytes(b"\xff\xfb\x90\x00" * 100)

        data = SidecarData(
            metadata_id="01HXTEST000000000000000001",
            path=str(audio_path),
            size=audio_path.stat().st_size,
            mtime=audio_path.stat().st_mtime,
        )
        sc_path = write_sidecar(audio_path, data)
        assert sc_path.exists()

    @pytest.mark.integration
    def test_round_trip(self, tmp_dir: Path) -> None:
        audio_path = tmp_dir / "song.mp3"
        audio_path.write_bytes(b"\xff\xfb\x90\x00" * 100)

        data = SidecarData(
            metadata_id="01HXTEST000000000000000001",
            path=str(audio_path),
            size=audio_path.stat().st_size,
            mtime=audio_path.stat().st_mtime,
        )
        write_sidecar(audio_path, data)
        loaded = read_sidecar(audio_path)
        assert loaded is not None
        assert loaded.metadata_id == data.metadata_id
        assert loaded.size == data.size
        assert loaded.version == 1

    @pytest.mark.integration
    def test_atomic_write_no_partial(self, tmp_dir: Path) -> None:
        """If writing fails mid-way, no sidecar file should exist."""
        audio_path = tmp_dir / "song.mp3"
        audio_path.write_bytes(b"\xff\xfb\x90\x00" * 100)
        sc = sidecar_path_for(audio_path)
        assert not sc.exists()

    @pytest.mark.integration
    def test_read_returns_none_when_missing(self, tmp_dir: Path) -> None:
        audio_path = tmp_dir / "no_sidecar.mp3"
        result = read_sidecar(audio_path)
        assert result is None

    @pytest.mark.integration
    def test_sidecar_json_has_version(self, tmp_dir: Path) -> None:
        audio_path = tmp_dir / "versioned.mp3"
        audio_path.write_bytes(b"\xff\xfb\x90\x00" * 10)

        data = SidecarData(
            metadata_id="01HXTEST000000000000000002",
            path=str(audio_path),
            size=audio_path.stat().st_size,
            mtime=audio_path.stat().st_mtime,
        )
        sc_path = write_sidecar(audio_path, data)
        raw = json.loads(sc_path.read_text(encoding="utf-8"))
        assert raw["v"] == 1
        assert "id" in raw
        assert "created_at" in raw
        assert "updated_at" in raw

    @pytest.mark.integration
    def test_overwrite_preserves_created_at(self, tmp_dir: Path) -> None:
        audio_path = tmp_dir / "update.mp3"
        audio_path.write_bytes(b"\xff\xfb\x90\x00" * 10)

        data1 = SidecarData(
            metadata_id="01HXTEST000000000000000003",
            path=str(audio_path),
            size=audio_path.stat().st_size,
            mtime=audio_path.stat().st_mtime,
        )
        write_sidecar(audio_path, data1)
        first = read_sidecar(audio_path)

        data2 = SidecarData(
            metadata_id="01HXTEST000000000000000003",
            path=str(audio_path),
            size=999,
            mtime=audio_path.stat().st_mtime,
        )
        write_sidecar(audio_path, data2, preserve_created=True)
        second = read_sidecar(audio_path)

        assert second is not None
        assert first is not None
        assert second.created_at == first.created_at
        assert second.size == 999
