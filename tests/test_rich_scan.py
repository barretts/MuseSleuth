"""Tests for rich tag extraction, lyric sidecar discovery, and raw tag blob at scan time."""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from musesleuth.db import create_schema, generate_metadata_id
from musesleuth.scanner import _read_tags, _process_scan_file, scan_directory, ScanResult


@pytest.fixture
def db(in_memory_db: sqlite3.Connection) -> sqlite3.Connection:
    create_schema(in_memory_db)
    return in_memory_db


# ---------------------------------------------------------------------------
# _read_tags: rich field extraction
# ---------------------------------------------------------------------------

class TestReadTagsRichFields:
    """Verify _read_tags extracts genre, album_artist, disc_number, etc."""

    @pytest.mark.unit
    def test_returns_all_rich_fields(self, tmp_path: Path) -> None:
        """_read_tags result dict contains all rich field keys."""
        audio = tmp_path / "silence.flac"
        audio.write_bytes(b"")  # empty file — mutagen will fail gracefully

        result = _read_tags(audio)

        for key in ("genre", "album_artist", "disc_number", "total_tracks",
                     "original_year", "label", "embedded_ids", "raw_tags_json"):
            assert key in result, f"Missing key: {key}"

    @pytest.mark.unit
    def test_vorbis_field_mapping(self) -> None:
        """Simulate Vorbis comment tags and check field mapping."""
        fake_tags = {
            "title": ["(sic)"],
            "artist": ["Slipknot"],
            "album": ["Slipknot (25th anniversary edition)"],
            "date": ["2025-09-05"],
            "tracknumber": ["2"],
            "genre": ["Rock"],
            "albumartist": ["Slipknot"],
            "discnumber": ["1"],
            "tracktotal": ["32"],
            "originaldate": ["1999-06-29"],
            "label": ["Roadrunner Records"],
            "musicbrainz_trackid": ["0ce3ae0e-8c21-4da6-9015-22c57359a703"],
            "musicbrainz_artistid": ["a466c2a2-6517-42fb-a160-1087c3bafd9f"],
            "musicbrainz_albumid": ["b4a9e088-3b30-48f0-b8b2-9c0e0592ff40"],
            "isrc": ["NLA321400509", "NLA329980192"],
        }

        mock_audio = MagicMock()
        mock_audio.info.length = 300
        mock_audio.tags = MagicMock()
        mock_audio.tags.__iter__ = MagicMock(return_value=iter([]))
        mock_audio.tags.getall = None  # not ID3
        mock_audio.tags.items = MagicMock(return_value=fake_tags.items())
        delattr(mock_audio.tags, "getall")

        with patch("musesleuth.scanner.mutagen.File", return_value=mock_audio):
            result = _read_tags(Path("fake.flac"))

        assert result["title"] == "(sic)"
        assert result["artist"] == "Slipknot"
        assert result["genre"] == "Rock"
        assert result["album_artist"] == "Slipknot"
        assert result["disc_number"] == "1"
        assert result["total_tracks"] == "32"
        assert result["original_year"] == "1999-06-29"
        assert result["label"] == "Roadrunner Records"

        # Embedded IDs
        ids = result["embedded_ids"]
        assert "musicbrainz" in ids
        assert "0ce3ae0e-8c21-4da6-9015-22c57359a703" in ids["musicbrainz"]
        assert "musicbrainz_artist" in ids
        assert "musicbrainz_album" in ids

        # Raw tags blob
        raw = json.loads(result["raw_tags_json"])
        assert "title" in raw
        assert "musicbrainz_trackid" in raw

    @pytest.mark.unit
    def test_track_number_slash_split(self) -> None:
        """Track number '2/32' should split into track_number=2, total_tracks=32."""
        fake_tags = {"tracknumber": ["2/32"]}
        mock_audio = MagicMock()
        mock_audio.info.length = 100
        mock_audio.tags = MagicMock()
        mock_audio.tags.items = MagicMock(return_value=fake_tags.items())
        delattr(mock_audio.tags, "getall")

        with patch("musesleuth.scanner.mutagen.File", return_value=mock_audio):
            result = _read_tags(Path("fake.mp3"))

        assert result["track_number"] == "2"
        assert result["total_tracks"] == "32"


