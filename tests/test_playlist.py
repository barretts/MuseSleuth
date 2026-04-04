"""TDD tests for playlist generation, Camelot compatibility, M3U8 export, and dedup."""
from __future__ import annotations

import sqlite3

import pytest

from musesleuth.db import create_schema, generate_metadata_id
from musesleuth.playlist_generator import (
    camelot_compatible,
    generate_playlist,
    _dedup_candidates,
    STRATEGY_MAP,
)
from musesleuth.playlist_export import export_m3u8


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_track(
    conn: sqlite3.Connection,
    mid: str,
    *,
    title: str = "Song",
    artist: str = "Artist",
    year: str = "2000",
    bpm: float = 128.0,
    key_name: str = "A",
    key_mode: str = "minor",
    energy: float = 0.7,
    genre: str = "trance",
    mood: str = "energetic",
    camelot: str = "8A",
    decade: str = "2000s",
    duplicate_group: str | None = None,
    remix_group: str | None = None,
    duration_ms: int = 300_000,
) -> None:
    """Insert a fully-wired track for playlist generator tests."""
    conn.execute(
        "INSERT INTO tracks (metadata_id, title, artist, year, file_path, filename, full_path) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (mid, title, artist, year, "C:\\test\\", f"{mid}.mp3", f"C:\\test\\{mid}.mp3"),
    )
    conn.execute(
        "INSERT INTO musical_features (metadata_id, bpm_final, key_name, key_mode, energy) "
        "VALUES (?, ?, ?, ?, ?)",
        (mid, bpm, key_name, key_mode, energy),
    )
    conn.execute(
        "INSERT INTO ml_features (metadata_id, genre_primary, genre_confidence, mood_tags) "
        "VALUES (?, ?, ?, ?)",
        (mid, genre, 0.9, mood),
    )
    conn.execute(
        "INSERT INTO playlist_signals (metadata_id, camelot_key, decade_bucket, energy_tier, duplicate_group, remix_group) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (mid, camelot, decade, "high" if energy >= 0.66 else "medium", duplicate_group, remix_group),
    )
    conn.execute(
        "INSERT INTO technical_features (metadata_id, duration_ms) VALUES (?, ?)",
        (mid, duration_ms),
    )
    conn.commit()


@pytest.fixture
def db(in_memory_db: sqlite3.Connection) -> sqlite3.Connection:
    create_schema(in_memory_db)
    return in_memory_db


# ---------------------------------------------------------------------------
# Camelot compatibility unit tests
# ---------------------------------------------------------------------------

class TestCamelotCompatible:

    @pytest.mark.unit
    def test_same_key(self) -> None:
        assert camelot_compatible("8A", "8A") is True

    @pytest.mark.unit
    def test_ab_swap(self) -> None:
        assert camelot_compatible("8A", "8B") is True

    @pytest.mark.unit
    def test_plus_one(self) -> None:
        assert camelot_compatible("8A", "9A") is True

    @pytest.mark.unit
    def test_minus_one(self) -> None:
        assert camelot_compatible("8A", "7A") is True

    @pytest.mark.unit
    def test_wrap_12_to_1(self) -> None:
        assert camelot_compatible("12A", "1A") is True

    @pytest.mark.unit
    def test_wrap_1_to_12(self) -> None:
        assert camelot_compatible("1B", "12B") is True

    @pytest.mark.unit
    def test_incompatible(self) -> None:
        assert camelot_compatible("8A", "3A") is False

    @pytest.mark.unit
    def test_different_letter_different_number(self) -> None:
        assert camelot_compatible("8A", "9B") is False

    @pytest.mark.unit
    def test_none_key(self) -> None:
        assert camelot_compatible("", "8A") is False
        assert camelot_compatible("8A", "") is False

    @pytest.mark.unit
    def test_invalid_format(self) -> None:
        assert camelot_compatible("XX", "8A") is False


# ---------------------------------------------------------------------------
# Genre playlist
# ---------------------------------------------------------------------------

class TestGenrePlaylist:

    @pytest.mark.integration
    def test_generates_by_genre(self, db: sqlite3.Connection) -> None:
        ids = [generate_metadata_id() for _ in range(5)]
        for mid in ids[:3]:
            _seed_track(db, mid, genre="trance")
        for mid in ids[3:]:
            _seed_track(db, mid, genre="house")

        result = generate_playlist(db, "genre", "Trance Mix", {"genre": "trance"})
        assert result.track_count == 3
        assert result.name == "Trance Mix"

        rows = db.execute(
            "SELECT metadata_id FROM playlist_tracks WHERE playlist_id = ? ORDER BY position",
            (result.playlist_id,),
        ).fetchall()
        assert len(rows) == 3
        assert all(r["metadata_id"] in ids[:3] for r in rows)

    @pytest.mark.integration
    def test_respects_limit(self, db: sqlite3.Connection) -> None:
        for _ in range(10):
            _seed_track(db, generate_metadata_id(), genre="trance")

        result = generate_playlist(db, "genre", "Small", {"genre": "trance"}, limit=3)
        assert result.track_count == 3

    @pytest.mark.integration
    def test_empty_when_no_match(self, db: sqlite3.Connection) -> None:
        _seed_track(db, generate_metadata_id(), genre="trance")
        result = generate_playlist(db, "genre", "Nope", {"genre": "dnb"})
        assert result.track_count == 0


