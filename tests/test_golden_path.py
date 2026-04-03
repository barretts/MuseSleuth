"""Golden path test -- single file through every pipeline stage, CLI command, and DB table."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from click.testing import CliRunner

from musesleuth.cli import cli
from musesleuth.db import get_connection, create_schema, TABLE_NAMES
from musesleuth.adapters.base import AdapterResult
from musesleuth.bpm_analyzer import BpmResult, KeyResult, AnalysisResult, EnergyResult
from musesleuth.fingerprint import FingerprintResult
from musesleuth.tag_reader import TagSnapshot
from musesleuth.dedup import assign_duplicate_groups


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# ---------------------------------------------------------------------------
# Mock return values shared across the golden-path test
# ---------------------------------------------------------------------------

FFPROBE_DATA = {
    "streams": [{"codec_type": "audio", "codec_name": "mp3",
                  "bit_rate": "320000", "sample_rate": "44100", "channels": 2}],
    "format": {"duration": "214.5", "bit_rate": "320000", "tags": {}},
}

TAG_SNAP = TagSnapshot(
    tags={"title": "Golden Song", "artist": "Golden Artist", "album": "Golden Album"},
    raw_json='{"title": "Golden Song"}',
)

BPM_AUBIO = BpmResult(bpm=138.0, confidence=0.9, source="aubio")
BPM_LIBROSA = BpmResult(bpm=138.5, confidence=0.85, source="librosa")
BPM_XCHECK = AnalysisResult(
    bpm_aubio=138.0, bpm_librosa=138.5, bpm_final=138.0,
    confidence=0.9, disagreement=False,
)
KEY_RESULT = KeyResult(key="A", mode="minor", confidence=0.9)
ENERGY_RESULT = EnergyResult(energy=0.55, dynamic_range=5.8, onset_density=2.9, peak_rms=0.15)
FP_RESULT = FingerprintResult(fingerprint="AQAA_golden_fingerprint", duration=214)

LFM_TRACK = AdapterResult(source="lastfm", success=True, data={
    "listeners": 5000, "play_count": 25000,
    "tags": ["trance", "electronic"],
})
LFM_ARTIST = AdapterResult(source="lastfm", success=True, data={
    "listeners": 10000, "play_count": 50000,
    "similar_artists": ["DJ X"], "genres": ["trance"],
})
MB_TRACK = AdapterResult(source="musicbrainz", success=True, data={
    "recording_id": "rec-golden-test", "isrcs": ["USXXX0000001"],
    "release_title": "Golden Album", "release_country": "US",
    "tags": ["trance"], "artist_country": "US",
})

ACOUSTID_RESPONSE = {
    "status": "ok",
    "results": [{
        "score": 0.95,
        "recordings": [{
            "id": "acoustid-rec-golden",
            "title": "Golden Song",
            "artists": [{"name": "Golden Artist"}],
            "duration": 214,
            "releases": [{"title": "Golden Album", "country": "US"}],
        }],
    }],
}


def _probe_patches():
    """Context managers that mock all external calls for the probe stage."""
    return [
        patch("musesleuth.probe_runner._run_ffprobe", return_value=FFPROBE_DATA),
        patch("musesleuth.probe_runner.hash_partial", return_value="partial_golden"),
        patch("musesleuth.probe_runner.read_tags", return_value=TAG_SNAP),
    ]


def _analyze_patches():
    """Context managers that mock all external calls for the analyze stage."""
    return [
        patch("musesleuth.analyze_runner.analyze_bpm_aubio", return_value=BPM_AUBIO),
        patch("musesleuth.analyze_runner.analyze_bpm_librosa", return_value=BPM_LIBROSA),
        patch("musesleuth.analyze_runner.cross_check_bpm", return_value=BPM_XCHECK),
        patch("musesleuth.analyze_runner.analyze_key", return_value=KEY_RESULT),
        patch("musesleuth.analyze_runner.analyze_energy", return_value=ENERGY_RESULT),
        patch("musesleuth.analyze_runner.run_fpcalc", return_value=FP_RESULT),
        patch("musesleuth.analyze_runner.hash_partial", return_value="partial_golden"),
        patch("musesleuth.analyze_runner.hash_full", return_value="full_golden"),
    ]


def _match_patches():
    return [
        patch("musesleuth.matcher._lookup_acoustid", return_value=ACOUSTID_RESPONSE),
    ]


def _enrich_patches():
    return [
        patch("musesleuth.enrich_runner._fetch_lastfm_track", return_value=LFM_TRACK),
        patch("musesleuth.enrich_runner._fetch_lastfm_artist", return_value=LFM_ARTIST),
        patch("musesleuth.enrich_runner._fetch_mb_track", return_value=MB_TRACK),
    ]


def _all_run_patches():
    """All patches needed for `musicmeta run` (all stages)."""
    return _probe_patches() + _analyze_patches() + _match_patches() + _enrich_patches()


class TestGoldenPath:
    """Drive a single track through every feature in MuseSleuth."""

    @pytest.mark.e2e
    def test_single_file_start_to_finish(self, runner: CliRunner, tmp_dir: Path) -> None:
        # ---------------------------------------------------------------
        # 1. CSV + Sidecar import
        # ---------------------------------------------------------------
        csv_path = tmp_dir / "tracks.csv"
        dir_str = str(tmp_dir) + "\\"
        csv_path.write_text(
            "Title;Artist;Album;Track;Year;Length;Size;Last Modified;Path;Filename\n"
            f"Golden Song;Golden Artist;Golden Album;1;2000;3:34;5000000;2024-01-01;{dir_str};golden.mp3\n",
            encoding="utf-8",
        )

        # Create a minimal valid-ish MP3 file (MPEG sync bytes)
        dummy_mp3 = tmp_dir / "golden.mp3"
        frame = b"\xff\xfb\x90\x00" + b"\x00" * 413
        dummy_mp3.write_bytes(frame * 10)

        db_path = tmp_dir / "golden_path.db"

        # Import WITHOUT --skip-sidecars so sidecar is created
        result = runner.invoke(cli, [
            "import", str(csv_path), "--db", str(db_path),
        ])
        assert result.exit_code == 0, f"Import failed: {result.output}"
        assert "Imported 1" in result.output

        conn = get_connection(db_path)

        # Verify tracks row
        track = conn.execute("SELECT * FROM tracks").fetchone()
        assert track is not None
        assert track["title"] == "Golden Song"
        metadata_id = track["metadata_id"]

        # Verify sidecar file was created on disk
        sidecar_file = Path(str(dummy_mp3) + ".dlpmeta")
        assert sidecar_file.exists(), "Sidecar file should exist after import"

        # Verify track_sidecars row
        sc_row = conn.execute(
            "SELECT * FROM track_sidecars WHERE metadata_id = ?", (metadata_id,)
        ).fetchone()
        assert sc_row is not None

        # Verify jobs seeded (8 stages)
        job_count = conn.execute("SELECT COUNT(*) as cnt FROM jobs").fetchone()["cnt"]
        assert job_count == 8

        conn.close()

        # ---------------------------------------------------------------
        # 2. Probe stage
        # ---------------------------------------------------------------
        patches = _probe_patches()
        for p in patches:
            p.start()
        try:
            result = runner.invoke(cli, [
                "run", "--db", str(db_path), "--stage", "probe",
            ])
        finally:
            for p in patches:
                p.stop()

        assert result.exit_code == 0, f"Probe failed: {result.output}"
        assert "Processed 1" in result.output

        conn = get_connection(db_path)

        tf = conn.execute(
            "SELECT * FROM technical_features WHERE metadata_id = ?", (metadata_id,)
        ).fetchone()
        assert tf is not None
        assert tf["codec"] == "mp3"
        assert tf["bitrate"] == 320000

        ts_raw = conn.execute(
            "SELECT * FROM tag_snapshot_raw WHERE metadata_id = ?", (metadata_id,)
        ).fetchone()
        assert ts_raw is not None
        assert "Golden Song" in ts_raw["tags_json"]

        conn.close()

        # ---------------------------------------------------------------
        # 3. Analyze stage
        # ---------------------------------------------------------------
        patches = _analyze_patches()
        for p in patches:
            p.start()
        try:
            result = runner.invoke(cli, [
                "run", "--db", str(db_path), "--stage", "analyze",
            ])
        finally:
            for p in patches:
                p.stop()

        assert result.exit_code == 0, f"Analyze failed: {result.output}"
        assert "Processed 1" in result.output

        conn = get_connection(db_path)

        mf = conn.execute(
            "SELECT * FROM musical_features WHERE metadata_id = ?", (metadata_id,)
        ).fetchone()
        assert mf is not None
        assert mf["bpm_final"] == 138.0
        assert mf["key_name"] == "A"
        assert mf["key_mode"] == "minor"
        assert mf["bpm_disagreement"] == 0

        sc_updated = conn.execute(
            "SELECT * FROM track_sidecars WHERE metadata_id = ?", (metadata_id,)
        ).fetchone()
        assert sc_updated["fingerprint"] == "AQAA_golden_fingerprint"
        assert sc_updated["hash_full"] == "full_golden"

        conn.close()

        # ---------------------------------------------------------------
        # 4. Fingerprint Match stage
        # ---------------------------------------------------------------
        patches = _match_patches()
        for p in patches:
            p.start()
        try:
            result = runner.invoke(cli, [
                "run", "--db", str(db_path), "--stage", "fingerprint_match",
            ])
        finally:
            for p in patches:
                p.stop()

        assert result.exit_code == 0, f"Match failed: {result.output}"
        assert "Processed 1" in result.output

        conn = get_connection(db_path)

        eids = conn.execute(
            "SELECT source, external_id FROM external_ids WHERE metadata_id = ?",
            (metadata_id,),
        ).fetchall()
        sources = {r["source"] for r in eids}
        assert "musicbrainz" in sources
        assert "acoustid" in sources

        conn.close()

        # ---------------------------------------------------------------
        # 5. Enrich stage
        # ---------------------------------------------------------------
        patches = _enrich_patches()
        for p in patches:
            p.start()
        try:
            result = runner.invoke(cli, [
                "run", "--db", str(db_path), "--stage", "enrich",
            ])
        finally:
            for p in patches:
                p.stop()

        assert result.exit_code == 0, f"Enrich failed: {result.output}"
        assert "Processed 1" in result.output

        conn = get_connection(db_path)

        # track_stats
        ts = conn.execute(
            "SELECT * FROM track_stats WHERE metadata_id = ?", (metadata_id,)
        ).fetchone()
        assert ts is not None
        assert ts["listener_count"] == 5000
        assert ts["play_count"] == 25000

        # artist_stats
        arts = conn.execute(
            "SELECT * FROM artist_stats WHERE metadata_id = ?", (metadata_id,)
        ).fetchall()
        assert len(arts) >= 1
        lfm_art = [a for a in arts if a["source"] == "lastfm"]
        assert len(lfm_art) == 1

        # genres_tags
        tags = conn.execute(
            "SELECT tag_value FROM genres_tags WHERE metadata_id = ?", (metadata_id,)
        ).fetchall()
        tag_vals = {r["tag_value"] for r in tags}
        assert "trance" in tag_vals
        assert "electronic" in tag_vals

        # external_ids from enrich (MusicBrainz recording + ISRCs)
        eids_all = conn.execute(
            "SELECT source, external_id FROM external_ids WHERE metadata_id = ?",
            (metadata_id,),
        ).fetchall()
        isrc_rows = [r for r in eids_all if r["source"] == "isrc"]
        assert len(isrc_rows) >= 1

        conn.close()

        # ---------------------------------------------------------------
        # 6. Derive Signals stage
        # ---------------------------------------------------------------
        result = runner.invoke(cli, [
            "run", "--db", str(db_path), "--stage", "derive_signals",
        ])
        assert result.exit_code == 0, f"Derive signals failed: {result.output}"
        assert "Processed 1" in result.output

        conn = get_connection(db_path)

        ps = conn.execute(
            "SELECT * FROM playlist_signals WHERE metadata_id = ?", (metadata_id,)
        ).fetchone()
        assert ps is not None
        assert ps["decade_bucket"] == "2000s"
        assert ps["bpm_bucket"] == "fast"  # 138 BPM -> fast (120-150)
        assert ps["camelot_key"] == "8A"   # A minor -> 8A
        assert ps["popularity_tier"] == "mainstream"  # 5000 listeners

        conn.close()

        # ---------------------------------------------------------------
        # 7. Status command
        # ---------------------------------------------------------------
        result = runner.invoke(cli, ["status", "--db", str(db_path)])
        assert result.exit_code == 0
        assert "Tracks: 1" in result.output
        assert "Done:" in result.output

        # ---------------------------------------------------------------
        # 8. Dashboard command
        # ---------------------------------------------------------------
        result = runner.invoke(cli, ["dashboard", "--db", str(db_path)])
        assert result.exit_code == 0

        # ---------------------------------------------------------------
        # 9. Retry flow -- fail writeback job, retry, re-run
        # ---------------------------------------------------------------
        conn = get_connection(db_path)
        conn.execute(
            "UPDATE jobs SET status = 'failed', last_error = 'test failure' "
            "WHERE metadata_id = ? AND stage = 'writeback'",
            (metadata_id,),
        )
        conn.commit()
        conn.close()

        result = runner.invoke(cli, [
            "retry", "--db", str(db_path), "--stage", "writeback",
        ])
        assert result.exit_code == 0
        assert "Retried 1" in result.output

        # Verify job is back to pending
        conn = get_connection(db_path)
        wb_job = conn.execute(
            "SELECT status FROM jobs WHERE metadata_id = ? AND stage = 'writeback'",
            (metadata_id,),
        ).fetchone()
        assert wb_job["status"] == "pending"
        conn.close()

        # Re-run writeback stage (it's a pass-through in pipeline.py)
        result = runner.invoke(cli, [
            "run", "--db", str(db_path), "--stage", "writeback",
        ])
        assert result.exit_code == 0

        # ---------------------------------------------------------------
        # 10. Writeback command (write BPM/genre/key into the MP3)
        # ---------------------------------------------------------------
        with patch("musesleuth.tag_writer.MP3") as mock_mp3:
            mock_audio = MagicMock()
            mock_audio.tags = MagicMock()
            mock_mp3.return_value = mock_audio

            result = runner.invoke(cli, [
                "writeback", "--db", str(db_path), "--fields", "bpm,genre,key",
            ])
        assert result.exit_code == 0, f"Writeback failed: {result.output}"
        assert "written" in result.output.lower()

        # Verify tag_writeback_log has entries
        conn = get_connection(db_path)
        wb_logs = conn.execute(
            "SELECT * FROM tag_writeback_log WHERE metadata_id = ?", (metadata_id,)
        ).fetchall()
        assert len(wb_logs) >= 1
        fields_logged = {r["field_name"] for r in wb_logs}
        assert "bpm" in fields_logged
        conn.close()

        # ---------------------------------------------------------------
        # 11. Dedup -- import a second track with same hash, run dedup
        # ---------------------------------------------------------------
        csv_path_2 = tmp_dir / "tracks2.csv"
        csv_path_2.write_text(
            "Title;Artist;Album;Track;Year;Length;Size;Last Modified;Path;Filename\n"
            f"Golden Song (Remix);Golden Artist;Golden Album;2;2000;3:35;5100000;2024-01-02;{dir_str};golden_remix.mp3\n",
            encoding="utf-8",
        )
        dummy_mp3_2 = tmp_dir / "golden_remix.mp3"
        dummy_mp3_2.write_bytes(frame * 10)

        result = runner.invoke(cli, [
            "import", str(csv_path_2), "--db", str(db_path), "--skip-sidecars",
        ])
        assert result.exit_code == 0
        assert "Imported 1" in result.output

        conn = get_connection(db_path)
        track2 = conn.execute(
            "SELECT metadata_id FROM tracks WHERE filename = 'golden_remix.mp3'"
        ).fetchone()
        assert track2 is not None
        mid2 = track2["metadata_id"]

        # Give track2 the same partial hash + a fingerprint so matcher & dedup work
        conn.execute(
            """
            INSERT OR REPLACE INTO track_sidecars
                (metadata_id, sidecar_path, hash_partial, hash_full, fingerprint)
            VALUES (?, ?, ?, ?, ?)
            """,
            (mid2, str(dummy_mp3_2) + ".dlpmeta", "partial_golden", "full_golden",
             "AQAA_golden_fingerprint"),
        )
        # Run analyze mock data for track2 so derive_signals can work
        conn.execute(
            """
            INSERT OR REPLACE INTO musical_features
                (metadata_id, bpm_aubio, bpm_librosa, bpm_final, bpm_confidence,
                 bpm_disagreement, key_name, key_mode, analyzed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (mid2, 138.0, 138.5, 138.0, 0.9, 0, "A", "minor"),
        )
        # Insert track_stats for track2 so popularity_tier resolves
        conn.execute(
            """
            INSERT OR REPLACE INTO track_stats
                (metadata_id, source, listener_count, play_count)
            VALUES (?, ?, ?, ?)
            """,
            (mid2, "lastfm", 5000, 25000),
        )
        # Also add a technical_features row for track2 (needed for fuzzy dedup duration)
        conn.execute(
            """
            INSERT OR REPLACE INTO technical_features
                (metadata_id, codec, bitrate, sample_rate, channels, duration_ms)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (mid2, "mp3", 320000, 44100, 2, 215000),
        )
        conn.commit()

        # Derive signals for track2 so playlist_signals row exists for dedup to update
        from musesleuth.signals import run_derive_signals_for_track
        run_derive_signals_for_track(conn, mid2)

        # Run dedup
        dup_assigned, _remix_assigned = assign_duplicate_groups(conn)
        assert dup_assigned >= 1, "Should detect at least one duplicate group"

        # Verify duplicate_group set on both tracks
        ps1 = conn.execute(
            "SELECT duplicate_group FROM playlist_signals WHERE metadata_id = ?",
            (metadata_id,),
        ).fetchone()
        ps2 = conn.execute(
            "SELECT duplicate_group FROM playlist_signals WHERE metadata_id = ?",
            (mid2,),
        ).fetchone()
        assert ps1["duplicate_group"] is not None
        assert ps2["duplicate_group"] is not None
        assert ps1["duplicate_group"] == ps2["duplicate_group"]

        conn.close()

        # ---------------------------------------------------------------
        # 12. Export -- JSON and CSV
        # ---------------------------------------------------------------
        json_out = tmp_dir / "export.json"
        result = runner.invoke(cli, [
            "export", "--db", str(db_path), "--format", "json", "--output", str(json_out),
        ])
        assert result.exit_code == 0
        data = json.loads(json_out.read_text())
        assert len(data) == 2  # both tracks exported
        golden = [d for d in data if d["title"] == "Golden Song"][0]
        assert golden["bpm"] == 138.0
        assert golden["camelot_key"] == "8A"
        assert golden["decade"] == "2000s"

        csv_out = tmp_dir / "export.csv"
        result = runner.invoke(cli, [
            "export", "--db", str(db_path), "--format", "csv", "--output", str(csv_out),
        ])
        assert result.exit_code == 0
        csv_content = csv_out.read_text()
        assert "metadata_id" in csv_content
        assert "Golden Song" in csv_content

        # ---------------------------------------------------------------
        # 13. Final DB audit -- every table has rows for the primary track
        # ---------------------------------------------------------------
        conn = get_connection(db_path)

        # Tables that key on metadata_id directly
        fk_tables = [
            "tracks", "track_sidecars", "technical_features",
            "musical_features", "tag_snapshot_raw", "playlist_signals",
        ]
        for table in fk_tables:
            row = conn.execute(
                f"SELECT COUNT(*) as cnt FROM {table} WHERE metadata_id = ?",
                (metadata_id,),
            ).fetchone()
            assert row["cnt"] >= 1, f"Table {table} missing row for {metadata_id}"

        # Tables with FK but possibly multiple rows
        multi_tables = [
            "external_ids", "artist_stats", "track_stats", "genres_tags",
            "tag_writeback_log",
        ]
        for table in multi_tables:
            row = conn.execute(
                f"SELECT COUNT(*) as cnt FROM {table} WHERE metadata_id = ?",
                (metadata_id,),
            ).fetchone()
            assert row["cnt"] >= 1, f"Table {table} missing row for {metadata_id}"

        # Jobs table: all 8 stages should exist
        jobs_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM jobs WHERE metadata_id = ?",
            (metadata_id,),
        ).fetchone()["cnt"]
        assert jobs_count == 8

        # scraper_cache may or may not have rows (adapters were mocked at a high level)
        # but the table must exist
        conn.execute("SELECT COUNT(*) FROM scraper_cache")

        # Verify all 13 tables exist
        existing_tables = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
        for table_name in TABLE_NAMES:
            assert table_name in existing_tables, f"Missing table: {table_name}"

        conn.close()