# ---------------------------------------------------------------------------
# _process_scan_file: lyric sidecar discovery
# ---------------------------------------------------------------------------

class TestProcessScanFileLyrics:
    """Verify _process_scan_file discovers .lrc lyric sidecars."""

    @pytest.mark.unit
    def test_discovers_lrc_sidecar(self, tmp_path: Path) -> None:
        """When a .lrc file exists beside the audio, lyrics_path is set."""
        audio = tmp_path / "song.flac"
        audio.write_bytes(b"\x00" * 100)
        lrc = tmp_path / "song.lrc"
        lrc.write_text("Some lyrics here", encoding="utf-8")

        with patch("musesleuth.scanner._read_tags") as mock_tags, \
             patch("musesleuth.scanner.hash_partial", return_value="abc123"):
            mock_tags.return_value = {
                "title": "Song", "artist": "Artist", "album": "Album",
                "year": "2024", "track_number": "1", "length_seconds": "200",
                "file_size": "100", "genre": "Rock", "album_artist": "",
                "disc_number": "", "total_tracks": "", "original_year": "",
                "label": "", "embedded_ids": {}, "raw_tags_json": "{}",
            }
            status, rec = _process_scan_file(audio)

        assert status == "new"
        assert rec is not None
        assert rec.lyrics_path is not None
        assert rec.lyrics_path.endswith(".lrc")

    @pytest.mark.unit
    def test_no_lrc_no_lyrics(self, tmp_path: Path) -> None:
        """When no .lrc file exists, lyrics_path is None."""
        audio = tmp_path / "song.flac"
        audio.write_bytes(b"\x00" * 100)

        with patch("musesleuth.scanner._read_tags") as mock_tags, \
             patch("musesleuth.scanner.hash_partial", return_value="abc123"):
            mock_tags.return_value = {
                "title": "Song", "artist": "Artist", "album": "Album",
                "year": "2024", "track_number": "1", "length_seconds": "200",
                "file_size": "100", "genre": "", "album_artist": "",
                "disc_number": "", "total_tracks": "", "original_year": "",
                "label": "", "embedded_ids": {}, "raw_tags_json": "{}",
            }
            status, rec = _process_scan_file(audio)

        assert status == "new"
        assert rec is not None
        assert rec.lyrics_path is None


# ---------------------------------------------------------------------------
# scan_directory: DB writes for rich tags, external IDs, lyrics, raw blob
# ---------------------------------------------------------------------------

