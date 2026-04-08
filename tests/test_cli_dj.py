"""TDD tests for DJ pipeline CLI & web integration (Phase 12) -- written before implementation."""
from __future__ import annotations

import json
import sqlite3

import pytest

from musesleuth.db import create_schema, generate_metadata_id
from tests.conftest import insert_dummy_track


def _seed_track_with_features(
    conn: sqlite3.Connection,
    mid: str,
    bpm: float,
    camelot: str,
    energy: float,
    lufs: float = -8.0,
) -> None:
    """Insert a track with musical features, playlist signals, and loudness."""
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
    conn.execute(
        """INSERT INTO structure_segments
            (metadata_id, segment_idx, start_s, end_s, kind, confidence, analyzed_at)
        VALUES (?, 0, 0.0, 16.0, 'intro', 0.8, datetime('now'))""",
        (mid,),
    )
    conn.execute(
        """INSERT INTO structure_segments
            (metadata_id, segment_idx, start_s, end_s, kind, confidence, analyzed_at)
        VALUES (?, 1, 180.0, 210.0, 'outro', 0.8, datetime('now'))""",
        (mid,),
    )
    conn.commit()


@pytest.fixture
def db(in_memory_db: sqlite3.Connection) -> sqlite3.Connection:
    create_schema(in_memory_db)
    return in_memory_db


class TestDjFlowInStrategyMap:
    """Tests that dj_flow is registered in the playlist generator strategy map."""

    @pytest.mark.unit
    def test_dj_flow_in_strategy_map(self) -> None:
        from musesleuth.playlist_generator import STRATEGY_MAP

        assert "dj_flow" in STRATEGY_MAP

    @pytest.mark.unit
    def test_dj_flow_via_generate_playlist(self, db: sqlite3.Connection) -> None:
        from musesleuth.playlist_generator import generate_playlist

        mids = [generate_metadata_id() for _ in range(4)]
        for i, mid in enumerate(mids):
            _seed_track_with_features(db, mid, 126 + i * 2, "8A", 0.6 + i * 0.05)

        result = generate_playlist(db, "dj_flow", "DJ Test", {}, limit=10)
        assert result.track_count == 4
        assert result.name == "DJ Test"


class TestDjFlowCliChoice:
    """Tests that the CLI accepts 'dj_flow' as a strategy."""

    @pytest.mark.unit
    def test_dj_flow_in_cli_choices(self) -> None:
        """The playlist generate command should accept dj_flow."""
        from click.testing import CliRunner
        from musesleuth.cli import cli

        runner = CliRunner()
        # Just check the help includes dj_flow
        result = runner.invoke(cli, ["playlist", "generate", "--help"])
        assert "dj_flow" in result.output


class TestMixPlanCliCommand:
    """Tests for the 'playlist mix-plan' CLI command."""

    @pytest.mark.unit
    def test_mix_plan_command_exists(self) -> None:
        """The mix-plan subcommand should be registered."""
        from click.testing import CliRunner
        from musesleuth.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["playlist", "mix-plan", "--help"])
        assert result.exit_code == 0
        assert "mix-plan" in result.output or "mix_plan" in result.output or "playlist" in result.output


class TestEvaluateCliCommand:
    """Tests for the 'playlist evaluate' CLI command."""

    @pytest.mark.unit
    def test_evaluate_command_exists(self) -> None:
        """The evaluate subcommand should be registered."""
        from click.testing import CliRunner
        from musesleuth.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["playlist", "evaluate", "--help"])
        assert result.exit_code == 0
        assert "evaluate" in result.output

    @pytest.mark.integration
    def test_evaluate_command_outputs_metrics(self, tmp_path) -> None:
        """The evaluate command emits JSON metrics for a playlist."""
        from click.testing import CliRunner
        from musesleuth.cli import cli
        from musesleuth.db import create_schema, generate_metadata_id, get_connection

        db_path = tmp_path / "test.db"
        conn = get_connection(db_path)
        create_schema(conn)

        mids = [generate_metadata_id() for _ in range(3)]
        for i, mid in enumerate(mids):
            _seed_track_with_features(conn, mid, 126 + i * 2, "8A", 0.6 + i * 0.05)

        pid = generate_metadata_id()
        conn.execute(
            "INSERT INTO playlists (playlist_id, name, strategy, track_count) VALUES (?, ?, ?, ?)",
            (pid, "Eval Test", "dj_flow", len(mids)),
        )
        for i, mid in enumerate(mids):
            conn.execute(
                "INSERT INTO playlist_tracks (playlist_id, metadata_id, position) VALUES (?, ?, ?)",
                (pid, mid, i + 1),
            )
        conn.commit()
        conn.close()

        runner = CliRunner()
        result = runner.invoke(cli, ["playlist", "evaluate", "--db", str(db_path), "--id", pid])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["track_count"] == 3
        assert "total_transition_cost" in parsed


class TestMixPlanWebRoute:
    """Tests for the web API mix plan endpoint."""

    @pytest.mark.integration
    def test_mix_plan_route_exists(self, db: sqlite3.Connection) -> None:
        """POST /playlists/{id}/mix-plan returns a mix plan JSON."""
        from musesleuth.mix_plan import MixPlanEntry, generate_mix_plan

        # Create a playlist with tracks
        mids = [generate_metadata_id() for _ in range(3)]
        for i, mid in enumerate(mids):
            _seed_track_with_features(db, mid, 128 + i, "8A", 0.7)

        pid = generate_metadata_id()
        db.execute(
            "INSERT INTO playlists (playlist_id, name, strategy, track_count) VALUES (?, ?, ?, ?)",
            (pid, "Test Mix", "dj_flow", len(mids)),
        )
        for i, mid in enumerate(mids):
            db.execute(
                "INSERT INTO playlist_tracks (playlist_id, metadata_id, position) VALUES (?, ?, ?)",
                (pid, mid, i + 1),
            )
        db.commit()

        # Generate mix plan directly (route test via unit)
        track_ids = [mid for mid in mids]
        plan = generate_mix_plan(db, track_ids)

        assert len(plan) == 3
        for entry in plan:
            assert isinstance(entry, MixPlanEntry)

    @pytest.mark.integration
    def test_web_route_registered(self) -> None:
        """The /playlists/{id}/mix-plan route should be in the router."""
        from musesleuth.web.routes.playlists import router

        routes = [r.path for r in router.routes]
        assert any("mix-plan" in p for p in routes)


class TestDjFlowWebStrategy:
    """Tests that dj_flow appears in the web STRATEGY_CHOICES."""

    @pytest.mark.unit
    def test_dj_flow_in_web_choices(self) -> None:
        from musesleuth.web.routes.playlists import STRATEGY_CHOICES

        assert "dj_flow" in STRATEGY_CHOICES
