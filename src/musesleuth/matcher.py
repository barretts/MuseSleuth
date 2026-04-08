"""Fingerprint matching via AcoustID and fuzzy metadata fallback."""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Optional

import requests

log = logging.getLogger(__name__)

SCORE_THRESHOLD = 0.5
ACOUSTID_API_URL = "https://api.acoustid.org/v2/lookup"


@dataclass
class AcoustIDCandidate:
    """A candidate recording returned by AcoustID lookup."""

    recording_id: str = ""
    acoustid_score: float = 0.0
    title: str = ""
    artist: str = ""
    duration: Optional[int] = None
    release_title: Optional[str] = None
    release_country: Optional[str] = None
    release_date: Optional[str] = None


@dataclass
class MatchResult:
    """Result of the match stage for a single track."""

    success: bool = True
    metadata_id: str = ""
    best_recording_id: Optional[str] = None
    candidates_found: int = 0
    error: Optional[str] = None


def parse_acoustid_response(data: dict) -> list[AcoustIDCandidate]:
    """Parse an AcoustID API JSON response into a list of candidates."""
    if data.get("status") != "ok":
        return []

    results = data.get("results", [])
    candidates: list[AcoustIDCandidate] = []

    for result in results:
        score = result.get("score", 0.0)
        recordings = result.get("recordings", [])

        for rec in recordings:
            artist_name = ""
            artists = rec.get("artists", [])
            if artists:
                artist_name = artists[0].get("name", "")

            release_title = None
            release_country = None
            releases = rec.get("releases", [])
            if releases:
                release_title = releases[0].get("title")
                release_country = releases[0].get("country")

            candidates.append(AcoustIDCandidate(
                recording_id=rec.get("id", ""),
                acoustid_score=score,
                title=rec.get("title", ""),
                artist=artist_name,
                duration=rec.get("duration"),
                release_title=release_title,
                release_country=release_country,
            ))

    return candidates


def rank_candidates(
    candidates: list[AcoustIDCandidate],
    threshold: float = 0.0,
) -> list[AcoustIDCandidate]:
    """Rank candidates by score descending and filter by threshold."""
    filtered = [c for c in candidates if c.acoustid_score >= threshold]
    return sorted(filtered, key=lambda c: c.acoustid_score, reverse=True)


def fuzzy_match_score(
    query_artist: str,
    query_title: str,
    candidate_artist: str,
    candidate_title: str,
    query_duration: Optional[int] = None,
    candidate_duration: Optional[int] = None,
) -> float:
    """Compute a fuzzy match score between a query track and a candidate.

    Returns a float between 0.0 and 1.0.
    """
    # Normalize strings for comparison
    qa = _normalize(query_artist)
    qt = _normalize(query_title)
    ca = _normalize(candidate_artist)
    ct = _normalize(candidate_title)

    artist_sim = SequenceMatcher(None, qa, ca).ratio()
    title_sim = SequenceMatcher(None, qt, ct).ratio()

    # Weight title more heavily than artist
    score = artist_sim * 0.35 + title_sim * 0.50

    # Duration component
    if query_duration is not None and candidate_duration is not None:
        diff = abs(query_duration - candidate_duration)
        if diff <= 5:
            dur_score = 1.0
        elif diff <= 15:
            dur_score = 0.7
        elif diff <= 30:
            dur_score = 0.4
        else:
            dur_score = 0.1
        score += dur_score * 0.15
    else:
        # No duration info -- give partial credit
        score += 0.075

    return score


def run_match_for_track(
    conn: sqlite3.Connection,
    metadata_id: str,
    acoustid_api_key: str = "",
) -> MatchResult:
    """Run the fingerprint match stage for a single track.

    1. Look up fingerprint in track_sidecars
    2. Query AcoustID API (or mock)
    3. Parse and rank candidates
    4. Store external IDs for matches above threshold
    """
    log.debug("match: mid=%s", metadata_id)
    result = MatchResult(metadata_id=metadata_id)

    # Get fingerprint from DB
    row = conn.execute(
        "SELECT fingerprint FROM track_sidecars WHERE metadata_id = ?",
        (metadata_id,),
    ).fetchone()

    fingerprint = row["fingerprint"] if row else None

    if not fingerprint:
        log.debug("match: mid=%s no fingerprint, skipping", metadata_id)
        result.candidates_found = 0
        return result

    # Get track info for fuzzy matching fallback
    track_row = conn.execute(
        "SELECT title, artist, length_seconds FROM tracks WHERE metadata_id = ?",
        (metadata_id,),
    ).fetchone()

    # Lookup via AcoustID
    try:
        api_data = _lookup_acoustid(fingerprint, acoustid_api_key)
        candidates = parse_acoustid_response(api_data)
    except Exception as exc:
        result.error = str(exc)
        result.success = True  # not a fatal error
        result.candidates_found = 0
        return result

    ranked = rank_candidates(candidates, threshold=SCORE_THRESHOLD)
    result.candidates_found = len(ranked)

    if ranked:
        result.best_recording_id = ranked[0].recording_id

    # Store external IDs
    for candidate in ranked:
        if candidate.recording_id:
            conn.execute(
                """
                INSERT OR IGNORE INTO external_ids
                    (metadata_id, source, external_id, confidence)
                VALUES (?, ?, ?, ?)
                """,
                (
                    metadata_id,
                    "musicbrainz",
                    candidate.recording_id,
                    candidate.acoustid_score,
                ),
            )
        # Also store the acoustid reference
        conn.execute(
            """
            INSERT OR IGNORE INTO external_ids
                (metadata_id, source, external_id, confidence)
            VALUES (?, ?, ?, ?)
            """,
            (
                metadata_id,
                "acoustid",
                fingerprint[:64],  # truncated fingerprint as reference
                candidate.acoustid_score,
            ),
        )

    conn.commit()
    log.debug("match: mid=%s %d candidates, best=%s", metadata_id, result.candidates_found, result.best_recording_id)
    return result


def _lookup_acoustid(fingerprint: str, api_key: str) -> dict:
    """Query the AcoustID API. Tests mock this function."""
    params = {
        "client": api_key,
        "fingerprint": fingerprint,
        "duration": 0,
        "meta": "recordings+releases+compress",
    }
    resp = requests.get(ACOUSTID_API_URL, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _normalize(s: str) -> str:
    """Normalize a string for fuzzy comparison."""
    import re
    s = s.lower().strip()
    s = re.sub(r"[^\w\s]", "", s)
    s = re.sub(r"\s+", " ", s)
    return s