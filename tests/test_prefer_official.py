"""Tests for the prefer-official cover filter."""
from __future__ import annotations

import sqlite3

import pytest

from musesleuth.db import create_schema, generate_metadata_id, get_connection
from musesleuth.prefer_official import (
    DEFAULT_POPULARITY_FLOOR,
    filter_and_prefer_official,
    has_soft_cover_signal,
    is_hard_blocked,
)


# ---------------------------------------------------------------------------
# Pure-function signal tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHardBlocklist:
    def test_blocks_known_tribute_labels(self) -> None:
        assert is_hard_blocked("8-Bit Arcade")
        assert is_hard_blocked("8 Bit Arcade")
        assert is_hard_blocked("8 Bit Universe")
        assert is_hard_blocked("Vitamin String Quartet")
        assert is_hard_blocked("Rockabye Baby!")
        assert is_hard_blocked("Kidz Bop Kids")
        assert is_hard_blocked("The Karaoke Channel")
        assert is_hard_blocked("Twinkle Twinkle Little Rock Star")

    def test_case_insensitive_and_whitespace(self) -> None:
        assert is_hard_blocked("  8-BIT ARCADE  ")
        assert is_hard_blocked("vitamin string quartet")

    def test_does_not_block_real_artists(self) -> None:
        assert not is_hard_blocked("Linkin Park")
        assert not is_hard_blocked("Tenacious D")
        assert not is_hard_blocked("Chester Bennington")
        assert not is_hard_blocked(None)
        assert not is_hard_blocked("")


@pytest.mark.unit
class TestSoftPatterns:
    def test_matches_title_cover_signals(self) -> None:
        assert has_soft_cover_signal("Some Artist", "In the End (8-bit Remix)")
        assert has_soft_cover_signal("Some Artist", "Numb (Karaoke Version)")
        assert has_soft_cover_signal("Some Artist", "One Step Closer (Instrumental Version)")
        assert has_soft_cover_signal("Some Artist", "Lullaby Rendition of Numb")
        assert has_soft_cover_signal("Some Artist", "Lullaby Version of Numb")
        assert has_soft_cover_signal("Some Artist", "Tribute to Chester")

    def test_does_not_match_standalone_lullaby_in_title(self) -> None:
        # "Lullaby" alone is a common legit song title; we only want to
        # match lullaby-rendition / lullaby-tribute style covers.
        assert not has_soft_cover_signal("A Perfect Circle", "Lullaby")
        assert not has_soft_cover_signal("Leonard Cohen", "Hunter's Lullaby")
        assert not has_soft_cover_signal("Opeth", "Death Whispered a Lullaby")

    def test_matches_artist_cover_signals(self) -> None:
        assert has_soft_cover_signal("8-Bit Heroes", "In the End")
        assert has_soft_cover_signal("Karaoke Party Band", "Numb")

    def test_does_not_match_legitimate_titles(self) -> None:
        assert not has_soft_cover_signal("Linkin Park", "In the End")
        assert not has_soft_cover_signal("Tenacious D", "Tribute")  # title word, no "to"
        assert not has_soft_cover_signal("Linkin Park", "Numb")


# ---------------------------------------------------------------------------
# filter_and_prefer_official against a live DB
# ---------------------------------------------------------------------------


def _insert(
    conn: sqlite3.Connection,
    mid: str,
    *,
    artist: str,
    title: str,
    listeners: int = 0,
    remix_group: str | None = None,
    duplicate_group: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO tracks (metadata_id, title, artist, file_path, filename, full_path) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (mid, title, artist, "C:\\t\\", f"{mid}.mp3", f"C:\\t\\{mid}.mp3"),
    )
    conn.execute(
        "INSERT INTO playlist_signals (metadata_id, duplicate_group, remix_group) "
        "VALUES (?, ?, ?)",
        (mid, duplicate_group, remix_group),
    )
    if listeners:
        conn.execute(
            "INSERT INTO track_stats (metadata_id, source, listener_count, play_count) "
            "VALUES (?, 'lastfm', ?, 0)",
            (mid, listeners),
        )
    conn.commit()


