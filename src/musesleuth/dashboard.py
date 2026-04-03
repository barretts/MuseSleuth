"""Rich TUI dashboard for pipeline progress visualization."""
from __future__ import annotations

import sqlite3

from rich.table import Table

from musesleuth.job_queue import STAGES, get_job_counts, JobStatus


def build_status_table(conn: sqlite3.Connection) -> Table:
    """Build a Rich Table with overall pipeline status."""
    total_tracks = conn.execute(
        "SELECT COUNT(*) as cnt FROM tracks"
    ).fetchone()["cnt"]

    rows = conn.execute(
        "SELECT status, COUNT(*) as cnt FROM jobs GROUP BY status"
    ).fetchall()
    counts = {r["status"]: r["cnt"] for r in rows}
    total_jobs = sum(counts.values())
    done = counts.get(JobStatus.DONE, 0)
    pct = (done / total_jobs * 100.0) if total_jobs > 0 else 0.0

    table = Table(title="Pipeline Status", show_lines=True)
    table.add_column("Metric", style="bold cyan")
    table.add_column("Value", style="bold white")
    table.add_column("Detail", style="dim")

    table.add_row("Tracks", str(total_tracks), "")
    table.add_row("Total Jobs", str(total_jobs), f"{pct:.1f}% complete")
    table.add_row(
        "Pending", str(counts.get(JobStatus.PENDING, 0)),
        "[yellow]waiting[/yellow]",
    )
    table.add_row(
        "Running", str(counts.get(JobStatus.RUNNING, 0)),
        "[blue]in progress[/blue]",
    )
    table.add_row(
        "Done", str(done),
        "[green]completed[/green]",
    )
    table.add_row(
        "Failed", str(counts.get(JobStatus.FAILED, 0)),
        "[red]needs retry[/red]",
    )

    return table


def build_stage_table(conn: sqlite3.Connection) -> Table:
    """Build a Rich Table with per-stage job breakdown."""
    stage_counts = get_job_counts(conn)

    table = Table(title="Per-Stage Breakdown", show_lines=True)
    table.add_column("Stage", style="bold cyan")
    table.add_column("Pending", justify="right")
    table.add_column("Running", justify="right")
    table.add_column("Done", justify="right", style="green")
    table.add_column("Failed", justify="right", style="red")

    for stage in STAGES:
        sc = stage_counts.get(stage, {})
        table.add_row(
            stage,
            str(sc.get(JobStatus.PENDING, 0)),
            str(sc.get(JobStatus.RUNNING, 0)),
            str(sc.get(JobStatus.DONE, 0)),
            str(sc.get(JobStatus.FAILED, 0)),
        )

    return table