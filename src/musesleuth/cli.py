"""MuseSleuth CLI entry points."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import click

from musesleuth.csv_parser import parse_csv_file
from musesleuth.db import create_schema, generate_metadata_id, get_connection
from musesleuth.db.search import index_track
from musesleuth.filename_parser import apply_filename_fallback
from musesleuth.job_queue import create_jobs_for_track, backfill_missing_jobs, STAGES, get_job_counts, retry_failed_jobs, refresh_incomplete_enrich, refresh_stage
from musesleuth.opus_migration import (
    backup_database,
    export_track_inventory,
    plan_tasks,
    reconcile_db_to_opus,
    regenerate_opus_sidecars,
    run_conversion_batch,
)
from musesleuth.pipeline import PipelineOrchestrator
from musesleuth.sidecar import SidecarData, write_sidecar


@click.group()
def cli() -> None:
    """MuseSleuth -- music metadata enrichment pipeline."""
    # Configure logging for all musesleuth modules.
    # Default to WARNING; the 'run' command bumps to INFO (or DEBUG with -v).
    logging.basicConfig(
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        level=logging.WARNING,
        stream=sys.stderr,
    )


@cli.command(name="import")
@click.argument("csv_path", type=click.Path(exists=False))
@click.option("--db", "db_path", required=True, type=click.Path(), help="SQLite database path.")
@click.option("--skip-sidecars", is_flag=True, default=False, help="Skip writing sidecar files.")
def import_cmd(csv_path: str, db_path: str, skip_sidecars: bool) -> None:
    """Import tracks from an mp3tag CSV export into the database."""
    csv_file = Path(csv_path)
    if not csv_file.exists():
        raise click.ClickException(f"CSV file not found: {csv_file}")

    db_file = Path(db_path)
    conn = get_connection(db_file)
    create_schema(conn)

    rows = parse_csv_file(csv_file, deduplicate=True)
    imported = 0
    skipped = 0

    for row in rows:
        file_path = row.get("Path", "")
        filename = row.get("Filename", "")
        full_path = file_path + filename

        if not full_path:
            skipped += 1
            continue

        # Check if already imported (idempotent)
        existing = conn.execute(
            "SELECT metadata_id FROM tracks WHERE full_path = ?",
            (full_path,),
        ).fetchone()

        if existing is not None:
            skipped += 1
            continue

        metadata_id = generate_metadata_id()

        apply_filename_fallback(row)

        conn.execute(
            """
            INSERT INTO tracks
                (metadata_id, title, artist, album, track_number, year,
                 length_seconds, file_size, last_modified, file_path, filename, full_path)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                metadata_id,
                row.get("Title", ""),
                row.get("Artist", ""),
                row.get("Album", ""),
                row.get("Track", ""),
                row.get("Year", ""),
                row.get("Length", ""),
                row.get("Size", ""),
                row.get("Last Modified", ""),
                file_path,
                filename,
                full_path,
            ),
        )
        index_track(conn, metadata_id)
        conn.commit()

        create_jobs_for_track(conn, metadata_id)

        # Mark import job as done since we just imported the track
        conn.execute(
            """
            UPDATE jobs SET status = 'done', completed_at = datetime('now'), updated_at = datetime('now')
            WHERE metadata_id = ? AND stage = 'import'
            """,
            (metadata_id,),
        )
        conn.commit()

        if not skip_sidecars:
            audio_path = Path(full_path)
            if audio_path.parent.exists():
                try:
                    sc_data = SidecarData(
                        metadata_id=metadata_id,
                        path=full_path,
                        size=0,
                        mtime=0.0,
                    )
                    write_sidecar(audio_path, sc_data)

                    sc_path = str(audio_path) + ".dlpmeta"
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO track_sidecars
                            (metadata_id, sidecar_path)
                        VALUES (?, ?)
                        """,
                        (metadata_id, sc_path),
                    )
                    conn.commit()
                except OSError:
                    pass

        imported += 1

    conn.close()
    click.echo(f"Imported {imported} tracks, skipped {skipped} duplicates.")


@cli.command()
@click.option("--db", "db_path", required=True, type=click.Path(), help="SQLite database path.")
def status(db_path: str) -> None:
    """Show pipeline progress and job status summary."""
    db_file = Path(db_path)
    if not db_file.exists():
        raise click.ClickException(f"Database not found: {db_file}")

    conn = get_connection(db_file)
    orch = PipelineOrchestrator(conn)
    stats = orch.get_stats()

    click.echo(f"Tracks: {stats.total_tracks}")
    click.echo(f"Jobs:   {stats.total_jobs}  "
               f"(Pending: {stats.pending_jobs}, Running: {stats.running_jobs}, "
               f"Done: {stats.done_jobs}, Failed: {stats.failed_jobs})")
    click.echo(f"Completion: {stats.completion_pct:.1f}%")

    stage_counts = get_job_counts(conn)
    if stage_counts:
        click.echo("\nPer-stage breakdown:")
        for stage in STAGES:
            counts = stage_counts.get(stage, {})
            parts = [f"{s}: {c}" for s, c in sorted(counts.items())]
            click.echo(f"  {stage:20s}  {', '.join(parts) if parts else '-'}")

    conn.close()


@cli.command(name="backfill-jobs")
@click.option("--db", "db_path", required=True, type=click.Path(), help="SQLite database path.")
@click.option("--stage", "stages", multiple=True, type=click.Choice(STAGES, case_sensitive=False),
              help="Only backfill these stages (repeatable). Default: all stages.")
def backfill_jobs_cmd(db_path: str, stages: tuple[str, ...]) -> None:
    """Seed missing job rows for existing tracks.

    Use this after adding new pipeline stages to an existing database.
    """
    db_file = Path(db_path)
    if not db_file.exists():
        raise click.ClickException(f"Database not found: {db_file}")

    conn = get_connection(db_file)
    create_schema(conn)
    stage_list = list(stages) if stages else None
    count = backfill_missing_jobs(conn, stages=stage_list)
    conn.close()
    click.echo(f"Backfilled {count} new job(s).")


@cli.command()
@click.option("--db", "db_path", required=True, type=click.Path(), help="SQLite database path.")
@click.option("--stage", "stage", default=None, type=click.Choice(STAGES, case_sensitive=False),
              help="Only process jobs for this stage.")
@click.option("--workers", "workers", default=1, type=int,
              help="Number of parallel workers for CPU-bound stages (default: 1).")
@click.option("--refresh", is_flag=True, default=False,
              help="Re-queue completed jobs. For enrich: only tracks with missing data. For other stages: all done jobs.")
@click.option("--probe-repair", is_flag=True, default=False,
              help="Attempt ffmpeg re-mux repair for probe integrity failures (disabled by default).")
@click.option("--include-ml-classify", is_flag=True, default=False,
              help="Include ml_classify when running the full pipeline (disabled by default).")
@click.option("--library-root", "library_root", default=None, type=click.Path(file_okay=False),
              help="Root directory for resolving relative track paths. Defaults to DB parent directory.")
@click.option("-v", "--verbose", is_flag=True, default=False,
              help="Enable verbose (DEBUG) logging output.")
def run(db_path: str, stage: str | None, workers: int, refresh: bool, probe_repair: bool, include_ml_classify: bool, library_root: str | None, verbose: bool) -> None:
    """Run the enrichment pipeline on all pending jobs."""
    # Bump logging for run command: INFO by default, DEBUG with -v
    log_level = logging.DEBUG if verbose else logging.INFO
    logging.getLogger("musesleuth").setLevel(log_level)

    db_file = Path(db_path)
    if not db_file.exists():
        raise click.ClickException(f"Database not found: {db_file}")

    conn = get_connection(db_file)

    if refresh:
        if stage == "enrich" or stage is None:
            refreshed = refresh_incomplete_enrich(conn)
            click.echo(f"Refreshed {refreshed} enrich job(s) with incomplete data.")
        if stage and stage != "enrich":
            refreshed = refresh_stage(conn, stage)
            retried = retry_failed_jobs(conn, stage=stage)
            click.echo(f"Reset {refreshed + retried} {stage} job(s) back to pending ({refreshed} done/running, {retried} failed).")

    lib_root = Path(library_root) if library_root else db_file.resolve().parent
    orch = PipelineOrchestrator(
        conn,
        worker_id="cli",
        probe_attempt_repair=probe_repair,
        library_root=lib_root,
    )

    if stage:
        if stage in ("probe", "analyze", "ml_classify", "fingerprint_match", "derive_signals", "writeback", "loudness", "timbre", "embed", "structure", "beatgrid") and workers > 1:
            total = orch.process_stage_parallel(stage, max_workers=workers, db_path=db_path)
            click.echo(f"Processed {total} {stage} job(s) with {workers} workers.")
        else:
            total = 0
            while orch.process_next(stage):
                total += 1
            click.echo(f"Processed {total} {stage} job(s).")
    else:
        default_stages = [s for s in STAGES if include_ml_classify or s != "ml_classify"]
        # For full pipeline, use parallel for CPU-bound stages if workers > 1
        if workers > 1:
            total = 0
            # Process CPU-bound/IO stages in parallel
            parallel_stages = tuple(
                s
                for s in ("probe", "analyze", "ml_classify", "fingerprint_match", "derive_signals", "writeback", "loudness", "timbre", "embed", "structure", "beatgrid")
                if include_ml_classify or s != "ml_classify"
            )
            for parallel_stage in parallel_stages:
                stage_total = orch.process_stage_parallel(parallel_stage, max_workers=workers, db_path=db_path)
                click.echo(f"Processed {stage_total} {parallel_stage} job(s) with {workers} workers.")
                total += stage_total

            # Then process remaining stages sequentially (import, enrich)
            for s in default_stages:
                if s not in parallel_stages:
                    while orch.process_next(s):
                        total += 1
        else:
            total = 0
            for s in default_stages:
                while orch.process_next(s):
                    total += 1
        click.echo(f"Processed {total} job(s) across all stages.")

    # Run dedup + remix grouping after all stages complete
    from musesleuth.dedup import assign_duplicate_groups
    dup_count, remix_count = assign_duplicate_groups(conn)
    if dup_count or remix_count:
        click.echo(f"Assigned {dup_count} duplicate group(s) and {remix_count} remix group(s).")

    conn.close()


@cli.command()
@click.option("--db", "db_path", required=True, type=click.Path(), help="SQLite database path.")
@click.option("--fields", "fields", default="bpm,genre,key",
              help="Comma-separated list of fields to write back.")
def writeback(db_path: str, fields: str) -> None:
    """Write enriched metadata back into audio file tags."""
    from musesleuth.tag_writer import write_tags_to_file

    db_file = Path(db_path)
    if not db_file.exists():
        raise click.ClickException(f"Database not found: {db_file}")

    conn = get_connection(db_file)
    field_list = [f.strip() for f in fields.split(",") if f.strip()]

    tracks = conn.execute(
        "SELECT metadata_id, full_path FROM tracks"
    ).fetchall()

    written = 0
    skipped = 0
    failed = 0

    for track in tracks:
        mid = track["metadata_id"]
        fpath = track["full_path"]

        # Build tags dict from DB data
        tags: dict[str, str] = {}

        if "bpm" in field_list:
            mf = conn.execute(
                "SELECT bpm_final FROM musical_features WHERE metadata_id = ?",
                (mid,),
            ).fetchone()
            if mf and mf["bpm_final"]:
                tags["bpm"] = str(int(round(mf["bpm_final"])))

        if "key" in field_list:
            ps = conn.execute(
                "SELECT camelot_key FROM playlist_signals WHERE metadata_id = ?",
                (mid,),
            ).fetchone()
            if ps and ps["camelot_key"]:
                tags["key"] = ps["camelot_key"]

        if "genre" in field_list:
            gt = conn.execute(
                "SELECT tag_value FROM genres_tags WHERE metadata_id = ? LIMIT 1",
                (mid,),
            ).fetchone()
            if gt and gt["tag_value"]:
                tags["genre"] = gt["tag_value"]

        if not tags:
            skipped += 1
            continue

        result = write_tags_to_file(conn, mid, fpath, tags)
        if result.success:
            written += 1
        else:
            failed += 1

    conn.close()
    click.echo(f"Writeback complete: {written} written, {skipped} skipped, {failed} failed.")


@cli.command()
@click.option("--db", "db_path", required=True, type=click.Path(), help="SQLite database path.")
@click.option("--stage", "stage", default=None, type=click.Choice(STAGES, case_sensitive=False),
              help="Only retry jobs for this stage.")
@click.option("--max-attempts", "max_attempts", default=0, type=int,
              help="Only retry jobs with fewer than N attempts (0 = unlimited).")
def retry(db_path: str, stage: str | None, max_attempts: int) -> None:
    """Reset failed jobs back to pending for retry."""
    db_file = Path(db_path)
    if not db_file.exists():
        raise click.ClickException(f"Database not found: {db_file}")

    conn = get_connection(db_file)
    count = retry_failed_jobs(conn, stage=stage, max_attempts=max_attempts)
    conn.close()

    label = f"{stage} " if stage else ""
    click.echo(f"Retried {count} {label}job(s).")


@cli.command()
@click.option("--db", "db_path", required=True, type=click.Path(), help="SQLite database path.")
def dedup(db_path: str) -> None:
    """Detect duplicates and remix groups across all tracks.

    Assigns duplicate_group (hash + fuzzy matches) and remix_group
    (same composition, different mix) IDs into playlist_signals so
    playlist generation picks only one version per song.
    """
    from musesleuth.dedup import assign_duplicate_groups

    db_file = Path(db_path)
    if not db_file.exists():
        raise click.ClickException(f"Database not found: {db_file}")

    conn = get_connection(db_file)
    dup_count, remix_count = assign_duplicate_groups(conn)
    conn.close()
    click.echo(f"Assigned {dup_count} duplicate group(s) and {remix_count} remix group(s).")


@cli.command()
@click.option("--db", "db_path", required=True, type=click.Path(), help="SQLite database path.")
@click.option("--format", "fmt", type=click.Choice(["csv", "json"]), default="csv",
              help="Output format.")
@click.option("--output", "output_path", required=True, type=click.Path(),
              help="Output file path.")
def export(db_path: str, fmt: str, output_path: str) -> None:
    """Export enriched track data to CSV or JSON."""
    import csv as csv_mod
    import json

    db_file = Path(db_path)
    if not db_file.exists():
        raise click.ClickException(f"Database not found: {db_file}")

    conn = get_connection(db_file)

    tracks = conn.execute(
        "SELECT metadata_id, title, artist, album, year, full_path FROM tracks"
    ).fetchall()

    records = []
    for t in tracks:
        mid = t["metadata_id"]
        rec: dict = {
            "metadata_id": mid,
            "title": t["title"],
            "artist": t["artist"],
            "album": t["album"],
            "year": t["year"],
            "full_path": t["full_path"],
        }

        mf = conn.execute(
            "SELECT bpm_final, key_name, key_mode, energy FROM musical_features WHERE metadata_id = ?",
            (mid,),
        ).fetchone()
        if mf:
            rec["bpm"] = mf["bpm_final"]
            rec["key"] = mf["key_name"]
            rec["mode"] = mf["key_mode"]
            rec["energy"] = mf["energy"]

        ps = conn.execute(
            "SELECT decade_bucket, bpm_bucket, camelot_key, energy_tier, popularity_tier "
            "FROM playlist_signals WHERE metadata_id = ?",
            (mid,),
        ).fetchone()
        if ps:
            rec["decade"] = ps["decade_bucket"]
            rec["bpm_bucket"] = ps["bpm_bucket"]
            rec["camelot_key"] = ps["camelot_key"]
            rec["energy_tier"] = ps["energy_tier"]
            rec["popularity_tier"] = ps["popularity_tier"]

        records.append(rec)

    conn.close()

    out = Path(output_path)
    if fmt == "json":
        out.write_text(json.dumps(records, indent=2, default=str))
    else:
        if records:
            fieldnames = list(records[0].keys())
            with open(out, "w", newline="", encoding="utf-8") as f:
                writer = csv_mod.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(records)
        else:
            out.write_text("")

    click.echo(f"Exported {len(records)} track(s) to {out}.")


@cli.command()
@click.argument("directory", type=click.Path(exists=True, file_okay=False))
@click.option("--db", "db_path", required=True, type=click.Path(), help="SQLite database path.")
@click.option("--dry-run", is_flag=True, default=False, help="Show what would be imported without making changes.")
@click.option("--workers", default=4, type=int, help="Parallel workers for file I/O (default: 4).")
@click.option("--exclude-dir", "exclude_dirs", multiple=True,
              help="Directory name to skip during recursion (repeatable, case-insensitive).")
def scan(directory: str, db_path: str, dry_run: bool, workers: int, exclude_dirs: tuple[str, ...]) -> None:
    """Scan a directory tree for new audio files and import them.

    Walks DIRECTORY recursively for audio files (.mp3, .flac, .ogg, etc.).
    New files are added to the database with sidecar identity files.
    Files with existing sidecars whose paths have changed are relocated.
    """
    from musesleuth.scanner import scan_directory

    db_file = Path(db_path)
    conn = get_connection(db_file)
    create_schema(conn)

    result = scan_directory(
        conn,
        Path(directory),
        dry_run=dry_run,
        workers=workers,
        exclude_dirs=exclude_dirs,
    )

    conn.close()

    if dry_run:
        click.echo(f"[dry-run] Would import {result.imported} new track(s), "
                    f"relocate {result.relocated}, skip {result.skipped} existing.")
    else:
        click.echo(f"Imported {result.imported} new track(s), "
                    f"relocated {result.relocated}, skipped {result.skipped} existing.")
        if result.lyrics_found:
            click.echo(f"  Found {result.lyrics_found} lyric sidecar(s).")

    if result.errors:
        click.echo(f"  {len(result.errors)} error(s):")
        for err in result.errors[:10]:
            click.echo(f"    {err}")
        if len(result.errors) > 10:
            click.echo(f"    ... and {len(result.errors) - 10} more")


@cli.command()
@click.argument("directory", type=click.Path(exists=True, file_okay=False))
@click.option("--db", "db_path", required=True, type=click.Path(), help="SQLite database path.")
@click.option("--dry-run", is_flag=True, default=False, help="Show what would change without making changes.")
@click.option("--verify-hash", is_flag=True, default=False,
              help="Verify BLAKE3 hash_partial before updating -- rejects files that were replaced, not just moved.")
@click.option("--workers", default=4, type=int, help="Parallel workers for file I/O (default: 4).")
@click.option("--exclude-dir", "exclude_dirs", multiple=True,
              help="Directory name to skip during recursion (repeatable, case-insensitive).")
def relocate(
    directory: str,
    db_path: str,
    dry_run: bool,
    verify_hash: bool,
    workers: int,
    exclude_dirs: tuple[str, ...],
) -> None:
    """Update database paths by scanning for sidecar identity files.

    Walks DIRECTORY for .dlpmeta sidecar files and matches each to a database
    record via metadata_id.  When the stored path differs from the file's
    current location, the database is updated so playlist exports and pipeline
    stages use the new path.
    """
    from musesleuth.scanner import relocate_directory

    db_file = Path(db_path)
    if not db_file.exists():
        raise click.ClickException(f"Database not found: {db_file}")

    conn = get_connection(db_file)

    result = relocate_directory(
        conn,
        Path(directory),
        dry_run=dry_run,
        verify_hash=verify_hash,
        workers=workers,
        exclude_dirs=exclude_dirs,
    )

    conn.close()

    label = "[dry-run] Would update" if dry_run else "Updated"
    click.echo(f"{label} {result.updated} path(s), "
               f"{result.unchanged} unchanged, "
               f"{result.orphaned} orphaned sidecar(s).")

    if result.missing_audio:
        click.echo(f"  {result.missing_audio} sidecar(s) with missing audio files.")
    if result.hash_mismatches:
        click.echo(f"  {result.hash_mismatches} hash mismatch(es) -- skipped (file content changed).")
    if result.errors:
        click.echo(f"  {len(result.errors)} error(s):")
        for err in result.errors[:10]:
            click.echo(f"    {err}")
        if len(result.errors) > 10:
            click.echo(f"    ... and {len(result.errors) - 10} more")


@cli.group(name="opus")
def opus_group() -> None:
    """Commands for staged Opus migration + DB sync."""


@opus_group.command(name="backup-baseline")
@click.option("--db", "db_path", required=True, type=click.Path(), help="SQLite database path.")
@click.option("--backup-dir", required=True, type=click.Path(file_okay=False), help="Directory for DB backup and inventory output.")
@click.option("--inventory-name", default="", help="Optional CSV filename (default timestamped).")
def opus_backup_baseline(db_path: str, backup_dir: str, inventory_name: str) -> None:
    """Create DB backup + baseline inventory manifest."""
    db_file = Path(db_path)
    if not db_file.exists():
        raise click.ClickException(f"Database not found: {db_file}")

    out_dir = Path(backup_dir)
    backup_file = backup_database(db_file, out_dir)

    inventory_file = out_dir / (
        inventory_name
        if inventory_name.strip()
        else f"track-inventory-{db_file.stem}.csv"
    )
    conn = get_connection(db_file)
    count = export_track_inventory(conn, inventory_file)
    conn.close()

    click.echo(f"Backup: {backup_file}")
    click.echo(f"Inventory: {inventory_file} ({count} track rows)")


@opus_group.command(name="pilot")
@click.option("--db", "db_path", required=True, type=click.Path(), help="SQLite database path.")
@click.option("--source-root", default="", type=click.Path(file_okay=False), help="Optional library root scope in DB paths.")
@click.option("--target-root", default="", type=click.Path(file_okay=False), help="Mirror output root for converted Opus files (default: source-root).")
@click.option("--manifest", "manifest_path", default="", type=click.Path(), help="CSV output path for pilot results (default: <source-root>/opus-pilot-manifest.csv, else DB folder).")
@click.option("--limit", default=1000, type=int, help="Max tracks in pilot batch.")
@click.option("--bitrate", default=160, type=int, help="Max Opus bitrate kbps (default 160).")
@click.option("--scaling/--no-scaling", default=True, help="Scale Opus bitrate from lossy source bitrate, capped by --bitrate.")
@click.option("--workers", default=8, type=int, help="Parallel ffmpeg workers.")
@click.option("--relocate-workers", default=8, type=int, help="Parallel workers for targeted relocate scan.")
@click.option("--dry-run", is_flag=True, default=False, help="Plan pilot conversion without encoding.")
def opus_pilot(
    db_path: str,
    source_root: str,
    target_root: str,
    manifest_path: str,
    limit: int,
    bitrate: int,
    scaling: bool,
    workers: int,
    relocate_workers: int,
    dry_run: bool,
) -> None:
    """Convert a representative pilot set and resync DB paths."""
    db_file = Path(db_path)
    if not db_file.exists():
        raise click.ClickException(f"Database not found: {db_file}")

    source_root_path = Path(source_root) if source_root.strip() else None
    if source_root_path is not None and not source_root_path.exists():
        raise click.ClickException(f"Source root not found: {source_root_path}")
    if source_root_path is not None and not source_root_path.is_dir():
        raise click.ClickException(f"Source root is not a directory: {source_root_path}")
    target_root_path = Path(target_root) if target_root.strip() else source_root_path
    default_manifest_parent = source_root_path if source_root_path is not None else db_file.parent
    manifest_path_obj = Path(manifest_path) if manifest_path.strip() else (default_manifest_parent / "opus-pilot-manifest.csv")

    conn = get_connection(db_file)
    tasks = plan_tasks(
        conn,
        source_root_path,
        target_root_path,
        limit=max(1, limit),
    )
    if not tasks:
        conn.close()
        click.echo("No eligible non-Opus tracks found for pilot.")
        return

    summary = run_conversion_batch(
        conn,
        tasks,
        bitrate_kbps=max(32, bitrate),
        scaling=scaling,
        workers=max(1, workers),
        dry_run=dry_run,
        manifest_path=manifest_path_obj,
        run_relocate=not dry_run,
        relocate_workers=max(1, relocate_workers),
    )
    conn.close()
    if dry_run:
        click.echo(f"[dry-run] Planned {len(tasks)} pilot conversion(s).")
        click.echo(f"Manifest: {manifest_path_obj}")
        return

    click.echo(f"Pilot converted={summary.converted}, skipped_existing={summary.skipped_existing}, failed={summary.failed}")
    click.echo(f"Relocate updated={summary.relocated}, unchanged={summary.unchanged}, orphaned={summary.orphaned}")
    click.echo(f"Manifest: {manifest_path_obj}")


@opus_group.command(name="batch-rollout")
@click.option("--db", "db_path", required=True, type=click.Path(), help="SQLite database path.")
@click.option("--source-root", default="", type=click.Path(file_okay=False), help="Optional library root scope in DB paths.")
@click.option("--target-root", default="", type=click.Path(file_okay=False), help="Mirror output root for converted Opus files (default: source-root).")
@click.option("--manifest-dir", default="", type=click.Path(file_okay=False), help="Directory for per-batch CSV manifests (default: source-root, else DB folder).")
@click.option("--batch-size", default=2000, type=int, help="Tracks per conversion batch (default 2000).")
@click.option("--bitrate", default=160, type=int, help="Max Opus bitrate kbps (default 160).")
@click.option("--scaling/--no-scaling", default=True, help="Scale Opus bitrate from lossy source bitrate, capped by --bitrate.")
@click.option("--workers", default=24, type=int, help="Parallel ffmpeg workers.")
@click.option("--relocate-workers", default=8, type=int, help="Parallel workers for targeted relocate scan.")
@click.option("--dry-run", is_flag=True, default=False, help="Plan full rollout without encoding.")
def opus_batch_rollout(
    db_path: str,
    source_root: str,
    target_root: str,
    manifest_dir: str,
    batch_size: int,
    bitrate: int,
    scaling: bool,
    workers: int,
    relocate_workers: int,
    dry_run: bool,
) -> None:
    """Run full-library conversion in batches with per-batch relocate scan."""
    db_file = Path(db_path)
    if not db_file.exists():
        raise click.ClickException(f"Database not found: {db_file}")

    source_root_path = Path(source_root) if source_root.strip() else None
    if source_root_path is not None and not source_root_path.exists():
        raise click.ClickException(f"Source root not found: {source_root_path}")
    if source_root_path is not None and not source_root_path.is_dir():
        raise click.ClickException(f"Source root is not a directory: {source_root_path}")
    target_root_path = Path(target_root) if target_root.strip() else source_root_path
    default_manifest_dir = source_root_path if source_root_path is not None else db_file.parent
    out_dir = Path(manifest_dir) if manifest_dir.strip() else default_manifest_dir

    conn = get_connection(db_file)
    tasks = plan_tasks(conn, source_root_path, target_root_path)
    if not tasks:
        conn.close()
        click.echo("No eligible non-Opus tracks found for rollout.")
        return

    out_dir.mkdir(parents=True, exist_ok=True)

    total = len(tasks)
    converted = 0
    skipped_existing = 0
    failed = 0
    relocated = 0
    batch_n = 0
    batch_size = max(1, batch_size)

    for start in range(0, total, batch_size):
        batch_n += 1
        chunk = tasks[start:start + batch_size]
        manifest_path_obj = out_dir / f"batch-{batch_n:04d}.csv"
        summary = run_conversion_batch(
            conn,
            chunk,
            bitrate_kbps=max(32, bitrate),
            scaling=scaling,
            workers=max(1, workers),
            dry_run=dry_run,
            manifest_path=manifest_path_obj,
            run_relocate=not dry_run,
            relocate_workers=max(1, relocate_workers),
        )
        converted += summary.converted
        skipped_existing += summary.skipped_existing
        failed += summary.failed
        relocated += summary.relocated
        click.echo(
            f"Batch {batch_n}: converted={summary.converted}, "
            f"skipped_existing={summary.skipped_existing}, "
            f"failed={summary.failed}, relocate_updated={summary.relocated}, "
            f"manifest={manifest_path_obj}"
        )

    conn.close()
    if dry_run:
        click.echo(f"[dry-run] Planned {total} conversion(s) in {batch_n} batch(es).")
    else:
        click.echo(
            f"Rollout complete: converted={converted}, skipped_existing={skipped_existing}, "
            f"failed={failed}, relocate_updated={relocated}."
        )


@opus_group.command(name="final-reconcile")
@click.option("--db", "db_path", required=True, type=click.Path(), help="SQLite database path.")
@click.option("--target-root", default="", type=click.Path(file_okay=False), help="Optional converted library root safety boundary.")
@click.option("--workers", default=12, type=int, help="Parallel workers for relocate scan.")
def opus_final_reconcile(db_path: str, target_root: str, workers: int) -> None:
    """Run final DB path reconcile and parity checks after cutover."""

    db_file = Path(db_path)
    if not db_file.exists():
        raise click.ClickException(f"Database not found: {db_file}")

    target_root_path = Path(target_root) if target_root.strip() else None
    if target_root_path is not None and not target_root_path.exists():
        raise click.ClickException(f"Target root not found: {target_root_path}")
    if target_root_path is not None and not target_root_path.is_dir():
        raise click.ClickException(f"Target root is not a directory: {target_root_path}")

    conn = get_connection(db_file)
    result = reconcile_db_to_opus(
        conn,
        target_root=target_root_path,
    )
    totals = conn.execute("SELECT COUNT(*) as cnt FROM tracks").fetchone()
    opus_rows = conn.execute(
        "SELECT COUNT(*) as cnt FROM tracks WHERE filename LIKE '%.opus'"
    ).fetchone()
    conn.close()

    click.echo(
        f"Final reconcile updated={result.updated}, "
        f"already_opus={result.already_opus}, missing_opus={result.missing_opus}"
    )
    click.echo(
        f"DB parity: total_tracks={totals['cnt']}, opus_tracks={opus_rows['cnt']}"
    )


@opus_group.command(name="regenerate-sidecars")
@click.option("--db", "db_path", required=True, type=click.Path(), help="SQLite database path.")
@click.option("--limit", default=800, type=int, help="How many Opus tracks to repair (default 800).")
@click.option("--newest-first/--oldest-first", default=True, help="Pick newest-updated Opus tracks first.")
@click.option("--dry-run", is_flag=True, default=False, help="Preview repair counts without writing.")
def opus_regenerate_sidecars(db_path: str, limit: int, newest_first: bool, dry_run: bool) -> None:
    """Regenerate Opus sidecars from sibling source sidecars."""
    db_file = Path(db_path)
    if not db_file.exists():
        raise click.ClickException(f"Database not found: {db_file}")

    conn = get_connection(db_file)
    summary = regenerate_opus_sidecars(
        conn,
        limit=max(1, limit),
        newest_first=newest_first,
        dry_run=dry_run,
    )
    conn.close()
    label = "[dry-run] " if dry_run else ""
    click.echo(
        f"{label}repaired={summary.repaired}, "
        f"missing_source_sidecar={summary.missing_source_sidecar}, "
        f"read_errors={summary.read_errors}, write_errors={summary.write_errors}"
    )


@cli.group()
def playlist() -> None:
    """Manage auto-generated playlists."""


@playlist.command(name="generate")
@click.option("--db", "db_path", required=True, type=click.Path(), help="SQLite database path.")
@click.option("--strategy", required=True,
              type=click.Choice(["genre", "bpm_range", "year_range", "camelot_chain", "energy_arc", "decade", "mood", "dj_flow"]),
              help="Playlist generation strategy.")
@click.option("--param", "raw_params", multiple=True,
              help="Strategy parameter as key=value (repeatable).")
@click.option("--name", "pl_name", required=True, help="Playlist name.")
@click.option("--limit", default=50, type=int, help="Max tracks (default 50).")
@click.option("--description", default=None, help="Optional description.")
def playlist_generate(db_path: str, strategy: str, raw_params: tuple[str, ...],
                      pl_name: str, limit: int, description: str | None) -> None:
    """Generate a playlist using the chosen strategy."""
    from musesleuth.playlist_generator import generate_playlist

    db_file = Path(db_path)
    if not db_file.exists():
        raise click.ClickException(f"Database not found: {db_file}")

    params: dict[str, str | float] = {}
    for kv in raw_params:
        if "=" not in kv:
            raise click.ClickException(f"Invalid param '{kv}' -- expected key=value")
        k, v = kv.split("=", 1)
        try:
            params[k] = float(v)
        except ValueError:
            params[k] = v

    conn = get_connection(db_file)
    create_schema(conn)
    result = generate_playlist(conn, strategy, pl_name, params, limit=limit, description=description)
    conn.close()
    click.echo(f"Created playlist '{result.name}' ({result.track_count} tracks)  id={result.playlist_id}")


@playlist.command(name="list")
@click.option("--db", "db_path", required=True, type=click.Path(), help="SQLite database path.")
def playlist_list(db_path: str) -> None:
    """List all playlists."""
    db_file = Path(db_path)
    if not db_file.exists():
        raise click.ClickException(f"Database not found: {db_file}")

    conn = get_connection(db_file)
    rows = conn.execute(
        "SELECT playlist_id, name, strategy, track_count, created_at FROM playlists ORDER BY created_at DESC"
    ).fetchall()
    conn.close()

    if not rows:
        click.echo("No playlists yet.")
        return
    for r in rows:
        click.echo(f"  {r['playlist_id']}  {r['name']:30s}  {r['strategy']:15s}  "
                    f"{r['track_count']:>4} tracks  {r['created_at']}")

@playlist.command(name="mix-plan")
@click.option("--db", "db_path", required=True, type=click.Path(), help="SQLite database path.")
@click.option("--id", "playlist_id", required=True, help="Playlist ID to generate mix plan for.")
def playlist_mix_plan(db_path: str, playlist_id: str) -> None:
    """Generate a mix plan with cue points and transitions for a playlist."""
    from musesleuth.mix_plan import generate_mix_plan, serialize_mix_plan

    db_file = Path(db_path)
    if not db_file.exists():
        raise click.ClickException(f"Database not found: {db_file}")

    conn = get_connection(db_file)

    pl = conn.execute(
        "SELECT playlist_id FROM playlists WHERE playlist_id = ?", (playlist_id,)
    ).fetchone()
    if not pl:
        conn.close()
        raise click.ClickException(f"Playlist not found: {playlist_id}")

    tracks = conn.execute(
        "SELECT metadata_id FROM playlist_tracks WHERE playlist_id = ? ORDER BY position",
        (playlist_id,),
    ).fetchall()
    track_ids = [r["metadata_id"] for r in tracks]

    plan = generate_mix_plan(conn, track_ids)
    conn.close()

    click.echo(serialize_mix_plan(plan))


@playlist.command(name="evaluate")
@click.option("--db", "db_path", required=True, type=click.Path(), help="SQLite database path.")
@click.option("--id", "playlist_id", required=True, help="Playlist ID to evaluate.")
@click.option("--compare-id", "compare_playlist_id", default=None, help="Optional second playlist ID for comparison.")
def playlist_evaluate(db_path: str, playlist_id: str, compare_playlist_id: str | None) -> None:
    """Compute DJ playlist metrics and optionally compare two playlists."""
    import json
    from musesleuth.evaluation import compare_playlists, compute_playlist_metrics

    db_file = Path(db_path)
    if not db_file.exists():
        raise click.ClickException(f"Database not found: {db_file}")

    conn = get_connection(db_file)

    pl = conn.execute(
        "SELECT playlist_id FROM playlists WHERE playlist_id = ?", (playlist_id,)
    ).fetchone()
    if not pl:
        conn.close()
        raise click.ClickException(f"Playlist not found: {playlist_id}")

    tracks = conn.execute(
        "SELECT metadata_id FROM playlist_tracks WHERE playlist_id = ? ORDER BY position",
        (playlist_id,),
    ).fetchall()
    track_ids = [r["metadata_id"] for r in tracks]

    if compare_playlist_id:
        other = conn.execute(
            "SELECT playlist_id FROM playlists WHERE playlist_id = ?", (compare_playlist_id,)
        ).fetchone()
        if not other:
            conn.close()
            raise click.ClickException(f"Playlist not found: {compare_playlist_id}")
        other_tracks = conn.execute(
            "SELECT metadata_id FROM playlist_tracks WHERE playlist_id = ? ORDER BY position",
            (compare_playlist_id,),
        ).fetchall()
        other_ids = [r["metadata_id"] for r in other_tracks]
        payload = compare_playlists(conn, track_ids, other_ids)
    else:
        payload = compute_playlist_metrics(conn, track_ids)

    conn.close()
    click.echo(json.dumps(payload, indent=2))


@playlist.command(name="export")
@click.option("--db", "db_path", required=True, type=click.Path(), help="SQLite database path.")
@click.option("--id", "playlist_id", required=True, help="Playlist ID to export.")
@click.option("--format", "fmt", type=click.Choice(["m3u8"]), default="m3u8", help="Export format.")
@click.option("--output", "output_path", required=True, type=click.Path(), help="Output file path.")
def playlist_export_cmd(db_path: str, playlist_id: str, fmt: str, output_path: str) -> None:
    """Export a playlist to a file."""
    from musesleuth.playlist_export import write_m3u8

    db_file = Path(db_path)
    if not db_file.exists():
        raise click.ClickException(f"Database not found: {db_file}")

    conn = get_connection(db_file)
    count = write_m3u8(conn, playlist_id, Path(output_path))
    conn.close()
    click.echo(f"Exported {count} track(s) to {output_path}")


@playlist.command(name="sync-subsonic")
@click.option("--db", "db_path", required=True, type=click.Path(), help="SQLite database path.")
@click.option("--id", "playlist_id", required=True, help="Playlist ID or exact playlist name to sync.")
@click.option("--subsonic-name", default=None, help="Optional override for the Subsonic playlist name.")
@click.option("--exclude-dir", "exclude_dirs", multiple=True, help="Exclude tracks under this directory prefix. May be repeated.")
def playlist_sync_subsonic(db_path: str, playlist_id: str, subsonic_name: str | None, exclude_dirs: tuple[str, ...]) -> None:
    """Sync a MuseSleuth playlist to Subsonic using path-first track resolution."""
    from musesleuth.subsonic import load_subsonic_settings_from_env, sync_playlist_to_subsonic

    db_file = Path(db_path)
    if not db_file.exists():
        raise click.ClickException(f"Database not found: {db_file}")

    conn = get_connection(db_file)
    create_schema(conn)
    try:
        settings = load_subsonic_settings_from_env()
        if exclude_dirs:
            from dataclasses import replace
            settings = replace(settings, excluded_dirs=settings.excluded_dirs + tuple(exclude_dirs))
        result = sync_playlist_to_subsonic(conn, playlist_id, target_name=subsonic_name, settings=settings)
    except (RuntimeError, ValueError) as exc:
        conn.close()
        raise click.ClickException(str(exc))
    conn.close()

    click.echo(
        f"Synced '{result.playlist_name}' to Subsonic as {result.subsonic_playlist_id} "
        f"({result.matched_count} matched, {result.missed_count} missed)"
    )
    if result.missed_tracks:
        click.echo("Missed tracks:")
        for missed in result.missed_tracks:
            click.echo(f"  - {missed}")


@playlist.command(name="audit-subsonic")
@click.option("--db", "db_path", required=True, type=click.Path(), help="SQLite database path.")
@click.option("--id", "playlist_id", required=True, help="Playlist ID or exact playlist name to audit.")
def playlist_audit_subsonic(db_path: str, playlist_id: str) -> None:
    """Audit a synced Subsonic playlist for duplicate IDs and fuzzy duplicate titles."""
    from musesleuth.subsonic import audit_synced_playlist

    db_file = Path(db_path)
    if not db_file.exists():
        raise click.ClickException(f"Database not found: {db_file}")

    conn = get_connection(db_file)
    create_schema(conn)
    try:
        result = audit_synced_playlist(conn, playlist_id)
    except (RuntimeError, ValueError) as exc:
        conn.close()
        raise click.ClickException(str(exc))
    conn.close()

    click.echo(
        f"Audit playlist={result.playlist_id} subsonic={result.subsonic_playlist_id} "
        f"tracks={result.track_count} dup_ids={result.duplicate_id_count} "
        f"fuzzy_title_dupes={result.fuzzy_duplicate_count}"
    )


@playlist.command(name="delete")
@click.option("--db", "db_path", required=True, type=click.Path(), help="SQLite database path.")
@click.option("--id", "playlist_id", required=True, help="Playlist ID or exact playlist name to delete.")
def playlist_delete(db_path: str, playlist_id: str) -> None:
    """Delete a MuseSleuth playlist and its linked Subsonic playlist when synced."""
    from musesleuth.subsonic import delete_playlist_with_remote

    db_file = Path(db_path)
    if not db_file.exists():
        raise click.ClickException(f"Database not found: {db_file}")

    conn = get_connection(db_file)
    create_schema(conn)
    try:
        deleted_id = delete_playlist_with_remote(conn, playlist_id)
    except (RuntimeError, ValueError) as exc:
        conn.close()
        raise click.ClickException(str(exc))
    conn.close()
    click.echo(f"Deleted playlist {deleted_id}")


@cli.command()
@click.option("--db", "db_path", required=True, type=click.Path(), help="SQLite database path.")
@click.option("--host", default="127.0.0.1", help="Host to bind to.")
@click.option("--port", default=8484, type=int, help="Port to serve on.")
def web(db_path: str, host: str, port: int) -> None:
    """Launch the web interface for exploring the database."""
    import uvicorn
    from musesleuth.web import create_app

    db_file = Path(db_path)
    if not db_file.exists():
        raise click.ClickException(f"Database not found: {db_file}")

    app = create_app(db_file)
    click.echo(f"Starting MuseSleuth web UI at http://{host}:{port}")
    uvicorn.run(app, host=host, port=port)


@cli.command()
@click.option("--db", "db_path", required=True, type=click.Path(), help="SQLite database path.")
def dashboard(db_path: str) -> None:
    """Show a Rich TUI dashboard of pipeline progress."""
    from rich.console import Console
    from musesleuth.dashboard import build_status_table, build_stage_table

    db_file = Path(db_path)
    if not db_file.exists():
        raise click.ClickException(f"Database not found: {db_file}")

    conn = get_connection(db_file)
    console = Console()
    console.print(build_status_table(conn))
    console.print(build_stage_table(conn))
    conn.close()