class TestScanDirectoryRichWrites:
    """Verify scan_directory persists rich tag data to the DB."""

    @pytest.mark.integration
    def test_writes_rich_columns(self, db: sqlite3.Connection, tmp_path: Path) -> None:
        """New track columns (genre, album_artist, etc.) are written."""
        audio = tmp_path / "song.flac"
        audio.write_bytes(b"\x00" * 100)

        tags = {
            "title": "Song", "artist": "Artist", "album": "Album",
            "year": "2024", "track_number": "1", "length_seconds": "200",
            "file_size": "100", "genre": "Industrial", "album_artist": "NIN",
            "disc_number": "1", "total_tracks": "10", "original_year": "1989",
            "label": "TVT Records", "embedded_ids": {}, "raw_tags_json": '{"title": "Song"}',
        }

        with patch("musesleuth.scanner._read_tags", return_value=tags), \
             patch("musesleuth.scanner.hash_partial", return_value="h1"), \
             patch("musesleuth.scanner.write_sidecar"):
            result = scan_directory(db, tmp_path)

        assert result.imported == 1

        row = db.execute("SELECT genre, album_artist, disc_number, total_tracks, original_year, label FROM tracks").fetchone()
        assert row["genre"] == "Industrial"
        assert row["album_artist"] == "NIN"
        assert row["disc_number"] == "1"
        assert row["total_tracks"] == "10"
        assert row["original_year"] == "1989"
        assert row["label"] == "TVT Records"

    @pytest.mark.integration
    def test_writes_tag_snapshot_raw(self, db: sqlite3.Connection, tmp_path: Path) -> None:
        """Raw tag JSON blob is saved to tag_snapshot_raw at scan time."""
        audio = tmp_path / "song.flac"
        audio.write_bytes(b"\x00" * 100)

        raw_json = json.dumps({"title": "Song", "custom_tag": "value"})
        tags = {
            "title": "Song", "artist": "A", "album": "", "year": "",
            "track_number": "", "length_seconds": "", "file_size": "100",
            "genre": "", "album_artist": "", "disc_number": "",
            "total_tracks": "", "original_year": "", "label": "",
            "embedded_ids": {}, "raw_tags_json": raw_json,
        }

        with patch("musesleuth.scanner._read_tags", return_value=tags), \
             patch("musesleuth.scanner.hash_partial", return_value="h1"), \
             patch("musesleuth.scanner.write_sidecar"):
            scan_directory(db, tmp_path)

        row = db.execute("SELECT tags_json FROM tag_snapshot_raw").fetchone()
        assert row is not None
        parsed = json.loads(row["tags_json"])
        assert parsed["custom_tag"] == "value"

    @pytest.mark.integration
    def test_writes_embedded_external_ids(self, db: sqlite3.Connection, tmp_path: Path) -> None:
        """Embedded MusicBrainz IDs from tags are saved to external_ids."""
        audio = tmp_path / "song.flac"
        audio.write_bytes(b"\x00" * 100)

        tags = {
            "title": "Song", "artist": "A", "album": "", "year": "",
            "track_number": "", "length_seconds": "", "file_size": "100",
            "genre": "", "album_artist": "", "disc_number": "",
            "total_tracks": "", "original_year": "", "label": "",
            "embedded_ids": {
                "musicbrainz": ["rec-123"],
                "musicbrainz_artist": ["art-456"],
                "embedded_isrc": ["US1234567890"],
            },
            "raw_tags_json": "{}",
        }

        with patch("musesleuth.scanner._read_tags", return_value=tags), \
             patch("musesleuth.scanner.hash_partial", return_value="h1"), \
             patch("musesleuth.scanner.write_sidecar"):
            scan_directory(db, tmp_path)

        rows = db.execute("SELECT source, external_id FROM external_ids ORDER BY source").fetchall()
        sources = {r["source"]: r["external_id"] for r in rows}
        assert sources["musicbrainz"] == "rec-123"
        assert sources["musicbrainz_artist"] == "art-456"
        assert sources["embedded_isrc"] == "US1234567890"

    @pytest.mark.integration
    def test_writes_embedded_genre_tag(self, db: sqlite3.Connection, tmp_path: Path) -> None:
        """Embedded genre tag is saved to genres_tags with source='embedded'."""
        audio = tmp_path / "song.flac"
        audio.write_bytes(b"\x00" * 100)

        tags = {
            "title": "Song", "artist": "A", "album": "", "year": "",
            "track_number": "", "length_seconds": "", "file_size": "100",
            "genre": "Industrial", "album_artist": "", "disc_number": "",
            "total_tracks": "", "original_year": "", "label": "",
            "embedded_ids": {}, "raw_tags_json": "{}",
        }

        with patch("musesleuth.scanner._read_tags", return_value=tags), \
             patch("musesleuth.scanner.hash_partial", return_value="h1"), \
             patch("musesleuth.scanner.write_sidecar"):
            scan_directory(db, tmp_path)

        row = db.execute(
            "SELECT tag_value, source FROM genres_tags WHERE tag_type = 'genre'"
        ).fetchone()
        assert row is not None
        assert row["tag_value"] == "Industrial"
        assert row["source"] == "embedded"

    @pytest.mark.integration
    def test_writes_lyric_sidecar(self, db: sqlite3.Connection, tmp_path: Path) -> None:
        """Lyric sidecar .lrc file is recorded in track_lyrics."""
        audio = tmp_path / "song.flac"
        audio.write_bytes(b"\x00" * 100)
        lrc = tmp_path / "song.lrc"
        lrc.write_text("Lyrics content here", encoding="utf-8")

        tags = {
            "title": "Song", "artist": "A", "album": "", "year": "",
            "track_number": "", "length_seconds": "", "file_size": "100",
            "genre": "", "album_artist": "", "disc_number": "",
            "total_tracks": "", "original_year": "", "label": "",
            "embedded_ids": {}, "raw_tags_json": "{}",
        }

        with patch("musesleuth.scanner._read_tags", return_value=tags), \
             patch("musesleuth.scanner.hash_partial", return_value="h1"), \
             patch("musesleuth.scanner.write_sidecar"):
            result = scan_directory(db, tmp_path)

        assert result.lyrics_found == 1

        row = db.execute("SELECT lyrics_path, lyrics_type FROM track_lyrics").fetchone()
        assert row is not None
        assert row["lyrics_path"].endswith("song.lrc")
        assert row["lyrics_type"] == "lrc"

    @pytest.mark.integration
    def test_no_lrc_no_track_lyrics_row(self, db: sqlite3.Connection, tmp_path: Path) -> None:
        """Without an .lrc file, no track_lyrics row is created."""
        audio = tmp_path / "song.flac"
        audio.write_bytes(b"\x00" * 100)

        tags = {
            "title": "Song", "artist": "A", "album": "", "year": "",
            "track_number": "", "length_seconds": "", "file_size": "100",
            "genre": "", "album_artist": "", "disc_number": "",
            "total_tracks": "", "original_year": "", "label": "",
            "embedded_ids": {}, "raw_tags_json": "{}",
        }

        with patch("musesleuth.scanner._read_tags", return_value=tags), \
             patch("musesleuth.scanner.hash_partial", return_value="h1"), \
             patch("musesleuth.scanner.write_sidecar"):
            result = scan_directory(db, tmp_path)

        assert result.lyrics_found == 0
        assert db.execute("SELECT COUNT(*) FROM track_lyrics").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# Enrich runner: skip MusicBrainz when embedded IDs present