def _fetch_candidate_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Mimic the shape of rows strategies hand to ``_dedup_candidates``."""
    return conn.execute(
        """
        SELECT t.metadata_id, ps.duplicate_group, ps.remix_group
        FROM tracks t
        LEFT JOIN playlist_signals ps ON ps.metadata_id = t.metadata_id
        ORDER BY t.metadata_id
        """
    ).fetchall()


@pytest.fixture
def db(tmp_path) -> sqlite3.Connection:
    conn = get_connection(tmp_path / "prefer.db")
    create_schema(conn)
    yield conn
    conn.close()


@pytest.mark.integration
class TestFilterAndPreferOfficial:
    def test_hard_blocked_artist_is_dropped(self, db: sqlite3.Connection) -> None:
        official = generate_metadata_id()
        cover = generate_metadata_id()
        _insert(db, official, artist="Linkin Park", title="In the End", listeners=8_000_000)
        _insert(db, cover, artist="8-Bit Arcade", title="In the End", listeners=500)

        rows = _fetch_candidate_rows(db)
        out = filter_and_prefer_official(db, rows)
        out_ids = [r["metadata_id"] for r in out]
        assert official in out_ids
        assert cover not in out_ids

    def test_soft_match_dropped_below_popularity_floor(
        self, db: sqlite3.Connection
    ) -> None:
        niche_cover = generate_metadata_id()
        _insert(
            db, niche_cover,
            artist="Some Obscure Band",
            title="In the End (Karaoke Version)",
            listeners=50,
        )
        rows = _fetch_candidate_rows(db)
        out = filter_and_prefer_official(db, rows)
        assert niche_cover not in [r["metadata_id"] for r in out]

    def test_soft_match_survives_above_popularity_floor(
        self, db: sqlite3.Connection
    ) -> None:
        # A real artist happening to have "tribute to" in a song title.
        real = generate_metadata_id()
        _insert(
            db, real,
            artist="Tenacious D",
            title="Tribute to the Greatest Song",
            listeners=250_000,
        )
        rows = _fetch_candidate_rows(db)
        out = filter_and_prefer_official(db, rows)
        assert real in [r["metadata_id"] for r in out]

    def test_prefers_official_within_remix_group(
        self, db: sqlite3.Connection
    ) -> None:
        """Within the same remix_group, the official version should appear
        first so the caller's ``_dedup_candidates`` (first-wins) keeps it."""
        remix = "group-linkin-end"
        # Cover listed first in query-order (lower metadata_id sorts first).
        # Use a title-only soft match so the row survives filtering with
        # popularity_floor=0 and we can assert purely on ordering.
        cover = "01AAAA000000000000000000A"
        official = "01BBBB000000000000000000B"
        _insert(
            db, cover,
            artist="Some Obscure Band",
            title="In the End (Karaoke Version)",
            listeners=5_000, remix_group=remix,
        )
        _insert(
            db, official,
            artist="Linkin Park", title="In the End",
            listeners=8_000_000, remix_group=remix,
        )

        rows = _fetch_candidate_rows(db)
        out = filter_and_prefer_official(db, rows, popularity_floor=0)
        out_ids = [r["metadata_id"] for r in out]
        assert official in out_ids and cover in out_ids
        assert out_ids.index(official) < out_ids.index(cover)

    def test_include_covers_bypasses_filter(self, db: sqlite3.Connection) -> None:
        cover = generate_metadata_id()
        _insert(db, cover, artist="8-Bit Arcade", title="Numb", listeners=500)
        rows = _fetch_candidate_rows(db)
        out = filter_and_prefer_official(db, rows, include_covers=True)
        assert cover in [r["metadata_id"] for r in out]

    def test_default_popularity_floor_is_1000(self) -> None:
        assert DEFAULT_POPULARITY_FLOOR == 1000

    def test_empty_rows_short_circuits(self, db: sqlite3.Connection) -> None:
        assert filter_and_prefer_official(db, []) == []


@pytest.mark.integration
class TestPlaylistIntegration:
    """End-to-end via generate_playlist: 8-bit cover loses to the real track."""

    def test_genre_playlist_prefers_official(self, db: sqlite3.Connection) -> None:
        from musesleuth.playlist_generator import generate_playlist

        real_mid = generate_metadata_id()
        cover_mid = generate_metadata_id()
        remix = "remix-linkin-end"

        # Cover first so the naive "first-wins" dedup would pick it.
        db.execute(
            "INSERT INTO tracks (metadata_id, title, artist, file_path, filename, full_path) "
            "VALUES (?, 'In the End', '8-Bit Arcade', 'C:\\\\t\\\\', ?, ?)",
            (cover_mid, f"{cover_mid}.mp3", f"C:\\t\\{cover_mid}.mp3"),
        )
        db.execute(
            "INSERT INTO tracks (metadata_id, title, artist, file_path, filename, full_path) "
            "VALUES (?, 'In the End', 'Linkin Park', 'C:\\\\t\\\\', ?, ?)",
            (real_mid, f"{real_mid}.mp3", f"C:\\t\\{real_mid}.mp3"),
        )
        for mid in (cover_mid, real_mid):
            db.execute(
                "INSERT INTO ml_features (metadata_id, genre_primary, genre_confidence) "
                "VALUES (?, 'nu-metal', 0.9)",
                (mid,),
            )
            db.execute(
                "INSERT INTO playlist_signals (metadata_id, remix_group) VALUES (?, ?)",
                (mid, remix),
            )
        db.execute(
            "INSERT INTO track_stats (metadata_id, source, listener_count, play_count) "
            "VALUES (?, 'lastfm', 8000000, 0)",
            (real_mid,),
        )
        db.commit()

        result = generate_playlist(
            db, "genre", "Nu-Metal Test", {"genre": "nu-metal"}
        )
        track_ids = [
            r["metadata_id"]
            for r in db.execute(
                "SELECT metadata_id FROM playlist_tracks WHERE playlist_id = ? ORDER BY position",
                (result.playlist_id,),
            )
        ]
        assert track_ids == [real_mid]

    def test_include_covers_param_allows_tributes(
        self, db: sqlite3.Connection
    ) -> None:
        from musesleuth.playlist_generator import generate_playlist

        cover_mid = generate_metadata_id()
        db.execute(
            "INSERT INTO tracks (metadata_id, title, artist, file_path, filename, full_path) "
            "VALUES (?, 'Numb', '8-Bit Arcade', 'C:\\\\t\\\\', ?, ?)",
            (cover_mid, f"{cover_mid}.mp3", f"C:\\t\\{cover_mid}.mp3"),
        )
        db.execute(
            "INSERT INTO ml_features (metadata_id, genre_primary, genre_confidence) "
            "VALUES (?, 'chiptune', 0.9)",
            (cover_mid,),
        )
        db.execute(
            "INSERT INTO playlist_signals (metadata_id) VALUES (?)",
            (cover_mid,),
        )
        db.commit()

        # Without include_covers the 8-Bit Arcade track is dropped.
        r1 = generate_playlist(db, "genre", "Default", {"genre": "chiptune"})
        assert r1.track_count == 0

        # With include_covers=True, the cover is included.
        r2 = generate_playlist(
            db, "genre", "Covers On",
            {"genre": "chiptune", "include_covers": True},
        )
        assert r2.track_count == 1
