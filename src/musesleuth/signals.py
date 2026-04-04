"""Derive playlist-ready signals from enriched track data."""
from __future__ import annotations

import sqlite3
from typing import Optional


# Camelot wheel mapping: (key, mode) -> Camelot notation
CAMELOT_MAP = {
    ("C", "major"): "8B", ("C", "minor"): "5A",
    ("C#", "major"): "3B", ("C#", "minor"): "12A",
    ("D", "major"): "10B", ("D", "minor"): "7A",
    ("D#", "major"): "5B", ("D#", "minor"): "2A",
    ("E", "major"): "12B", ("E", "minor"): "9A",
    ("F", "major"): "7B", ("F", "minor"): "4A",
    ("F#", "major"): "2B", ("F#", "minor"): "11A",
    ("G", "major"): "9B", ("G", "minor"): "6A",
    ("G#", "major"): "4B", ("G#", "minor"): "1A",
    ("A", "major"): "11B", ("A", "minor"): "8A",
    ("A#", "major"): "6B", ("A#", "minor"): "3A",
    ("B", "major"): "1B", ("B", "minor"): "10A",
}


def derive_decade_bucket(year: Optional[str]) -> Optional[str]:
    """Convert a year string to a decade bucket like '2000s'."""
    if not year:
        return None
    try:
        y = int(year[:4])
        decade = (y // 10) * 10
        return f"{decade}s"
    except (ValueError, IndexError):
        return None


def derive_bpm_bucket(bpm: Optional[float]) -> Optional[str]:
    """Classify BPM into speed buckets."""
    if bpm is None:
        return None
    if bpm < 100:
        return "slow"
    elif bpm < 120:
        return "mid"
    elif bpm < 150:
        return "fast"
    else:
        return "very_fast"


def derive_energy_tier(energy: Optional[float]) -> Optional[str]:
    """Classify energy (0.0-1.0) into tiers."""
    if energy is None:
        return None
    if energy < 0.33:
        return "low"
    elif energy < 0.66:
        return "medium"
    else:
        return "high"


def derive_camelot_key(key: Optional[str], mode: Optional[str]) -> Optional[str]:
    """Map a musical key and mode to Camelot wheel notation."""
    if key is None or mode is None:
        return None
    return CAMELOT_MAP.get((key, mode))


def derive_popularity_tier(listener_count: Optional[int]) -> str:
    """Classify listener count into popularity tiers."""
    if listener_count is None:
        return "unknown"
    if listener_count < 1000:
        return "niche"
    elif listener_count < 100000:
        return "mainstream"
    else:
        return "viral"


def run_derive_signals_for_track(
    conn: sqlite3.Connection,
    metadata_id: str,
) -> None:
    """Derive and store all playlist signals for a track."""
    # Get year from tracks
    track = conn.execute(
        "SELECT year FROM tracks WHERE metadata_id = ?", (metadata_id,)
    ).fetchone()
    year = track["year"] if track else None

    # Get musical features
    mf = conn.execute(
        "SELECT bpm_final, key_name, key_mode, energy FROM musical_features WHERE metadata_id = ?",
        (metadata_id,),
    ).fetchone()

    bpm = mf["bpm_final"] if mf else None
    key_name = mf["key_name"] if mf else None
    key_mode = mf["key_mode"] if mf else None
    energy = mf["energy"] if mf else None

    # Get listener count
    ts = conn.execute(
        "SELECT listener_count FROM track_stats WHERE metadata_id = ? ORDER BY listener_count DESC LIMIT 1",
        (metadata_id,),
    ).fetchone()
    listeners = ts["listener_count"] if ts else None

    # Derive signals
    decade = derive_decade_bucket(year)
    year_bucket = year[:4] if year and len(year) >= 4 else None
    bpm_b = derive_bpm_bucket(bpm)
    camelot = derive_camelot_key(key_name, key_mode)
    energy_t = derive_energy_tier(energy)
    pop_t = derive_popularity_tier(listeners)

    conn.execute(
        """
        INSERT OR REPLACE INTO playlist_signals
            (metadata_id, decade_bucket, year_bucket, bpm_bucket, camelot_key,
             energy_tier, popularity_tier, computed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        (metadata_id, decade, year_bucket, bpm_b, camelot, energy_t, pop_t),
    )
    conn.commit()