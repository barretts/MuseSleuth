"""Tests for staged Opus migration helpers and CLI wiring."""
from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from musesleuth.cli import cli
from musesleuth.db import create_schema, get_connection
from musesleuth.opus_migration import backup_database, export_track_inventory, plan_tasks


def _insert_track(conn, metadata_id: str, full_path: Path) -> None:
    conn.execute(
        """
        INSERT INTO tracks (metadata_id, title, artist, file_path, filename, full_path, file_size)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            metadata_id,
            "Song",
            "Artist",
            str(full_path.parent) + "\\",
            full_path.name,
            str(full_path),
            str(full_path.stat().st_size),
        ),
    )
    conn.commit()


@pytest.mark.integration
def test_plan_tasks_only_non_opus_in_source_root(tmp_dir: Path) -> None:
    source_root = tmp_dir / "source"
    target_root = tmp_dir / "target"
    source_root.mkdir(parents=True)

    mp3 = source_root / "A" / "song.mp3"
    opus = source_root / "A" / "song2.opus"
    outside = tmp_dir / "outside.mp3"
    mp3.parent.mkdir(parents=True)
    mp3.write_bytes(b"abc123")
    opus.write_bytes(b"abc123")
    outside.write_bytes(b"abc123")

    db_path = tmp_dir / "music.db"
    conn = get_connection(db_path)
    create_schema(conn)
    _insert_track(conn, "01HXTEST000000000000000001", mp3)
    _insert_track(conn, "01HXTEST000000000000000002", opus)
    _insert_track(conn, "01HXTEST000000000000000003", outside)

    tasks = plan_tasks(conn, source_root, target_root)
    conn.close()

    assert len(tasks) == 1
    assert tasks[0].source_path == mp3.resolve()
    assert tasks[0].target_path == (target_root / "A" / "song.opus").resolve()


@pytest.mark.integration
def test_plan_tasks_without_source_root_uses_inplace_targets(tmp_dir: Path) -> None:
    lib_dir = tmp_dir / "lib"
    lib_dir.mkdir(parents=True)
    mp3 = lib_dir / "x.mp3"
    mp3.write_bytes(b"abc123")

    db_path = tmp_dir / "music.db"
    conn = get_connection(db_path)
    create_schema(conn)
    _insert_track(conn, "01HXTEST000000000000000004", mp3)

    tasks = plan_tasks(conn, None, None)
    conn.close()
    assert len(tasks) == 1
    assert tasks[0].target_path == mp3.with_suffix(".opus").resolve()


@pytest.mark.integration
def test_backup_and_inventory_export(tmp_dir: Path) -> None:
    db_path = tmp_dir / "music.db"
    conn = get_connection(db_path)
    create_schema(conn)

    song = tmp_dir / "lib" / "x.mp3"
    song.parent.mkdir(parents=True)
    song.write_bytes(b"abcdef")
    _insert_track(conn, "01HXTEST000000000000000011", song)

    inventory = tmp_dir / "inventory.csv"
    row_count = export_track_inventory(conn, inventory)
    conn.close()

    backup_file = backup_database(db_path, tmp_dir / "backups")
    assert row_count == 1
    assert inventory.exists()
    assert backup_file.exists()


@pytest.mark.cli
def test_cli_opus_pilot_dry_run_writes_manifest(tmp_dir: Path) -> None:
    source_root = tmp_dir / "source"
    target_root = tmp_dir / "target"
    source_root.mkdir(parents=True)
    song = source_root / "DJ" / "track.mp3"
    song.parent.mkdir(parents=True)
    song.write_bytes(b"abc123")

    db_path = tmp_dir / "music.db"
    conn = get_connection(db_path)
    create_schema(conn)
    _insert_track(conn, "01HXTEST000000000000000021", song)
    conn.close()

    manifest = tmp_dir / "pilot.csv"
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "opus",
            "pilot",
            "--db",
            str(db_path),
            "--source-root",
            str(source_root),
            "--target-root",
            str(target_root),
            "--manifest",
            str(manifest),
            "--limit",
            "50",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert manifest.exists()
    assert "Planned 1 pilot conversion" in result.output


@pytest.mark.cli
def test_cli_opus_pilot_defaults_to_source_root_for_target_and_manifest(tmp_dir: Path) -> None:
    source_root = tmp_dir / "source"
    source_root.mkdir(parents=True)
    song = source_root / "DJ" / "track.mp3"
    song.parent.mkdir(parents=True)
    song.write_bytes(b"abc123")

    db_path = tmp_dir / "music.db"
    conn = get_connection(db_path)
    create_schema(conn)
    _insert_track(conn, "01HXTEST000000000000000031", song)
    conn.close()

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "opus",
            "pilot",
            "--db",
            str(db_path),
            "--source-root",
            str(source_root),
            "--limit",
            "10",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    default_manifest = source_root / "opus-pilot-manifest.csv"
    assert default_manifest.exists()
    assert str(default_manifest) in result.output


@pytest.mark.cli
def test_cli_opus_batch_rollout_without_source_root_works(tmp_dir: Path) -> None:
    song = tmp_dir / "music" / "track.mp3"
    song.parent.mkdir(parents=True)
    song.write_bytes(b"abc123")

    db_path = tmp_dir / "music.db"
    conn = get_connection(db_path)
    create_schema(conn)
    _insert_track(conn, "01HXTEST000000000000000041", song)
    conn.close()

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "opus",
            "batch-rollout",
            "--db",
            str(db_path),
            "--batch-size",
            "10",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Planned 1 conversion" in result.output
