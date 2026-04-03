"""Probe stage runner -- orchestrates ffprobe, hashing, tag reading for a single track."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import sqlite3

from musesleuth.hasher import hash_partial
from musesleuth.probe import check_integrity, parse_ffprobe_output, repair_remux
from musesleuth.sidecar import SidecarData, read_sidecar, write_sidecar
from musesleuth.tag_reader import read_tags


@dataclass
class ProbeResult:
    """Result of probing a single track."""

    success: bool
    metadata_id: str = ""
    error: Optional[str] = None


def run_probe_for_track(
    conn: sqlite3.Connection,
    metadata_id: str,
    file_path: str,
) -> ProbeResult:
    """Run the full probe stage for a single track.

    1. Verify file exists
    2. Run ffprobe and parse output
    3. Compute partial BLAKE3 hash
    4. Read embedded tags
    5. Store results in DB
    6. Update sidecar with hash
    """
    path = Path(file_path)

    if not path.exists():
        return ProbeResult(
            success=False,
            metadata_id=metadata_id,
            error=f"File not found: {file_path}",
        )

    # 1. Run ffprobe
    try:
        ffprobe_data = _run_ffprobe(str(path))
    except Exception as exc:
        ffprobe_data = {}

    tech = parse_ffprobe_output(ffprobe_data)

    # 1b. Integrity check (full decode), with repair attempt if needed
    try:
        integrity = check_integrity(str(path))
        if not integrity.ok:
            integrity.repair_attempted = True
            if repair_remux(str(path)):
                recheck = check_integrity(str(path))
                if recheck.ok:
                    integrity = recheck
                    integrity.was_repaired = True
                    integrity.repair_attempted = True
    except Exception:
        integrity = None
    tech.integrity = integrity

    # 2. Compute partial hash
    try:
        partial_hash = hash_partial(path)
    except Exception:
        partial_hash = None

    # 3. Read embedded tags
    tag_snap = read_tags(path)

    # 4. Store technical features
    conn.execute(
        """
        INSERT OR REPLACE INTO technical_features
            (metadata_id, codec, bitrate, sample_rate, channels, duration_ms,
             raw_ffprobe, integrity_ok, integrity_errors, was_repaired, analyzed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        (
            metadata_id,
            tech.codec,
            tech.bitrate,
            tech.sample_rate,
            tech.channels,
            tech.duration_ms,
            tech.raw_json,
            1 if (integrity is None or integrity.ok) else 0,
            integrity.to_json() if integrity else "[]",
            1 if (integrity and integrity.was_repaired) else 0,
        ),
    )

    # 5. Store tag snapshot
    conn.execute(
        """
        INSERT OR REPLACE INTO tag_snapshot_raw
            (metadata_id, tags_json)
        VALUES (?, ?)
        """,
        (metadata_id, tag_snap.raw_json or "{}"),
    )

    # 6. Store/update sidecar record in DB
    stat = path.stat()
    conn.execute(
        """
        INSERT OR REPLACE INTO track_sidecars
            (metadata_id, sidecar_path, hash_partial, synced_at)
        VALUES (?, ?, ?, datetime('now'))
        """,
        (
            metadata_id,
            str(path) + ".dlpmeta",
            partial_hash,
        ),
    )

    conn.commit()

    # 7. Update sidecar file if it exists
    existing_sc = read_sidecar(path)
    if existing_sc is not None:
        existing_sc.hash_partial = partial_hash
        existing_sc.size = stat.st_size
        existing_sc.mtime = stat.st_mtime
        write_sidecar(path, existing_sc, preserve_created=True)

    return ProbeResult(success=True, metadata_id=metadata_id)


def _run_ffprobe(file_path: str) -> dict:
    """Run ffprobe and return parsed JSON output.

    This is the real implementation; tests mock this function.
    """
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        "-show_format",
        file_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        return {}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}
