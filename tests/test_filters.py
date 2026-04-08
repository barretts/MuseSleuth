"""TDD tests for semantic filtration (Phase 7) -- written before implementation."""
from __future__ import annotations

import sqlite3

import pytest

from musesleuth.db import create_schema, generate_metadata_id
from tests.conftest import insert_dummy_track


class TestTitleFilters:
    """Tests for title-based heuristic filters (live, demo, spoken intro, etc.)."""

    @pytest.mark.unit
    def test_detect_live_recording(self) -> None:
        from musesleuth.filters import classify_title

        v = classify_title("Sandstorm (Live at Tomorrowland 2019)")
        assert "live" in v.tags

    @pytest.mark.unit
    def test_detect_demo_version(self) -> None:
        from musesleuth.filters import classify_title

        v = classify_title("Levels (Demo Version)")
        assert "demo" in v.tags

    @pytest.mark.unit
    def test_detect_acoustic(self) -> None:
        from musesleuth.filters import classify_title

        v = classify_title("Strobe (Acoustic Version)")
        assert "acoustic" in v.tags

    @pytest.mark.unit
    def test_detect_instrumental(self) -> None:
        from musesleuth.filters import classify_title

        v = classify_title("Ghosts N Stuff (Instrumental)")
        assert "instrumental" in v.tags

    @pytest.mark.unit
    def test_clean_title_no_tags(self) -> None:
        from musesleuth.filters import classify_title

        v = classify_title("Normal Song Title")
        assert v.tags == []

    @pytest.mark.unit
    def test_detect_intro_spoken(self) -> None:
        from musesleuth.filters import classify_title

        v = classify_title("DJ Intro (Spoken Word)")
        assert "spoken" in v.tags

    @pytest.mark.unit
    def test_detect_skit(self) -> None:
        from musesleuth.filters import classify_title

        v = classify_title("Intermission Skit")
        assert "skit" in v.tags

    @pytest.mark.unit
    def test_multiple_tags(self) -> None:
        from musesleuth.filters import classify_title

        v = classify_title("Song (Live Acoustic Demo)")
        assert "live" in v.tags
        assert "acoustic" in v.tags
        assert "demo" in v.tags


class TestQualityVerdict:
    """Tests for quality verdict computation combining loudness + metadata signals."""

    @pytest.mark.unit
    def test_verdict_pass_normal(self) -> None:
        from musesleuth.filters import compute_quality_verdict

        verdict = compute_quality_verdict(
            lufs_i=-8.0, lra=6.0, title_tags=[], clipping_ratio=0.001
        )
        assert verdict == "pass"

    @pytest.mark.unit
    def test_verdict_demo(self) -> None:
        from musesleuth.filters import compute_quality_verdict

        verdict = compute_quality_verdict(
            lufs_i=-24.0, lra=15.0, title_tags=["demo"], clipping_ratio=0.0
        )
        assert verdict == "demo"

    @pytest.mark.unit
    def test_verdict_live(self) -> None:
        from musesleuth.filters import compute_quality_verdict

        verdict = compute_quality_verdict(
            lufs_i=-10.0, lra=8.0, title_tags=["live"], clipping_ratio=0.0
        )
        assert verdict == "live"

    @pytest.mark.unit
    def test_verdict_clipped(self) -> None:
        from musesleuth.filters import compute_quality_verdict

        verdict = compute_quality_verdict(
            lufs_i=-6.0, lra=4.0, title_tags=[], clipping_ratio=0.05
        )
        assert verdict == "clipped"

    @pytest.mark.unit
    def test_verdict_skit(self) -> None:
        from musesleuth.filters import compute_quality_verdict

        verdict = compute_quality_verdict(
            lufs_i=-10.0, lra=5.0, title_tags=["skit"], clipping_ratio=0.0
        )
        assert verdict == "skit"

    @pytest.mark.unit
    def test_verdict_spoken(self) -> None:
        from musesleuth.filters import compute_quality_verdict

        verdict = compute_quality_verdict(
            lufs_i=-10.0, lra=5.0, title_tags=["spoken"], clipping_ratio=0.0
        )
        assert verdict == "spoken"

    @pytest.mark.unit
    def test_verdict_none_lufs_passes_if_no_flags(self) -> None:
        from musesleuth.filters import compute_quality_verdict

        verdict = compute_quality_verdict(
            lufs_i=None, lra=None, title_tags=[], clipping_ratio=None
        )
        assert verdict == "pass"


class TestRunFiltersForTrack:
    """Tests for DB runner that computes and persists quality_verdict."""

    @pytest.mark.integration
    def test_run_filters_writes_verdict(self, in_memory_db: sqlite3.Connection) -> None:
        from musesleuth.filters import run_filters_for_track

        create_schema(in_memory_db)
        mid = generate_metadata_id()
        insert_dummy_track(in_memory_db, mid)
        in_memory_db.execute(
            "UPDATE tracks SET title = 'Normal Song' WHERE metadata_id = ?", (mid,)
        )
        in_memory_db.execute(
            "INSERT INTO playlist_signals (metadata_id) VALUES (?)", (mid,)
        )
        in_memory_db.commit()

        run_filters_for_track(in_memory_db, mid)

        row = in_memory_db.execute(
            "SELECT quality_verdict FROM playlist_signals WHERE metadata_id = ?",
            (mid,),
        ).fetchone()
        assert row is not None
        assert row["quality_verdict"] is not None

    @pytest.mark.integration
    def test_run_filters_live_track(self, in_memory_db: sqlite3.Connection) -> None:
        from musesleuth.filters import run_filters_for_track

        create_schema(in_memory_db)
        mid = generate_metadata_id()
        insert_dummy_track(in_memory_db, mid)
        in_memory_db.execute(
            "UPDATE tracks SET title = 'Song (Live at Wembley)' WHERE metadata_id = ?",
            (mid,),
        )
        in_memory_db.execute(
            "INSERT INTO playlist_signals (metadata_id) VALUES (?)", (mid,)
        )
        in_memory_db.commit()

        run_filters_for_track(in_memory_db, mid)

        row = in_memory_db.execute(
            "SELECT quality_verdict FROM playlist_signals WHERE metadata_id = ?",
            (mid,),
        ).fetchone()
        assert row["quality_verdict"] == "live"

    @pytest.mark.integration
    def test_run_filters_demo_from_loudness(
        self, in_memory_db: sqlite3.Connection
    ) -> None:
        from musesleuth.filters import run_filters_for_track

        create_schema(in_memory_db)
        mid = generate_metadata_id()
        insert_dummy_track(in_memory_db, mid)
        in_memory_db.execute(
            "UPDATE tracks SET title = 'Song' WHERE metadata_id = ?", (mid,)
        )
        in_memory_db.execute(
            "INSERT INTO playlist_signals (metadata_id) VALUES (?)", (mid,)
        )
        in_memory_db.execute(
            """INSERT INTO loudness_features
                (metadata_id, lufs_integrated, lra, analyzed_at)
            VALUES (?, -24.0, 15.0, datetime('now'))""",
            (mid,),
        )
        in_memory_db.commit()

        run_filters_for_track(in_memory_db, mid)

        row = in_memory_db.execute(
            "SELECT quality_verdict FROM playlist_signals WHERE metadata_id = ?",
            (mid,),
        ).fetchone()
        assert row["quality_verdict"] == "demo"