# ---------------------------------------------------------------------------
# BPM range playlist
# ---------------------------------------------------------------------------

class TestBpmRangePlaylist:

    @pytest.mark.integration
    def test_bpm_range_filter(self, db: sqlite3.Connection) -> None:
        _seed_track(db, generate_metadata_id(), bpm=90.0)
        mid_in = generate_metadata_id()
        _seed_track(db, mid_in, bpm=130.0)
        _seed_track(db, generate_metadata_id(), bpm=180.0)

        result = generate_playlist(db, "bpm_range", "120-140", {"bpm_min": 120, "bpm_max": 140})
        assert result.track_count == 1

        row = db.execute(
            "SELECT metadata_id FROM playlist_tracks WHERE playlist_id = ?",
            (result.playlist_id,),
        ).fetchone()
        assert row["metadata_id"] == mid_in


# ---------------------------------------------------------------------------
# Camelot chain playlist
# ---------------------------------------------------------------------------

class TestCamelotChainPlaylist:

    @pytest.mark.integration
    def test_chains_compatible_keys(self, db: sqlite3.Connection) -> None:
        seed = generate_metadata_id()
        _seed_track(db, seed, camelot="8A")
        m2 = generate_metadata_id()
        _seed_track(db, m2, camelot="8B")
        m3 = generate_metadata_id()
        _seed_track(db, m3, camelot="9A")
        _far = generate_metadata_id()
        _seed_track(db, _far, camelot="3B")

        result = generate_playlist(db, "camelot_chain", "Mix", {"seed_id": seed})
        assert result.track_count >= 2
        track_ids = [
            r["metadata_id"] for r in db.execute(
                "SELECT metadata_id FROM playlist_tracks WHERE playlist_id = ? ORDER BY position",
                (result.playlist_id,),
            ).fetchall()
        ]
        assert track_ids[0] == seed

    @pytest.mark.integration
    def test_raises_without_seed(self, db: sqlite3.Connection) -> None:
        with pytest.raises(ValueError, match="seed_id"):
            generate_playlist(db, "camelot_chain", "Fail", {})

    @pytest.mark.integration
    def test_raises_for_missing_camelot(self, db: sqlite3.Connection) -> None:
        mid = generate_metadata_id()
        db.execute(
            "INSERT INTO tracks (metadata_id, title, artist, file_path, filename, full_path) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (mid, "Song", "Art", "C:\\test\\", "x.mp3", f"C:\\test\\{mid}.mp3"),
        )
        db.commit()
        with pytest.raises(ValueError, match="Camelot key"):
            generate_playlist(db, "camelot_chain", "Fail", {"seed_id": mid})


# ---------------------------------------------------------------------------
# Energy arc playlist
# ---------------------------------------------------------------------------

class TestEnergyArcPlaylist:

    @pytest.mark.integration
    def test_creates_arc(self, db: sqlite3.Connection) -> None:
        energies = [0.1, 0.3, 0.5, 0.7, 0.9, 0.8, 0.4, 0.2]
        mids = []
        for e in energies:
            mid = generate_metadata_id()
            _seed_track(db, mid, energy=e)
            mids.append(mid)

        result = generate_playlist(db, "energy_arc", "Arc", {"peak_position": 0.6}, limit=8)
        assert result.track_count == 8

    @pytest.mark.integration
    def test_empty_when_no_energy(self, db: sqlite3.Connection) -> None:
        result = generate_playlist(db, "energy_arc", "Empty", {})
        assert result.track_count == 0


# ---------------------------------------------------------------------------
# Decade playlist
# ---------------------------------------------------------------------------

class TestDecadePlaylist:

    @pytest.mark.integration
    def test_filters_by_decade(self, db: sqlite3.Connection) -> None:
        _seed_track(db, generate_metadata_id(), decade="2000s")
        _seed_track(db, generate_metadata_id(), decade="2000s")
        _seed_track(db, generate_metadata_id(), decade="1990s")

        result = generate_playlist(db, "decade", "Y2K", {"decade": "2000s"})
        assert result.track_count == 2


# ---------------------------------------------------------------------------
# Mood playlist
# ---------------------------------------------------------------------------

class TestMoodPlaylist:

    @pytest.mark.integration
    def test_filters_by_mood(self, db: sqlite3.Connection) -> None:
        _seed_track(db, generate_metadata_id(), mood="energetic, dark")
        _seed_track(db, generate_metadata_id(), mood="chill, mellow")
        _seed_track(db, generate_metadata_id(), mood="energetic, uplifting")

        result = generate_playlist(db, "mood", "Energy", {"mood": "energetic"})
        assert result.track_count == 2


# ---------------------------------------------------------------------------
# Dedup exclusion
# ---------------------------------------------------------------------------

