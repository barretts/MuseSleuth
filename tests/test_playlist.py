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
from musesleuth.timbre import serialize_array


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


def _seed_sonic_vectors(
    conn: sqlite3.Connection,
    mid: str,
    *,
    embedding: list[float] | None = None,
    timbre: list[float] | None = None,
) -> None:
    if embedding is not None:
        conn.execute(
            """
            INSERT INTO embeddings (metadata_id, model, scope, dim, vector)
            VALUES (?, 'test-model', 'global', ?, ?)
            """,
            (mid, len(embedding), serialize_array(embedding)),
        )
    if timbre is not None:
        conn.execute(
            """
            INSERT OR REPLACE INTO timbre_features (metadata_id, mfcc_mean, analyzed_at)
            VALUES (?, ?, datetime('now'))
            """,
            (mid, serialize_array(timbre)),
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
# Custom playlist seed-facet ranking
# ---------------------------------------------------------------------------

class TestCustomSeedFacets:

    @pytest.mark.integration
    def test_custom_seed_bpm_prioritizes_near_matches(self, db: sqlite3.Connection) -> None:
        seed = generate_metadata_id()
        near = generate_metadata_id()
        far = generate_metadata_id()

        _seed_track(db, seed, bpm=128.0, genre="trance")
        _seed_track(db, near, bpm=130.0, genre="trance")
        _seed_track(db, far, bpm=180.0, genre="trance")

        result = generate_playlist(
            db,
            "custom",
            "Seed BPM",
            {"seed_id": seed, "seed_facets": "bpm"},
            limit=2,
        )

        rows = db.execute(
            "SELECT metadata_id FROM playlist_tracks WHERE playlist_id = ? ORDER BY position",
            (result.playlist_id,),
        ).fetchall()
        playlist_ids = [row["metadata_id"] for row in rows]

        assert seed in playlist_ids
        assert near in playlist_ids
        assert far not in playlist_ids

    @pytest.mark.integration
    def test_custom_seed_included_even_if_range_filters_exclude_it(self, db: sqlite3.Connection) -> None:
        seed = generate_metadata_id()
        in_range = generate_metadata_id()

        _seed_track(db, seed, year="1995", bpm=128.0, genre="trance")
        _seed_track(db, in_range, year="2005", bpm=129.0, genre="trance")

        result = generate_playlist(
            db,
            "custom",
            "Seed Forced Include",
            {
                "seed_id": seed,
                "seed_facets": "bpm",
                "year_min": 2000,
                "year_max": 2010,
            },
            limit=2,
        )

        rows = db.execute(
            "SELECT metadata_id FROM playlist_tracks WHERE playlist_id = ? ORDER BY position",
            (result.playlist_id,),
        ).fetchall()
        playlist_ids = [row["metadata_id"] for row in rows]

        assert seed in playlist_ids
        assert in_range in playlist_ids

    @pytest.mark.integration
    def test_custom_seed_multiple_facets(self, db: sqlite3.Connection) -> None:
        seed = generate_metadata_id()
        close = generate_metadata_id()
        mismatch = generate_metadata_id()

        _seed_track(db, seed, year="2010", bpm=126.0, genre="house", camelot="8A", mood="energetic")
        _seed_track(db, close, year="2011", bpm=127.0, genre="house", camelot="8B", mood="energetic")
        _seed_track(db, mismatch, year="1995", bpm=90.0, genre="jazz", camelot="3B", mood="calm")

        result = generate_playlist(
            db,
            "custom",
            "Seed Multi",
            {"seed_id": seed, "seed_facets": "genre,bpm,camelot,mood,year"},
            limit=2,
        )

        rows = db.execute(
            "SELECT metadata_id FROM playlist_tracks WHERE playlist_id = ? ORDER BY position",
            (result.playlist_id,),
        ).fetchall()
        playlist_ids = [row["metadata_id"] for row in rows]

        assert seed in playlist_ids
        assert close in playlist_ids
        assert mismatch not in playlist_ids

    @pytest.mark.integration
    def test_custom_seed_embedding_prioritizes_vector_similarity(self, db: sqlite3.Connection) -> None:
        seed = generate_metadata_id()
        near = generate_metadata_id()
        far = generate_metadata_id()

        _seed_track(db, seed, genre="trance")
        _seed_track(db, near, genre="trance")
        _seed_track(db, far, genre="trance")
        _seed_sonic_vectors(db, seed, embedding=[1.0, 0.0, 0.0, 0.0])
        _seed_sonic_vectors(db, near, embedding=[0.99, 0.01, 0.0, 0.0])
        _seed_sonic_vectors(db, far, embedding=[0.0, 1.0, 0.0, 0.0])

        result = generate_playlist(
            db,
            "custom",
            "Seed Embedding",
            {"seed_id": seed, "seed_facets": "embedding"},
            limit=2,
        )

        rows = db.execute(
            "SELECT metadata_id FROM playlist_tracks WHERE playlist_id = ? ORDER BY position",
            (result.playlist_id,),
        ).fetchall()
        playlist_ids = [row["metadata_id"] for row in rows]

        assert seed in playlist_ids
        assert near in playlist_ids
        assert far not in playlist_ids

    @pytest.mark.integration
    def test_custom_seed_timbre_prioritizes_vector_similarity(self, db: sqlite3.Connection) -> None:
        seed = generate_metadata_id()
        near = generate_metadata_id()
        far = generate_metadata_id()

        _seed_track(db, seed, genre="trance")
        _seed_track(db, near, genre="trance")
        _seed_track(db, far, genre="trance")
        _seed_sonic_vectors(db, seed, timbre=[0.5, 0.5, 0.2, 0.1])
        _seed_sonic_vectors(db, near, timbre=[0.49, 0.52, 0.2, 0.1])
        _seed_sonic_vectors(db, far, timbre=[-0.5, -0.5, -0.2, -0.1])

        result = generate_playlist(
            db,
            "custom",
            "Seed Timbre",
            {"seed_id": seed, "seed_facets": "timbre"},
            limit=2,
        )

        rows = db.execute(
            "SELECT metadata_id FROM playlist_tracks WHERE playlist_id = ? ORDER BY position",
            (result.playlist_id,),
        ).fetchall()
        playlist_ids = [row["metadata_id"] for row in rows]

        assert seed in playlist_ids
        assert near in playlist_ids
        assert far not in playlist_ids

    @pytest.mark.integration
    def test_custom_seed_year_window_filters_candidates(self, db: sqlite3.Connection) -> None:
        seed = generate_metadata_id()
        within_window = generate_metadata_id()
        outside_window = generate_metadata_id()

        _seed_track(db, seed, year="1999", genre="trance", bpm=130.0)
        _seed_track(db, within_window, year="2001", genre="trance", bpm=130.0)
        _seed_track(db, outside_window, year="2005", genre="trance", bpm=130.0)

        result = generate_playlist(
            db,
            "custom",
            "Seed Year Window",
            {
                "seed_id": seed,
                "seed_facets": "year",
                "seed_year_window": 2,
            },
            limit=3,
        )

        rows = db.execute(
            "SELECT metadata_id FROM playlist_tracks WHERE playlist_id = ? ORDER BY position",
            (result.playlist_id,),
        ).fetchall()
        playlist_ids = [row["metadata_id"] for row in rows]

        assert seed in playlist_ids
        assert within_window in playlist_ids
        assert outside_window not in playlist_ids

    @pytest.mark.integration
    def test_custom_seed_year_override_applies_window_from_override(self, db: sqlite3.Connection) -> None:
        seed = generate_metadata_id()
        around_override = generate_metadata_id()
        around_original = generate_metadata_id()

        # Seed has an incorrect metadata year (re-release scenario).
        _seed_track(db, seed, year="2010", genre="trance", bpm=130.0)
        _seed_track(db, around_override, year="1999", genre="trance", bpm=130.0)
        _seed_track(db, around_original, year="2011", genre="trance", bpm=130.0)

        result = generate_playlist(
            db,
            "custom",
            "Seed Year Override",
            {
                "seed_id": seed,
                "seed_facets": "year",
                "seed_year_window": 1,
                "seed_year_override": 1999,
            },
            limit=3,
        )

        rows = db.execute(
            "SELECT metadata_id FROM playlist_tracks WHERE playlist_id = ? ORDER BY position",
            (result.playlist_id,),
        ).fetchall()
        playlist_ids = [row["metadata_id"] for row in rows]

        assert seed in playlist_ids
        assert around_override in playlist_ids
        assert around_original not in playlist_ids

    @pytest.mark.integration
    def test_custom_multi_seed_averages_similarity_and_includes_seeds(self, db: sqlite3.Connection) -> None:
        seed_a = generate_metadata_id()
        seed_b = generate_metadata_id()
        seed_c = generate_metadata_id()
        balanced = generate_metadata_id()
        one_sided = generate_metadata_id()

        _seed_track(db, seed_a, bpm=120.0, genre="trance")
        _seed_track(db, seed_b, bpm=130.0, genre="trance")
        _seed_track(db, seed_c, bpm=140.0, genre="trance")
        _seed_track(db, balanced, bpm=130.0, genre="trance")
        _seed_track(db, one_sided, bpm=120.0, genre="trance")

        result = generate_playlist(
            db,
            "custom",
            "Multi Seed",
            {
                "seed_ids": ",".join([seed_a, seed_b, seed_c]),
                "seed_facets": "bpm",
            },
            limit=5,
        )

        rows = db.execute(
            "SELECT metadata_id FROM playlist_tracks WHERE playlist_id = ? ORDER BY position",
            (result.playlist_id,),
        ).fetchall()
        playlist_ids = [row["metadata_id"] for row in rows]

        assert seed_a in playlist_ids
        assert seed_b in playlist_ids
        assert seed_c in playlist_ids
        assert balanced in playlist_ids
        assert one_sided in playlist_ids
        assert playlist_ids.index(balanced) < playlist_ids.index(one_sided)

    @pytest.mark.integration
    def test_custom_multi_seed_includes_selected_songs_without_facets(self, db: sqlite3.Connection) -> None:
        seed_a = generate_metadata_id()
        seed_b = generate_metadata_id()
        other = generate_metadata_id()

        _seed_track(db, seed_a, genre="trance")
        _seed_track(db, seed_b, genre="house")
        _seed_track(db, other, genre="trance")

        result = generate_playlist(
            db,
            "custom",
            "Seed Must Include",
            {
                "seed_ids": ",".join([seed_a, seed_b]),
            },
            limit=3,
        )

        rows = db.execute(
            "SELECT metadata_id FROM playlist_tracks WHERE playlist_id = ? ORDER BY position",
            (result.playlist_id,),
        ).fetchall()
        playlist_ids = [row["metadata_id"] for row in rows]

        assert seed_a in playlist_ids
        assert seed_b in playlist_ids


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