# ---------------------------------------------------------------------------

class TestEnrichSkipMusicBrainz:
    """Verify enrichment skips MusicBrainz when embedded IDs exist."""

    @pytest.mark.integration
    def test_skips_mb_track_when_embedded(self, db: sqlite3.Connection) -> None:
        from musesleuth.enrich_runner import run_enrich_for_track
        from musesleuth.adapters.base import AdapterResult

        mid = generate_metadata_id()
        db.execute(
            "INSERT INTO tracks (metadata_id, title, artist, file_path, filename, full_path) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (mid, "Song", "Artist", "C:\\t\\", "s.mp3", f"C:\\t\\{mid}.mp3"),
        )
        db.execute(
            "INSERT INTO jobs (metadata_id, stage, status) VALUES (?, 'enrich', 'pending')",
            (mid,),
        )
        # Simulate embedded MusicBrainz recording ID from scan
        db.execute(
            "INSERT INTO external_ids (metadata_id, source, external_id, confidence) "
            "VALUES (?, 'musicbrainz', 'rec-embedded', 1.0)",
            (mid,),
        )
        db.execute(
            "INSERT INTO external_ids (metadata_id, source, external_id, confidence) "
            "VALUES (?, 'musicbrainz_artist', 'art-embedded', 1.0)",
            (mid,),
        )
        db.commit()

        mock_lfm = AdapterResult(source="lastfm", success=False, data={})

        with patch("musesleuth.enrich_runner._fetch_lastfm_track", return_value=mock_lfm), \
             patch("musesleuth.enrich_runner._fetch_lastfm_artist", return_value=mock_lfm), \
             patch("musesleuth.enrich_runner._fetch_mb_track") as mb_track_mock, \
             patch("musesleuth.enrich_runner._fetch_mb_artist") as mb_artist_mock:
            run_enrich_for_track(db, mid)

        # MusicBrainz fetchers should NOT have been called
        mb_track_mock.assert_not_called()
        mb_artist_mock.assert_not_called()

        # Embedded IDs should still be there
        rows = db.execute(
            "SELECT source, external_id FROM external_ids WHERE metadata_id = ? ORDER BY source",
            (mid,),
        ).fetchall()
        sources = {r["source"]: r["external_id"] for r in rows}
        assert sources["musicbrainz"] == "rec-embedded"
        assert sources["musicbrainz_artist"] == "art-embedded"