class TestDedupExclusion:

    @pytest.mark.integration
    def test_dedup_keeps_one_per_group(self, db: sqlite3.Connection) -> None:
        group_id = "dup-group-1"
        _seed_track(db, generate_metadata_id(), genre="trance", duplicate_group=group_id)
        _seed_track(db, generate_metadata_id(), genre="trance", duplicate_group=group_id)
        _seed_track(db, generate_metadata_id(), genre="trance", duplicate_group=group_id)
        _seed_track(db, generate_metadata_id(), genre="trance")

        result = generate_playlist(db, "genre", "Dedup Test", {"genre": "trance"})
        assert result.track_count == 2

    @pytest.mark.integration
    def test_remix_group_keeps_one_per_family(self, db: sqlite3.Connection) -> None:
        remix_id = "remix-group-1"
        _seed_track(db, generate_metadata_id(), genre="trance", remix_group=remix_id,
                     title="Song (Original Mix)")
        _seed_track(db, generate_metadata_id(), genre="trance", remix_group=remix_id,
                     title="Song (Extended Mix)")
        _seed_track(db, generate_metadata_id(), genre="trance", remix_group=remix_id,
                     title="Song (Club Mix)")
        _seed_track(db, generate_metadata_id(), genre="trance",
                     title="Different Track")

        result = generate_playlist(db, "genre", "Remix Dedup Test", {"genre": "trance"})
        assert result.track_count == 2

    @pytest.mark.integration
    def test_remix_and_duplicate_groups_independent(self, db: sqlite3.Connection) -> None:
        dup_id = "dup-group-A"
        remix_id = "remix-group-B"
        _seed_track(db, generate_metadata_id(), genre="trance",
                     duplicate_group=dup_id, title="Exact Copy 1")
        _seed_track(db, generate_metadata_id(), genre="trance",
                     duplicate_group=dup_id, title="Exact Copy 2")
        _seed_track(db, generate_metadata_id(), genre="trance",
                     remix_group=remix_id, title="Song (Original Mix)")
        _seed_track(db, generate_metadata_id(), genre="trance",
                     remix_group=remix_id, title="Song (Extended Mix)")
        _seed_track(db, generate_metadata_id(), genre="trance",
                     title="Standalone")

        result = generate_playlist(db, "genre", "Both Groups", {"genre": "trance"})
        assert result.track_count == 3


# ---------------------------------------------------------------------------
# Unknown strategy
# ---------------------------------------------------------------------------

class TestStrategyValidation:

    @pytest.mark.unit
    def test_unknown_strategy_raises(self, db: sqlite3.Connection) -> None:
        with pytest.raises(ValueError, match="Unknown strategy"):
            generate_playlist(db, "bogus", "Fail", {})


# ---------------------------------------------------------------------------
# M3U8 export
# ---------------------------------------------------------------------------

class TestM3U8Export:

    @pytest.mark.integration
    def test_m3u8_format(self, db: sqlite3.Connection) -> None:
        m1 = generate_metadata_id()
        m2 = generate_metadata_id()
        _seed_track(db, m1, title="Track One", artist="DJ A", duration_ms=180_000)
        _seed_track(db, m2, title="Track Two", artist="DJ B", duration_ms=240_000)

        result = generate_playlist(db, "genre", "Export", {"genre": "trance"})
        content = export_m3u8(db, result.playlist_id)

        lines = content.strip().split("\n")
        assert lines[0] == "#EXTM3U"
        assert "#EXTINF:180,DJ A - Track One" in content
        assert "#EXTINF:240,DJ B - Track Two" in content
        assert "C:\\test\\" in content

    @pytest.mark.integration
    def test_empty_playlist_export(self, db: sqlite3.Connection) -> None:
        result = generate_playlist(db, "genre", "Empty Export", {"genre": "nothing"})
        content = export_m3u8(db, result.playlist_id)
        assert content.strip() == "#EXTM3U"


# ---------------------------------------------------------------------------
# Playlist persistence round-trip
# ---------------------------------------------------------------------------

class TestPlaylistPersistence:

    @pytest.mark.integration
    def test_playlist_stored_in_db(self, db: sqlite3.Connection) -> None:
        _seed_track(db, generate_metadata_id(), genre="trance")
        _seed_track(db, generate_metadata_id(), genre="trance")

        result = generate_playlist(db, "genre", "Stored", {"genre": "trance"}, description="test desc")

        pl = db.execute(
            "SELECT * FROM playlists WHERE playlist_id = ?", (result.playlist_id,)
        ).fetchone()
        assert pl is not None
        assert pl["name"] == "Stored"
        assert pl["strategy"] == "genre"
        assert pl["track_count"] == 2
        assert pl["description"] == "test desc"

    @pytest.mark.integration
    def test_positions_are_sequential(self, db: sqlite3.Connection) -> None:
        for _ in range(5):
            _seed_track(db, generate_metadata_id(), genre="house")

        result = generate_playlist(db, "genre", "Positions", {"genre": "house"})
        positions = [
            r["position"] for r in db.execute(
                "SELECT position FROM playlist_tracks WHERE playlist_id = ? ORDER BY position",
                (result.playlist_id,),
            ).fetchall()
        ]
        assert positions == list(range(1, result.track_count + 1))
