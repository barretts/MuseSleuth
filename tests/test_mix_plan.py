"""TDD tests for mix plan generation (Phase 11) -- written before implementation."""
from __future__ import annotations

import json
import sqlite3

import pytest

from musesleuth.db import create_schema, generate_metadata_id
from tests.conftest import insert_dummy_track


def _seed_track_full(
    conn: sqlite3.Connection,
    mid: str,
    bpm: float,
    camelot: str,
    energy: float,
    lufs: float = -8.0,
    intro_end: float = 16.0,
    outro_start: float = 180.0,
    outro_end: float = 210.0,
) -> None:
    """Insert a track with features and structure segments for mix plan tests."""
    insert_dummy_track(conn, mid)
    conn.execute(
        "INSERT INTO musical_features (metadata_id, bpm_final, energy) VALUES (?, ?, ?)",
        (mid, bpm, energy),
    )
    conn.execute(
        "INSERT INTO playlist_signals (metadata_id, camelot_key, quality_verdict) VALUES (?, ?, 'pass')",
        (mid, camelot),
    )
    conn.execute(
        "INSERT INTO loudness_features (metadata_id, lufs_integrated, analyzed_at) VALUES (?, ?, datetime('now'))",
        (mid, lufs),
    )
    # intro segment
    conn.execute(
        """INSERT INTO structure_segments
            (metadata_id, segment_idx, start_s, end_s, kind, confidence, analyzed_at)
        VALUES (?, 0, 0.0, ?, 'intro', 0.8, datetime('now'))""",
        (mid, intro_end),
    )
    # outro segment
    conn.execute(
        """INSERT INTO structure_segments
            (metadata_id, segment_idx, start_s, end_s, kind, confidence, analyzed_at)
        VALUES (?, 1, ?, ?, 'outro', 0.8, datetime('now'))""",
        (mid, outro_start, outro_end),
    )
    conn.commit()


@pytest.fixture
def db(in_memory_db: sqlite3.Connection) -> sqlite3.Connection:
    create_schema(in_memory_db)
    return in_memory_db


class TestMixPlanEntry:
    """Tests for individual mix plan entry computation."""

    @pytest.mark.unit
    def test_mix_plan_entry_fields(self) -> None:
        """MixPlanEntry has expected fields."""
        from musesleuth.mix_plan import MixPlanEntry

        entry = MixPlanEntry(
            metadata_id="abc",
            position=0,
            cue_in_s=0.0,
            cue_out_s=180.0,
            transition_type="blend",
            transition_cost=0.5,
            bpm=128.0,
            camelot_key="8A",
        )
        assert entry.metadata_id == "abc"
        assert entry.cue_in_s == 0.0
        assert entry.cue_out_s == 180.0
        assert entry.transition_type == "blend"

    @pytest.mark.unit
    def test_mix_plan_entry_defaults(self) -> None:
        """MixPlanEntry works with minimal required fields."""
        from musesleuth.mix_plan import MixPlanEntry

        entry = MixPlanEntry(metadata_id="xyz", position=0)
        assert entry.cue_in_s is None
        assert entry.cue_out_s is None
        assert entry.transition_type == "cut"


