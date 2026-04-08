"""Writeback stage runner -- writes enriched metadata to audio file tags."""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from musesleuth.tag_writer import write_tags_to_file

log = logging.getLogger(__name__)


@dataclass
class WritebackStageResult:
    """Result of the writeback stage for a single track."""

    success: bool = True
    metadata_id: str = ""
    fields_written: int = 0
    error: Optional[str] = None


DEFAULT_FIELDS = ["bpm", "genre", "key"]


def run_writeback_for_track(
    conn: sqlite3.Connection,
    metadata_id: str,
    fields: Optional[list[str]] = None,
) -> WritebackStageResult:
    """Run the writeback stage for a single track.

    Writes enriched metadata back to the audio file tags.
    Uses the fields specified in the job or defaults to bpm, genre, key.
    """
    log.debug("writeback: mid=%s fields=%s", metadata_id, fields or DEFAULT_FIELDS)
    if fields is None:
        fields = DEFAULT_FIELDS

    track = conn.execute(
        "SELECT full_path FROM tracks WHERE metadata_id = ?",
        (metadata_id,),
    ).fetchone()

    if not track:
        log.warning("writeback: track not found mid=%s", metadata_id)
        return WritebackStageResult(
            success=False,
            metadata_id=metadata_id,
            error=f"Track not found: {metadata_id}",
        )

    file_path = track["full_path"]

    tags: dict[str, str] = {}

    if "bpm" in fields:
        mf = conn.execute(
            "SELECT bpm_final FROM musical_features WHERE metadata_id = ?",
            (metadata_id,),
        ).fetchone()
        if mf and mf["bpm_final"]:
            tags["bpm"] = str(int(round(mf["bpm_final"])))

    if "key" in fields:
        ps = conn.execute(
            "SELECT camelot_key FROM playlist_signals WHERE metadata_id = ?",
            (metadata_id,),
        ).fetchone()
        if ps and ps["camelot_key"]:
            tags["key"] = ps["camelot_key"]

    if "genre" in fields:
        gt = conn.execute(
            "SELECT tag_value FROM genres_tags WHERE metadata_id = ? LIMIT 1",
            (metadata_id,),
        ).fetchone()
        if gt and gt["tag_value"]:
            tags["genre"] = gt["tag_value"]

    if "title" in fields:
        t = conn.execute(
            "SELECT title FROM tracks WHERE metadata_id = ?",
            (metadata_id,),
        ).fetchone()
        if t and t["title"]:
            tags["title"] = t["title"]

    if "artist" in fields:
        t = conn.execute(
            "SELECT artist FROM tracks WHERE metadata_id = ?",
            (metadata_id,),
        ).fetchone()
        if t and t["artist"]:
            tags["artist"] = t["artist"]

    if "album" in fields:
        t = conn.execute(
            "SELECT album FROM tracks WHERE metadata_id = ?",
            (metadata_id,),
        ).fetchone()
        if t and t["album"]:
            tags["album"] = t["album"]

    if not tags:
        return WritebackStageResult(
            success=True,
            metadata_id=metadata_id,
            fields_written=0,
            error=None,
        )

    result = write_tags_to_file(conn, metadata_id, file_path, tags)
    if result.success:
        log.debug("writeback: mid=%s wrote %d fields", metadata_id, result.fields_written)
    else:
        log.warning("writeback: mid=%s failed: %s", metadata_id, result.error)

    return WritebackStageResult(
        success=result.success,
        metadata_id=metadata_id,
        fields_written=result.fields_written,
        error=result.error,
    )
