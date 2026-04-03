"""Fix import jobs that are stuck in pending status."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import click

from musesleuth.db import get_connection


@click.command()
@click.option("--db", "db_path", required=True, type=click.Path(), help="SQLite database path.")
def fix_import_jobs(db_path: str) -> None:
    """Mark all import jobs as done for tracks that have been imported."""
    db_file = Path(db_path)
    if not db_file.exists():
        raise click.ClickException(f"Database not found: {db_file}")

    conn = get_connection(db_file)
    
    # Get count of pending import jobs
    pending_count = conn.execute(
        "SELECT COUNT(*) as cnt FROM jobs WHERE stage = 'import' AND status = 'pending'"
    ).fetchone()["cnt"]
    
    if pending_count == 0:
        click.echo("No pending import jobs found. Nothing to fix.")
        conn.close()
        return
    
    # Mark all import jobs as done for tracks that exist
    cursor = conn.execute(
        """
        UPDATE jobs 
        SET status = 'done', 
            completed_at = datetime('now'), 
            updated_at = datetime('now')
        WHERE stage = 'import' 
        AND status = 'pending'
        AND metadata_id IN (SELECT metadata_id FROM tracks)
        """
    )
    
    updated_count = cursor.rowcount
    conn.commit()
    conn.close()
    
    click.echo(f"Fixed {updated_count} import job(s). They are now marked as done.")


if __name__ == "__main__":
    fix_import_jobs()