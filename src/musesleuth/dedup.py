"""Duplicate detection across tracks via hash matching and fuzzy title/artist comparison."""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Optional

from musesleuth.db import generate_metadata_id


@dataclass
class DuplicateGroup:
    """A group of tracks identified as duplicates."""

    group_id: str
    metadata_ids: list[str] = field(default_factory=list)
    match_type: str = ""  # "hash_full", "hash_partial", "fuzzy"


def find_hash_duplicates(conn: sqlite3.Connection) -> list[DuplicateGroup]:
    """Find tracks with identical BLAKE3 hashes (full or partial).

    Returns one DuplicateGroup per set of duplicates.
    """
    groups: list[DuplicateGroup] = []

    # Full hash duplicates
    rows = conn.execute(
        """
        SELECT hash_full, GROUP_CONCAT(metadata_id) as mids
        FROM track_sidecars
        WHERE hash_full IS NOT NULL AND hash_full != ''
        GROUP BY hash_full
        HAVING COUNT(*) > 1
        """
    ).fetchall()

    seen_mids: set[str] = set()
    for row in rows:
        mids = row["mids"].split(",")
        gid = generate_metadata_id()
        groups.append(DuplicateGroup(group_id=gid, metadata_ids=mids, match_type="hash_full"))
        seen_mids.update(mids)

    # Partial hash duplicates (only for tracks not already grouped by full hash)
    rows = conn.execute(
        """
        SELECT hash_partial, GROUP_CONCAT(metadata_id) as mids
        FROM track_sidecars
        WHERE hash_partial IS NOT NULL AND hash_partial != ''
        GROUP BY hash_partial
        HAVING COUNT(*) > 1
        """
    ).fetchall()

    for row in rows:
        mids = [m for m in row["mids"].split(",") if m not in seen_mids]
        if len(mids) > 1:
            gid = generate_metadata_id()
            groups.append(DuplicateGroup(group_id=gid, metadata_ids=mids, match_type="hash_partial"))

    return groups


def find_fuzzy_duplicates(
    conn: sqlite3.Connection,
    title_threshold: float = 0.85,
    duration_tolerance_ms: int = 5000,
) -> list[DuplicateGroup]:
    """Find tracks with similar titles by the same artist within a duration window.

    Uses SequenceMatcher for fuzzy title comparison.
    """
    tracks = conn.execute(
        """
        SELECT t.metadata_id, t.title, t.artist,
               tf.duration_ms
        FROM tracks t
        LEFT JOIN technical_features tf ON t.metadata_id = tf.metadata_id
        WHERE t.title IS NOT NULL AND t.title != ''
          AND t.artist IS NOT NULL AND t.artist != ''
        """
    ).fetchall()

    # Group by normalized artist for efficiency
    by_artist: dict[str, list] = defaultdict(list)
    for t in tracks:
        key = (t["artist"] or "").strip().lower()
        by_artist[key].append(t)

    groups: list[DuplicateGroup] = []
    seen: set[str] = set()

    for artist_key, artist_tracks in by_artist.items():
        n = len(artist_tracks)
        for i in range(n):
            if artist_tracks[i]["metadata_id"] in seen:
                continue
            cluster = [artist_tracks[i]["metadata_id"]]

            t1_title = (artist_tracks[i]["title"] or "").strip().lower()
            t1_dur = artist_tracks[i]["duration_ms"]

            for j in range(i + 1, n):
                if artist_tracks[j]["metadata_id"] in seen:
                    continue

                t2_title = (artist_tracks[j]["title"] or "").strip().lower()
                t2_dur = artist_tracks[j]["duration_ms"]

                # Title similarity
                ratio = SequenceMatcher(None, t1_title, t2_title).ratio()
                if ratio < title_threshold:
                    continue

                # Duration check (if both have durations)
                if t1_dur is not None and t2_dur is not None:
                    if abs(t1_dur - t2_dur) > duration_tolerance_ms:
                        continue

                cluster.append(artist_tracks[j]["metadata_id"])

            if len(cluster) > 1:
                gid = generate_metadata_id()
                groups.append(DuplicateGroup(group_id=gid, metadata_ids=cluster, match_type="fuzzy"))
                seen.update(cluster)

    return groups


def find_remix_groups(conn: sqlite3.Connection) -> list[DuplicateGroup]:
    """Find tracks that are different mixes/remixes of the same composition.

    Groups tracks by the same normalized artist and base title (with remix
    parenthetical stripped).  No duration check -- remixes intentionally have
    different lengths.
    """
    from musesleuth.filename_parser import extract_base_title

    tracks = conn.execute(
        """
        SELECT metadata_id, title, artist
        FROM tracks
        WHERE title IS NOT NULL AND title != ''
          AND artist IS NOT NULL AND artist != ''
        """
    ).fetchall()

    by_composition: dict[tuple[str, str], list[str]] = defaultdict(list)
    for t in tracks:
        artist_key = (t["artist"] or "").strip().lower()
        base = extract_base_title(t["title"] or "").strip().lower()
        if base:
            by_composition[(artist_key, base)].append(t["metadata_id"])

    groups: list[DuplicateGroup] = []
    for (_artist, _base), mids in by_composition.items():
        if len(mids) > 1:
            gid = generate_metadata_id()
            groups.append(DuplicateGroup(group_id=gid, metadata_ids=mids, match_type="remix"))

    return groups


def assign_duplicate_groups(conn: sqlite3.Connection) -> tuple[int, int]:
    """Find all duplicates and remix groups, writing IDs into playlist_signals.

    Returns ``(duplicate_groups_assigned, remix_groups_assigned)``.
    """
    hash_groups = find_hash_duplicates(conn)
    fuzzy_groups = find_fuzzy_duplicates(conn)
    remix_groups = find_remix_groups(conn)

    dup_assigned = 0
    for group in hash_groups + fuzzy_groups:
        for mid in group.metadata_ids:
            conn.execute(
                "UPDATE playlist_signals SET duplicate_group = ? WHERE metadata_id = ?",
                (group.group_id, mid),
            )
        dup_assigned += 1

    remix_assigned = 0
    for group in remix_groups:
        for mid in group.metadata_ids:
            conn.execute(
                "UPDATE playlist_signals SET remix_group = ? WHERE metadata_id = ?",
                (group.group_id, mid),
            )
        remix_assigned += 1

    conn.commit()
    return dup_assigned, remix_assigned