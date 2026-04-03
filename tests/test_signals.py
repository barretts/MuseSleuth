"""TDD tests for playlist signal derivation -- written before implementation."""
from __future__ import annotations

import sqlite3

import pytest

from musesleuth.db import create_schema, generate_metadata_id
from musesleuth.signals import (
    derive_decade_bucket,
    derive_bpm_bucket,
    derive_energy_tier,
    derive_camelot_key,
    derive_popularity_tier,
    run_derive_signals_for_track,
    CAMELOT_MAP,
)


class TestDeriveBuckets:
    """Tests for individual bucket/tier derivation functions."""

    @pytest.mark.unit
    def test_decade_2000(self) -> None:
        assert derive_decade_bucket("2000") == "2000s"

    @pytest.mark.unit
    def test_decade_1995(self) -> None:
        assert derive_decade_bucket("1995") == "1990s"

    @pytest.mark.unit
    def test_decade_empty(self) -> None:
        assert derive_decade_bucket("") is None

    @pytest.mark.unit
    def test_decade_invalid(self) -> None:
        assert derive_decade_bucket("abcd") is None

    @pytest.mark.unit
    def test_bpm_slow(self) -> None:
        assert derive_bpm_bucket(85.0) == "slow"

    @pytest.mark.unit
    def test_bpm_mid(self) -> None:
        assert derive_bpm_bucket(115.0) == "mid"

    @pytest.mark.unit
    def test_bpm_fast(self) -> None:
        assert derive_bpm_bucket(140.0) == "fast"

    @pytest.mark.unit
    def test_bpm_very_fast(self) -> None:
        assert derive_bpm_bucket(175.0) == "very_fast"

    @pytest.mark.unit
    def test_bpm_none(self) -> None:
        assert derive_bpm_bucket(None) is None

    @pytest.mark.unit
    def test_energy_low(self) -> None:
        assert derive_energy_tier(0.2) == "low"

    @pytest.mark.unit
    def test_energy_medium(self) -> None:
        assert derive_energy_tier(0.5) == "medium"

    @pytest.mark.unit
    def test_energy_high(self) -> None:
        assert derive_energy_tier(0.8) == "high"

    @pytest.mark.unit
    def test_energy_none(self) -> None:
        assert derive_energy_tier(None) is None

    @pytest.mark.unit
    def test_popularity_unknown(self) -> None:
        assert derive_popularity_tier(None) == "unknown"

    @pytest.mark.unit
    def test_popularity_niche(self) -> None:
        assert derive_popularity_tier(50) == "niche"

    @pytest.mark.unit
    def test_popularity_mainstream(self) -> None:
        assert derive_popularity_tier(50000) == "mainstream"

    @pytest.mark.unit
    def test_popularity_viral(self) -> None:
        assert derive_popularity_tier(500000) == "viral"


class TestCamelotKey:
    """Tests for Camelot wheel key mapping."""

    @pytest.mark.unit
    def test_c_major(self) -> None:
        assert derive_camelot_key("C", "major") == "8B"

    @pytest.mark.unit
    def test_a_minor(self) -> None:
        assert derive_camelot_key("A", "minor") == "8A"

    @pytest.mark.unit
    def test_unknown_key(self) -> None:
        assert derive_camelot_key(None, None) is None

    @pytest.mark.unit
    def test_all_major_keys_mapped(self) -> None:
        for key in ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]:
            result = derive_camelot_key(key, "major")
            assert result is not None
            assert result.endswith("B")

    @pytest.mark.unit
    def test_all_minor_keys_mapped(self) -> None:
        for key in ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]:
            result = derive_camelot_key(key, "minor")
            assert result is not None
            assert result.endswith("A")


def _seed_full_track(conn: sqlite3.Connection, mid: str, year: str = "2000",
                     bpm: float = 128.0, key: str = "A", mode: str = "minor",
                     energy: float = 0.7, listeners: int = 5000) -> None:
    conn.execute(
        "INSERT INTO tracks (metadata_id, title, artist, year, file_path, filename, full_path) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (mid, "Song", "Artist", year, "C:\\test\\", "song.mp3", f"C:\\test\\{mid}.mp3"),
    )
    conn.execute(
        "INSERT INTO musical_features (metadata_id, bpm_final, key_name, key_mode, energy) "
        "VALUES (?, ?, ?, ?, ?)",
        (mid, bpm, key, mode, energy),
    )
    conn.execute(
        "INSERT INTO track_stats (metadata_id, source, listener_count) VALUES (?, ?, ?)",
        (mid, "lastfm", listeners),
    )
    conn.commit()


@pytest.fixture
def db(in_memory_db: sqlite3.Connection) -> sqlite3.Connection:
    create_schema(in_memory_db)
    return in_memory_db


class TestRunDeriveSignals:
    """Tests for the derive_signals stage runner."""

    @pytest.mark.integration
    def test_stores_playlist_signals(self, db: sqlite3.Connection) -> None:
        mid = generate_metadata_id()
        _seed_full_track(db, mid)

        run_derive_signals_for_track(db, mid)

        row = db.execute(
            "SELECT decade_bucket, bpm_bucket, camelot_key, energy_tier, popularity_tier "
            "FROM playlist_signals WHERE metadata_id = ?",
            (mid,),
        ).fetchone()
        assert row is not None
        assert row["decade_bucket"] == "2000s"
        assert row["bpm_bucket"] == "fast"
        assert row["camelot_key"] == "8A"
        assert row["energy_tier"] == "high"
        assert row["popularity_tier"] is not None

    @pytest.mark.integration
    def test_handles_missing_features(self, db: sqlite3.Connection) -> None:
        mid = generate_metadata_id()
        db.execute(
            "INSERT INTO tracks (metadata_id, title, artist, file_path, filename, full_path) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (mid, "Song", "Artist", "C:\\test\\", "song.mp3", f"C:\\test\\{mid}.mp3"),
        )
        db.commit()

        run_derive_signals_for_track(db, mid)

        row = db.execute(
            "SELECT * FROM playlist_signals WHERE metadata_id = ?", (mid,)
        ).fetchone()
        assert row is not None
