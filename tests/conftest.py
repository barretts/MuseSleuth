"""Shared fixtures for MuseSleuth test suite."""
from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def tmp_dir(tmp_path: Path) -> Path:
    """Provide a clean temporary directory."""
    return tmp_path


@pytest.fixture
def sample_csv_rows() -> list[dict[str, str]]:
    """Minimal set of parsed CSV rows matching mp3tag.csv schema."""
    return [
        {
            "Title": "Into The Inner Space (Trance Radio Edit)",
            "Artist": "!Attention!",
            "Album": "Dream Dance Vol. 18",
            "Track": "17",
            "Year": "2000",
            "Length": "214",
            "Size": "8.17 MB",
            "Last Modified": "6/19/2013",
            "Path": "D:\\Music\\Top Hits\\",
            "Filename": "!Attention! - Into The Inner Space.mp3",
        },
        {
            "Title": "Higher (Original)",
            "Artist": "L-Vee",
            "Album": "Higher (Vinyl)",
            "Track": "1",
            "Year": "1999",
            "Length": "560",
            "Size": "10.68 MB",
            "Last Modified": "3/29/2026",
            "Path": "D:\\Music\\Trance_Collection\\2001\\L-Vee\\",
            "Filename": "(l_vee)-higher__original_vinyl_bmi.mp3",
        },
    ]


@pytest.fixture
def sample_csv_content() -> str:
    """Raw semicolon-delimited CSV content matching mp3tag.csv format."""
    header = "Title;Artist;Album;Track;Year;Length;Size;Last Modified;Path;Filename;"
    row1 = (
        "Into The Inner Space (Trance Radio Edit);!Attention!;Dream Dance Vol. 18;"
        "17;2000;214;8.17 MB;6/19/2013;"
        "D:\\Music\\Top Hits\\;!Attention! - Into The Inner Space.mp3;"
    )
    row2 = (
        "Higher (Original);L-Vee;Higher (Vinyl);"
        "1;1999;560;10.68 MB;3/29/2026;"
        "D:\\Music\\Trance_Collection\\2001\\L-Vee\\;(l_vee)-higher__original_vinyl_bmi.mp3;"
    )
    return "\n".join([header, row1, row2])


@pytest.fixture
def sample_csv_file(tmp_dir: Path, sample_csv_content: str) -> Path:
    """Write sample CSV to a temp file and return its path."""
    csv_path = tmp_dir / "test_tracks.csv"
    csv_path.write_text(sample_csv_content, encoding="utf-8")
    return csv_path


@pytest.fixture
def in_memory_db() -> sqlite3.Connection:
    """Provide a fresh in-memory SQLite connection with WAL-like settings."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


@pytest.fixture
def tmp_db_path(tmp_dir: Path) -> Path:
    """Provide a path for a temporary SQLite file."""
    return tmp_dir / "musesleuth_test.db"


def insert_dummy_track(conn: sqlite3.Connection, metadata_id: str) -> None:
    """Insert a minimal track row to satisfy FK constraints in tests."""
    conn.execute(
        """
        INSERT OR IGNORE INTO tracks
            (metadata_id, file_path, filename, full_path)
        VALUES (?, ?, ?, ?)
        """,
        (metadata_id, "C:\\test\\", "dummy.mp3", f"C:\\test\\{metadata_id}.mp3"),
    )
    conn.commit()
