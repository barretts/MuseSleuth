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


@pytest.fixture
def synthetic_audio(tmp_path: Path) -> tuple:
    """Generate a 5-second 440 Hz sine wave as (numpy_array, sample_rate, wav_path).

    Useful for all audio-analysis tests so they don't need real music files.
    The WAV file is 16-bit PCM mono at 22050 Hz.
    """
    import struct
    import math

    sr = 22050
    duration = 5.0
    freq = 440.0
    n_samples = int(sr * duration)

    # Generate samples as floats in [-1, 1]
    samples_float = [math.sin(2.0 * math.pi * freq * i / sr) for i in range(n_samples)]

    # Write a minimal WAV file (16-bit PCM mono)
    wav_path = tmp_path / "sine_440hz.wav"
    max_int16 = 32767
    raw_data = struct.pack(f"<{n_samples}h", *(int(s * max_int16) for s in samples_float))

    with wav_path.open("wb") as f:
        # RIFF header
        data_size = n_samples * 2  # 16-bit = 2 bytes per sample
        f.write(b"RIFF")
        f.write(struct.pack("<I", 36 + data_size))
        f.write(b"WAVE")
        # fmt chunk
        f.write(b"fmt ")
        f.write(struct.pack("<I", 16))       # chunk size
        f.write(struct.pack("<H", 1))        # PCM format
        f.write(struct.pack("<H", 1))        # mono
        f.write(struct.pack("<I", sr))       # sample rate
        f.write(struct.pack("<I", sr * 2))   # byte rate
        f.write(struct.pack("<H", 2))        # block align
        f.write(struct.pack("<H", 16))       # bits per sample
        # data chunk
        f.write(b"data")
        f.write(struct.pack("<I", data_size))
        f.write(raw_data)

    # Also provide numpy array for tests that want raw samples
    try:
        import numpy as np
        samples_np = np.array(samples_float, dtype=np.float32)
    except ImportError:
        samples_np = samples_float

    return (samples_np, sr, wav_path)


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
