"""Database connection and schema creation."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from ulid import ULID

from musesleuth.db.schema import SCHEMA_SQL
from musesleuth.db.migrate import _migrate


def generate_metadata_id() -> str:
    """Generate a new ULID string for use as a metadata_id."""
    return str(ULID())


def get_connection(db_path: Path) -> sqlite3.Connection:
    """Create or open a SQLite connection with WAL mode and foreign keys."""
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    """Create all tables if they don't exist. Idempotent."""
    conn.executescript(SCHEMA_SQL)
    _migrate(conn)
