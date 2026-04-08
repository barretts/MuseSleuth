"""Semantic filtration: title-based tagging and quality verdict for playlist eligibility."""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TitleClassification:
    """Result of title-based heuristic classification."""

    tags: list[str] = field(default_factory=list)


# Patterns for title-based detection, ordered by priority
_TAG_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("live", re.compile(r"\b(live\b|live at\b|live from\b)", re.IGNORECASE)),
    ("demo", re.compile(r"\b(demo|unmastered|rough mix)\b", re.IGNORECASE)),
    ("acoustic", re.compile(r"\bacoustic\b", re.IGNORECASE)),
    ("instrumental", re.compile(r"\binstrumental\b", re.IGNORECASE)),
    ("spoken", re.compile(r"\b(spoken\s*word|spoken\s*intro|narrat)", re.IGNORECASE)),
    ("skit", re.compile(r"\b(skit|interlude|intermission)\b", re.IGNORECASE)),
    ("acapella", re.compile(r"\b(acapella|a\s*cappella)\b", re.IGNORECASE)),
    ("karaoke", re.compile(r"\bkaraoke\b", re.IGNORECASE)),
]


def classify_title(title: str) -> TitleClassification:
    """Classify a track title using regex heuristics.

    Returns a TitleClassification with detected tags (e.g. "live", "demo", "skit").
    """
    tags: list[str] = []
    for tag, pattern in _TAG_PATTERNS:
        if pattern.search(title):
            tags.append(tag)
    return TitleClassification(tags=tags)


def compute_quality_verdict(
    lufs_i: Optional[float],
    lra: Optional[float],
    title_tags: list[str],
    clipping_ratio: Optional[float],
    lufs_demo_threshold: float = -18.0,
    lra_demo_threshold: float = 12.0,
    clipping_threshold: float = 0.02,
) -> str:
    """Compute a quality verdict string for playlist filtering.

    Priority order (first match wins):
      1. "skit"    - title contains skit/interlude
      2. "spoken"  - title contains spoken word
      3. "live"    - title indicates live recording
      4. "demo"    - title says demo OR loudness heuristic triggers
      5. "clipped" - clipping_ratio exceeds threshold
      6. "pass"    - none of the above

    Returns one of: "pass", "demo", "live", "clipped", "skit", "spoken"
    """
    # Title-based verdicts (highest priority)
    if "skit" in title_tags:
        return "skit"
    if "spoken" in title_tags:
        return "spoken"
    if "live" in title_tags:
        return "live"
    if "demo" in title_tags:
        return "demo"

    # Loudness-based demo detection
    if lufs_i is not None and lra is not None:
        if lufs_i < lufs_demo_threshold and lra > lra_demo_threshold:
            return "demo"

    # Clipping detection
    if clipping_ratio is not None and clipping_ratio > clipping_threshold:
        return "clipped"

    return "pass"


def run_filters_for_track(
    conn: sqlite3.Connection,
    metadata_id: str,
) -> str:
    """Compute and persist quality_verdict for a single track.

    Reads title from tracks, loudness from loudness_features, clipping from
    technical_features, writes verdict to playlist_signals.quality_verdict.

    Returns the computed verdict string.
    """
    # Get title
    track = conn.execute(
        "SELECT title FROM tracks WHERE metadata_id = ?", (metadata_id,)
    ).fetchone()
    title = (track["title"] or "") if track else ""

    # Classify title
    tc = classify_title(title)

    # Get loudness features
    lufs_i: Optional[float] = None
    lra: Optional[float] = None
    lf = conn.execute(
        "SELECT lufs_integrated, lra FROM loudness_features WHERE metadata_id = ?",
        (metadata_id,),
    ).fetchone()
    if lf:
        lufs_i = lf["lufs_integrated"]
        lra = lf["lra"]

    # Get clipping ratio
    clipping_ratio: Optional[float] = None
    tf = conn.execute(
        "SELECT clipping_ratio FROM technical_features WHERE metadata_id = ?",
        (metadata_id,),
    ).fetchone()
    if tf:
        clipping_ratio = tf["clipping_ratio"]

    verdict = compute_quality_verdict(lufs_i, lra, tc.tags, clipping_ratio)

    # Persist
    conn.execute(
        "UPDATE playlist_signals SET quality_verdict = ? WHERE metadata_id = ?",
        (verdict, metadata_id),
    )
    conn.commit()

    return verdict
