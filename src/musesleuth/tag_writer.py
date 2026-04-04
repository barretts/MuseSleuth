"""Write enriched metadata back into audio file tags."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import mutagen
from mutagen.id3 import TBPM, TCON, TKEY, TIT2, TPE1, TALB, COMM
from mutagen.mp3 import MP3


# Fields that can be written back to audio files
WRITABLE_FIELDS = {
    "bpm": TBPM,
    "genre": TCON,
    "key": TKEY,
    "title": TIT2,
    "artist": TPE1,
    "album": TALB,
}


@dataclass
class WritebackResult:
    """Result of a tag writeback operation."""

    success: bool
    fields_written: int = 0
    error: Optional[str] = None


def write_tags_to_file(
    conn: sqlite3.Connection,
    metadata_id: str,
    file_path: str,
    tags: dict[str, str],
) -> WritebackResult:
    """Write selected tag fields into an audio file.

    Supports MP3 (ID3) files. Logs each field change to tag_writeback_log.
    """
    path = Path(file_path)
    if not path.exists():
        return WritebackResult(success=False, error=f"File not found: {file_path}")

    try:
        audio = MP3(str(path))
    except Exception as exc:
        return WritebackResult(success=False, error=str(exc))

    if audio.tags is None:
        try:
            audio.add_tags()
        except Exception:
            pass

    written = 0
    for field_name, value in tags.items():
        frame_cls = WRITABLE_FIELDS.get(field_name)
        if frame_cls is None:
            continue

        # Get old value for logging
        frame_id = _frame_id_for(field_name)
        old_value = None
        if frame_id and frame_id in audio.tags:
            old_frame = audio.tags[frame_id]
            if hasattr(old_frame, "text") and old_frame.text:
                old_value = str(old_frame.text[0])

        # Write new value
        audio.tags.add(frame_cls(encoding=3, text=[value]))
        written += 1

        # Log the change
        try:
            conn.execute(
                """
                INSERT INTO tag_writeback_log
                    (metadata_id, field_name, old_value, new_value)
                VALUES (?, ?, ?, ?)
                """,
                (metadata_id, field_name, old_value, value),
            )
        except Exception:
            pass

    try:
        audio.save()
        conn.commit()
    except Exception as exc:
        return WritebackResult(success=False, error=str(exc))

    return WritebackResult(success=True, fields_written=written)


def _frame_id_for(field_name: str) -> Optional[str]:
    """Map a field name to its ID3 frame ID."""
    mapping = {
        "bpm": "TBPM",
        "genre": "TCON",
        "key": "TKEY",
        "title": "TIT2",
        "artist": "TPE1",
        "album": "TALB",
    }
    return mapping.get(field_name)