class TestGenerateMixPlan:
    """Tests for generating a full mix plan from a playlist."""

    @pytest.mark.integration
    def test_generate_mix_plan_basic(self, db: sqlite3.Connection) -> None:
        """Mix plan produces one entry per track with cue points."""
        from musesleuth.mix_plan import generate_mix_plan

        mids = [generate_metadata_id() for _ in range(3)]
        for i, mid in enumerate(mids):
            _seed_track_full(db, mid, 128.0 + i, "8A", 0.7)

        plan = generate_mix_plan(db, mids)
        assert len(plan) == 3
        for i, entry in enumerate(plan):
            assert entry.position == i
            assert entry.metadata_id == mids[i]

    @pytest.mark.integration
    def test_mix_plan_cue_points_from_structure(self, db: sqlite3.Connection) -> None:
        """Cue-in uses intro end, cue-out uses outro start from structure segments."""
        from musesleuth.mix_plan import generate_mix_plan

        mid = generate_metadata_id()
        _seed_track_full(db, mid, 128.0, "8A", 0.7, intro_end=16.0, outro_start=180.0, outro_end=210.0)

        plan = generate_mix_plan(db, [mid])
        assert len(plan) == 1
        # Should use structure data for cue points
        assert plan[0].cue_in_s is not None
        assert plan[0].cue_out_s is not None

    @pytest.mark.integration
    def test_mix_plan_transition_types(self, db: sqlite3.Connection) -> None:
        """Adjacent tracks get transition type annotations."""
        from musesleuth.mix_plan import generate_mix_plan

        m1 = generate_metadata_id()
        m2 = generate_metadata_id()
        _seed_track_full(db, m1, 128.0, "8A", 0.7)
        _seed_track_full(db, m2, 128.0, "8B", 0.7)

        plan = generate_mix_plan(db, [m1, m2])
        assert len(plan) == 2
        # First track: no incoming transition
        assert plan[0].transition_type in ("cut", "blend", "none")
        # Second track should have a transition annotation
        assert plan[1].transition_type in ("cut", "blend")

    @pytest.mark.integration
    def test_mix_plan_snaps_cues_to_phrase_boundaries(self, db: sqlite3.Connection) -> None:
        """Structure cues should snap to beatgrid phrase boundaries when available."""
        from musesleuth.mix_plan import generate_mix_plan

        mid = generate_metadata_id()
        _seed_track_full(db, mid, 128.0, "8A", 0.7, intro_end=15.2, outro_start=181.3, outro_end=210.0)

        db.execute(
            """
            INSERT INTO beat_grids
                (metadata_id, phrase_boundaries_json, analyzed_at)
            VALUES (?, ?, datetime('now'))
            """,
            (mid, json.dumps([0.0, 16.0, 32.0, 176.0, 192.0])),
        )
        db.commit()

        plan = generate_mix_plan(db, [mid])
        assert len(plan) == 1
        assert plan[0].cue_in_s == pytest.approx(16.0)
        assert plan[0].cue_out_s == pytest.approx(176.0)

    @pytest.mark.integration
    def test_mix_plan_empty(self, db: sqlite3.Connection) -> None:
        """Empty track list produces empty mix plan."""
        from musesleuth.mix_plan import generate_mix_plan

        plan = generate_mix_plan(db, [])
        assert plan == []

    @pytest.mark.integration
    def test_mix_plan_single_track(self, db: sqlite3.Connection) -> None:
        """Single track produces a plan with one entry."""
        from musesleuth.mix_plan import generate_mix_plan

        mid = generate_metadata_id()
        _seed_track_full(db, mid, 128.0, "8A", 0.7)

        plan = generate_mix_plan(db, [mid])
        assert len(plan) == 1
        assert plan[0].transition_cost == 0.0


class TestMixPlanSerialization:
    """Tests for serializing mix plan to JSON."""

    @pytest.mark.unit
    def test_serialize_mix_plan(self) -> None:
        """Mix plan serializes to a valid JSON list."""
        from musesleuth.mix_plan import MixPlanEntry, serialize_mix_plan

        plan = [
            MixPlanEntry(metadata_id="a", position=0, cue_in_s=0.0, cue_out_s=180.0,
                         transition_type="none", bpm=128.0, camelot_key="8A"),
            MixPlanEntry(metadata_id="b", position=1, cue_in_s=16.0, cue_out_s=200.0,
                         transition_type="blend", transition_cost=0.3, bpm=130.0, camelot_key="8B"),
        ]
        result = serialize_mix_plan(plan)
        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert len(parsed) == 2
        assert parsed[0]["metadata_id"] == "a"
        assert parsed[1]["transition_type"] == "blend"

    @pytest.mark.unit
    def test_serialize_empty_plan(self) -> None:
        """Empty plan serializes to '[]'."""
        from musesleuth.mix_plan import serialize_mix_plan

        assert json.loads(serialize_mix_plan([])) == []
