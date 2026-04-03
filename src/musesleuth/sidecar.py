"""Per-file sidecar identity records for MuseSleuth track linking."""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


SIDECAR_EXT = ".dlpmeta"


@dataclass
class SidecarData:
    """Data stored in a sidecar identity file."""

    metadata_id: str
    path: str
    size: int
    mtime: float
    hash_partial: Optional[str] = None
    hash_full: Optional[str] = None
    fingerprint: Optional[str] = None
    version: int = 1
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        now = _now_iso()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now


def sidecar_path_for(audio_path: Path) -> Path:
    """Derive the sidecar file path for a given audio file."""
    return Path(str(audio_path) + SIDECAR_EXT)


def write_sidecar(
    audio_path: Path,
    data: SidecarData,
    *,
    preserve_created: bool = False,
) -> Path:
    """Atomically write a sidecar JSON file beside the audio file.

    Uses temp-file + rename for atomicity. If preserve_created is True
    and a sidecar already exists, the created_at timestamp is preserved.
    """
    sc_path = sidecar_path_for(audio_path)

    if preserve_created and sc_path.exists():
        existing = read_sidecar(audio_path)
        if existing is not None:
            data.created_at = existing.created_at

    data.updated_at = _now_iso()

    payload = {
        "v": data.version,
        "id": data.metadata_id,
        "path": data.path,
        "size": data.size,
        "mtime": data.mtime,
        "hash_partial": data.hash_partial,
        "hash_full": data.hash_full,
        "fp": data.fingerprint,
        "created_at": data.created_at,
        "updated_at": data.updated_at,
    }

    # Atomic write: write to temp file in same dir, then rename
    dir_path = sc_path.parent
    fd, tmp_path = tempfile.mkstemp(dir=str(dir_path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, str(sc_path))
    except Exception:
        # Clean up temp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    return sc_path


def read_sidecar(audio_path: Path) -> Optional[SidecarData]:
    """Read a sidecar file for the given audio path. Returns None if missing."""
    sc_path = sidecar_path_for(audio_path)
    if not sc_path.exists():
        return None

    try:
        raw = json.loads(sc_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    return SidecarData(
        metadata_id=raw.get("id", ""),
        path=raw.get("path", ""),
        size=raw.get("size", 0),
        mtime=raw.get("mtime", 0.0),
        hash_partial=raw.get("hash_partial"),
        hash_full=raw.get("hash_full"),
        fingerprint=raw.get("fp"),
        version=raw.get("v", 1),
        created_at=raw.get("created_at", ""),
        updated_at=raw.get("updated_at", ""),
    )


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()