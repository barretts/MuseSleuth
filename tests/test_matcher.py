"""TDD tests for fingerprint matching and fuzzy fallback -- written before implementation."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from musesleuth.db import create_schema, generate_metadata_id
from musesleuth.matcher import (
    AcoustIDCandidate,
    parse_acoustid_response,
    rank_candidates,
    fuzzy_match_score,
    run_match_for_track,
    MatchResult,
    SCORE_THRESHOLD,
)


FIXTURES_DIR = Path(__file__).parent / "fixtures"


class TestParseAcoustIDResponse:
    """Tests for parsing AcoustID API JSON responses."""

    @pytest.fixture
    def response_data(self) -> dict:
        return json.loads((FIXTURES_DIR / "acoustid_response.json").read_text())

    @pytest.mark.unit
    def test_parses_candidates(self, response_data: dict) -> None:
        candidates = parse_acoustid_response(response_data)
        assert len(candidates) == 2

    @pytest.mark.unit
    def test_first_candidate_fields(self, response_data: dict) -> None:
        candidates = parse_acoustid_response(response_data)
        c = candidates[0]
        assert isinstance(c, AcoustIDCandidate)
        assert c.acoustid_score == 0.95
        assert c.recording_id == "rec-1111-2222-3333-444444444444"
        assert c.title == "Into The Inner Space"
        assert c.artist == "!Attention!"
        assert c.duration == 214

    @pytest.mark.unit
    def test_extracts_release_info(self, response_data: dict) -> None:
        candidates = parse_acoustid_response(response_data)
        c = candidates[0]
        assert c.release_title == "Dream Dance Vol. 18"
        assert c.release_country == "DE"

    @pytest.mark.unit
    def test_handles_empty_response(self) -> None:
        candidates = parse_acoustid_response({"status": "ok", "results": []})
        assert candidates == []

    @pytest.mark.unit
    def test_handles_error_response(self) -> None:
        candidates = parse_acoustid_response({"status": "error", "error": {"message": "bad key"}})
        assert candidates == []

    @pytest.mark.unit
    def test_handles_no_recordings(self) -> None:
        data = {"status": "ok", "results": [{"id": "x", "score": 0.5, "recordings": []}]}
        candidates = parse_acoustid_response(data)
        assert candidates == []


class TestRankCandidates:
    """Tests for candidate ranking logic."""

    @pytest.mark.unit
    def test_sorts_by_score_descending(self) -> None:
        candidates = [
            AcoustIDCandidate(recording_id="a", acoustid_score=0.7, title="X", artist="Y"),
            AcoustIDCandidate(recording_id="b", acoustid_score=0.95, title="X", artist="Y"),
            AcoustIDCandidate(recording_id="c", acoustid_score=0.8, title="X", artist="Y"),
        ]
        ranked = rank_candidates(candidates)
        assert [c.recording_id for c in ranked] == ["b", "c", "a"]

    @pytest.mark.unit
    def test_filters_below_threshold(self) -> None:
        candidates = [
            AcoustIDCandidate(recording_id="a", acoustid_score=0.3, title="X", artist="Y"),
            AcoustIDCandidate(recording_id="b", acoustid_score=0.95, title="X", artist="Y"),
        ]
        ranked = rank_candidates(candidates, threshold=SCORE_THRESHOLD)
        assert all(c.acoustid_score >= SCORE_THRESHOLD for c in ranked)

    @pytest.mark.unit
    def test_empty_input(self) -> None:
        assert rank_candidates([]) == []


class TestFuzzyMatchScore:
    """Tests for fuzzy metadata matching."""

    @pytest.mark.unit
    def test_exact_match_high_score(self) -> None:
        score = fuzzy_match_score(
            query_artist="!Attention!", query_title="Into The Inner Space",
            candidate_artist="!Attention!", candidate_title="Into The Inner Space",
            query_duration=214, candidate_duration=214,
        )
        assert score >= 0.9

    @pytest.mark.unit
    def test_different_track_low_score(self) -> None:
        score = fuzzy_match_score(
            query_artist="!Attention!", query_title="Into The Inner Space",
            candidate_artist="Scooter", candidate_title="Hyper Hyper",
            query_duration=214, candidate_duration=300,
        )
        assert score < 0.5

    @pytest.mark.unit
    def test_duration_tolerance(self) -> None:
        """Within 5s should not penalize much."""
        score_close = fuzzy_match_score(
            query_artist="A", query_title="B",
            candidate_artist="A", candidate_title="B",
            query_duration=200, candidate_duration=203,
        )
        score_far = fuzzy_match_score(
            query_artist="A", query_title="B",
            candidate_artist="A", candidate_title="B",
            query_duration=200, candidate_duration=260,
        )
        assert score_close > score_far

    @pytest.mark.unit
    def test_case_insensitive(self) -> None:
        score = fuzzy_match_score(
            query_artist="attention", query_title="into the inner space",
            candidate_artist="!Attention!", candidate_title="Into The Inner Space",
            query_duration=214, candidate_duration=214,
        )
        assert score >= 0.7

    @pytest.mark.unit
    def test_none_duration_still_works(self) -> None:
        score = fuzzy_match_score(
            query_artist="A", query_title="B",
            candidate_artist="A", candidate_title="B",
        )
        assert isinstance(score, float)


def _seed_track(conn: sqlite3.Connection, mid: str, full_path: str,
                title: str = "", artist: str = "") -> None:
    conn.execute(
        "INSERT INTO tracks (metadata_id, title, artist, file_path, filename, full_path) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (mid, title, artist, str(Path(full_path).parent), Path(full_path).name, full_path),
    )
    conn.execute(
        "INSERT INTO jobs (metadata_id, stage, status) VALUES (?, 'fingerprint_match', 'pending')",
        (mid,),
    )
    conn.commit()


@pytest.fixture
def db(in_memory_db: sqlite3.Connection) -> sqlite3.Connection:
    create_schema(in_memory_db)
    return in_memory_db


class TestRunMatchForTrack:
    """Tests for the full match stage orchestration."""

    @pytest.mark.integration
    def test_stores_external_ids(self, db: sqlite3.Connection) -> None:
        mid = generate_metadata_id()
        _seed_track(db, mid, "C:\\test\\song.mp3", "Into The Inner Space", "!Attention!")
        # Seed a fingerprint in sidecars
        db.execute(
            "INSERT INTO track_sidecars (metadata_id, sidecar_path, fingerprint) VALUES (?, ?, ?)",
            (mid, "C:\\test\\song.mp3.dlpmeta", "AQABz0qUkZ..."),
        )
        db.commit()

        fixture = json.loads((FIXTURES_DIR / "acoustid_response.json").read_text())
        with patch("musesleuth.matcher._lookup_acoustid") as mock:
            mock.return_value = fixture
            result = run_match_for_track(db, mid)

        assert isinstance(result, MatchResult)
        assert result.success is True

        rows = db.execute(
            "SELECT source, external_id, confidence FROM external_ids WHERE metadata_id = ?",
            (mid,),
        ).fetchall()
        assert len(rows) >= 1
        sources = {r["source"] for r in rows}
        assert "acoustid" in sources or "musicbrainz" in sources

    @pytest.mark.integration
    def test_handles_no_fingerprint(self, db: sqlite3.Connection) -> None:
        mid = generate_metadata_id()
        _seed_track(db, mid, "C:\\test\\song.mp3", "Test", "Artist")

        result = run_match_for_track(db, mid)
        assert isinstance(result, MatchResult)
        # Should still succeed (just no matches found)
        assert result.candidates_found == 0

    @pytest.mark.integration
    def test_stores_best_match(self, db: sqlite3.Connection) -> None:
        mid = generate_metadata_id()
        _seed_track(db, mid, "C:\\test\\song.mp3", "Into The Inner Space", "!Attention!")
        db.execute(
            "INSERT INTO track_sidecars (metadata_id, sidecar_path, fingerprint) VALUES (?, ?, ?)",
            (mid, "C:\\test\\song.mp3.dlpmeta", "AQABz0qUkZ..."),
        )
        db.commit()

        fixture = json.loads((FIXTURES_DIR / "acoustid_response.json").read_text())
        with patch("musesleuth.matcher._lookup_acoustid") as mock:
            mock.return_value = fixture
            result = run_match_for_track(db, mid)

        assert result.best_recording_id is not None
        assert result.candidates_found >= 1
