"""Read embedded audio tags using mutagen."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import mutagen
from mutagen.id3 import ID3


# Mapping of common ID3 frame IDs to normalized field names
_ID3_TAG_MAP = {
    "TIT2": "title",
    "TPE1": "artist",
    "TALB": "album",
    "TRCK": "track",
    "TDRC": "date",
    "TYER": "year",
    "TCON": "genre",
    "TBPM": "bpm",
    "TKEY": "key",
    "TPUB": "publisher",
    "COMM": "comment",
    "TLAN": "language",
    "TCOP": "copyright",
    "TPE2": "album_artist",
    "TPOS": "disc_number",
}


@dataclass
class TagSnapshot:
    """Snapshot of embedded tags from an audio file."""

    tags: dict[str, str] = field(default_factory=dict)
    raw_json: Optional[str] = None
    error: Optional[str] = None


def read_tags(path: Path) -> TagSnapshot:
    """Read embedded tags from an audio file.

    Returns a TagSnapshot even on failure (with error field set).
    Uses mutagen to auto-detect format.
    """
    path = Path(path)
    if not path.exists():
        return TagSnapshot(error=f"File not found: {path}")

    try:
        audio = mutagen.File(str(path), easy=False)
    except Exception as exc:
        return TagSnapshot(error=str(exc))

    if audio is None or audio.tags is None:
        return TagSnapshot(
            raw_json=json.dumps({}),
        )

    raw_tags: dict[str, str] = {}
    normalized: dict[str, str] = {}

    try:
        # Handle ID3 tags (MP3, AIFF, etc.)
        if hasattr(audio.tags, "getall"):
            for frame_id in audio.tags:
                frame = audio.tags[frame_id]
                text_val = _extract_text(frame)
                if text_val is not None:
                    raw_tags[frame_id] = text_val
                    base_id = frame_id.split(":")[0] if ":" in frame_id else frame_id
                    if base_id in _ID3_TAG_MAP:
                        normalized[_ID3_TAG_MAP[base_id]] = text_val
        else:
            # Vorbis comments, MP4 tags, etc.
            for key, value in audio.tags.items():
                if isinstance(value, list):
                    text_val = str(value[0]) if value else ""
                else:
                    text_val = str(value)
                raw_tags[key] = text_val
                norm_key = key.lower().replace(" ", "_")
                normalized[norm_key] = text_val
    except Exception as exc:
        return TagSnapshot(error=str(exc))

    return TagSnapshot(
        tags=normalized,
        raw_json=json.dumps(raw_tags, ensure_ascii=False),
    )


def _extract_text(frame: object) -> Optional[str]:
    """Extract text content from a mutagen ID3 frame."""
    if hasattr(frame, "text") and frame.text:
        return str(frame.text[0])
    if hasattr(frame, "url"):
        return str(frame.url)
    return None