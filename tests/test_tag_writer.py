"""TDD tests for tag writeback -- written before implementation."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from musesleuth.db import create_schema, generate_metadata_id
from musesleuth.tag_writer import (
    write_tags_to_file,
    WritebackResult,
    WRITABLE_FIELDS,
)


def _make_tagged_mp3(path: Path) -> Path:
    from mutagen.mp3 import MP3
    from mutagen.id3 import TIT2, TPE1
    frame = b"\xff\xfb\x90\x00" + b"\x00" * 413
    path.write_bytes(frame * 10)
    try:
        audio = MP3(str(path))
        audio.add_tags()
        audio.tags.add(TIT2(encoding=3, text=["Original Title"]))
        audio.tags.add(TPE1(encoding=3, text=["Original Artist"]))
        audio.save()
    except Exception:
        pass
    return path


@pytest.fixture
def db(in_memory_db: sqlite3.Connection) -> sqlite3.Connection:
    create_schema(in_memory_db)
    return in_memory_db


class TestWritebackResult:
    @pytest.mark.unit
    def test_success(self) -> None:
        r = WritebackResult(success=True, fields_written=3)
        assert r.success is True

    @pytest.mark.unit
    def test_failure(self) -> None:
        r = WritebackResult(success=False, error="read only")
        assert r.error == "read only"


class TestWriteTagsToFile:
    """Tests for writing selected tags into audio file metadata."""

    @pytest.mark.integration
    def test_writes_bpm_tag(self, tmp_path: Path, db: sqlite3.Connection) -> None:
        mid = generate_metadata_id()
        mp3 = _make_tagged_mp3(tmp_path / "song.mp3")
        tags_to_write = {"bpm": "128"}

        result = write_tags_to_file(db, mid, str(mp3), tags_to_write)
        assert result.success is True
        assert result.fields_written >= 1

        from mutagen.mp3 import MP3
        audio = MP3(str(mp3))
        assert "TBPM" in audio.tags
        assert audio.tags["TBPM"].text[0] == "128"

    @pytest.mark.integration
    def test_writes_genre_tag(self, tmp_path: Path, db: sqlite3.Connection) -> None:
        mid = generate_metadata_id()
        mp3 = _make_tagged_mp3(tmp_path / "song.mp3")
        tags_to_write = {"genre": "Trance"}

        result = write_tags_to_file(db, mid, str(mp3), tags_to_write)
        assert result.success is True

        from mutagen.mp3 import MP3
        audio = MP3(str(mp3))
        assert "TCON" in audio.tags
        assert audio.tags["TCON"].text[0] == "Trance"

    @pytest.mark.integration
    def test_logs_writeback(self, tmp_path: Path, db: sqlite3.Connection) -> None:
        mid = generate_metadata_id()
        db.execute(
            "INSERT INTO tracks (metadata_id, file_path, filename, full_path) VALUES (?, ?, ?, ?)",
            (mid, str(tmp_path), "song.mp3", str(tmp_path / "song.mp3")),
        )
        db.commit()
        mp3 = _make_tagged_mp3(tmp_path / "song.mp3")

        write_tags_to_file(db, mid, str(mp3), {"bpm": "140"})

        rows = db.execute(
            "SELECT field_name, new_value FROM tag_writeback_log WHERE metadata_id = ?",
            (mid,),
        ).fetchall()
        assert len(rows) >= 1
        assert any(r["field_name"] == "bpm" for r in rows)

    @pytest.mark.integration
    def test_handles_missing_file(self, tmp_path: Path, db: sqlite3.Connection) -> None:
        mid = generate_metadata_id()
        result = write_tags_to_file(db, mid, str(tmp_path / "gone.mp3"), {"bpm": "128"})
        assert result.success is False

    @pytest.mark.integration
    def test_writable_fields_defined(self) -> None:
        assert "bpm" in WRITABLE_FIELDS
        assert "genre" in WRITABLE_FIELDS
        assert "key" in WRITABLE_FIELDS
