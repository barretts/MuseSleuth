"""End-to-end integration test -- exercises the full pipeline with mocked externals."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from click.testing import CliRunner

from musesleuth.cli import cli
from musesleuth.db import get_connection
from musesleuth.adapters.base import AdapterResult
from musesleuth.bpm_analyzer import BpmResult, KeyResult, AnalysisResult, EnergyResult
from musesleuth.fingerprint import FingerprintResult
from musesleuth.tag_reader import TagSnapshot


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


class TestEndToEnd:
    """Full pipeline test: import -> run all stages -> export."""

    @pytest.mark.e2e
    def test_full_pipeline(self, runner: CliRunner, tmp_dir: Path) -> None:
        # 1. Create a sample CSV with the path ending in backslash
        csv_path = tmp_dir / "tracks.csv"
        dir_str = str(tmp_dir) + "\\"
        csv_path.write_text(
            "Title;Artist;Album;Track;Year;Length;Size;Last Modified;Path;Filename\n"
            f"Test Song;Test Artist;Test Album;1;2000;3:34;5000000;2024-01-01;{dir_str};song.mp3\n",
            encoding="utf-8",
        )

        # Create a dummy mp3 so the file exists
        dummy_mp3 = tmp_dir / "song.mp3"
        frame = b"\xff\xfb\x90\x00" + b"\x00" * 413
        dummy_mp3.write_bytes(frame * 10)

        db_path = tmp_dir / "pipeline.db"

        # 2. Import
        result = runner.invoke(cli, [
            "import", str(csv_path), "--db", str(db_path), "--skip-sidecars"
        ])
        assert result.exit_code == 0, f"Import failed: {result.output}"
        assert "Imported 1" in result.output

        # 3. Build mock return values
        ffprobe_data = {
            "streams": [{"codec_name": "mp3", "bit_rate": "320000",
                         "sample_rate": "44100", "channels": 2}],
            "format": {"duration": "214.5", "tags": {}},
        }

        tag_snap = TagSnapshot(
            tags={"title": "Test Song", "artist": "Test Artist", "album": "Test Album"},
            raw_json="{}",
        )

        bpm_xcheck = AnalysisResult(
            bpm_aubio=128.0, bpm_librosa=128.5, bpm_final=128.0,
            confidence=0.95, disagreement=False,
        )

        key_result = KeyResult(key="A", mode="minor", confidence=0.9)

        fp_result = FingerprintResult(
            fingerprint="AQAA_fake_fingerprint", duration=214,
        )

        lfm_track = AdapterResult(source="lastfm", success=True, data={
            "listeners": 5000, "play_count": 25000,
            "tags": ["trance", "electronic"],
        })
        lfm_artist = AdapterResult(source="lastfm", success=True, data={
            "listeners": 10000, "play_count": 50000,
            "similar_artists": ["DJ X"], "genres": ["trance"],
        })
        mb_track = AdapterResult(source="musicbrainz", success=True, data={
            "recording_id": "rec-e2e-test", "isrcs": ["USXXX0000001"],
            "release_title": "Test Album", "release_country": "US",
            "tags": ["trance"], "artist_country": "US",
        })

        # 4. Run pipeline with all external calls mocked
        with patch("musesleuth.probe_runner._run_ffprobe", return_value=ffprobe_data), \
             patch("musesleuth.probe_runner.hash_partial", return_value="partial_fake"), \
             patch("musesleuth.probe_runner.read_tags", return_value=tag_snap), \
             patch("musesleuth.analyze_runner.analyze_bpm_aubio", return_value=BpmResult(bpm=128.0, confidence=0.9, source="aubio")), \
             patch("musesleuth.analyze_runner.analyze_bpm_librosa", return_value=BpmResult(bpm=128.5, confidence=0.85, source="librosa")), \
             patch("musesleuth.analyze_runner.cross_check_bpm", return_value=bpm_xcheck), \
             patch("musesleuth.analyze_runner.analyze_key", return_value=key_result), \
             patch("musesleuth.analyze_runner.analyze_energy", return_value=EnergyResult(energy=0.5, dynamic_range=5.0, onset_density=2.5, peak_rms=0.1)), \
             patch("musesleuth.analyze_runner.run_fpcalc", return_value=fp_result), \
             patch("musesleuth.analyze_runner.hash_partial", return_value="partial_fake"), \
             patch("musesleuth.analyze_runner.hash_full", return_value="full_fake"), \
             patch("musesleuth.matcher._lookup_acoustid", return_value={"status": "ok", "results": []}), \
             patch("musesleuth.enrich_runner._fetch_lastfm_track", return_value=lfm_track), \
             patch("musesleuth.enrich_runner._fetch_lastfm_artist", return_value=lfm_artist), \
             patch("musesleuth.enrich_runner._fetch_mb_track", return_value=mb_track):
            result = runner.invoke(cli, ["run", "--db", str(db_path)])
        assert result.exit_code == 0, f"Run failed: {result.output}"
        assert "Processed" in result.output

        # 5. Check status
        result = runner.invoke(cli, ["status", "--db", str(db_path)])
        assert result.exit_code == 0
        assert "1" in result.output

        # 6. Export to JSON
        json_out = tmp_dir / "export.json"
        result = runner.invoke(cli, [
            "export", "--db", str(db_path), "--format", "json", "--output", str(json_out)
        ])
        assert result.exit_code == 0
        data = json.loads(json_out.read_text())
        assert len(data) == 1
        assert data[0]["title"] == "Test Song"
        assert data[0]["artist"] == "Test Artist"

        # 7. Export to CSV
        csv_out = tmp_dir / "export.csv"
        result = runner.invoke(cli, [
            "export", "--db", str(db_path), "--format", "csv", "--output", str(csv_out)
        ])
        assert result.exit_code == 0
        content = csv_out.read_text()
        assert "metadata_id" in content
        assert "Test Song" in content

        # 8. Verify DB enrichment data
        conn = get_connection(db_path)

        # Track stats from Last.fm
        ts = conn.execute("SELECT * FROM track_stats").fetchone()
        assert ts is not None
        assert ts["listener_count"] == 5000

        # Genres from enrichment
        tags = conn.execute("SELECT tag_value FROM genres_tags").fetchall()
        tag_vals = {r["tag_value"] for r in tags}
        assert "trance" in tag_vals

        # External IDs from MusicBrainz
        eids = conn.execute("SELECT source, external_id FROM external_ids").fetchall()
        sources = {r["source"] for r in eids}
        assert "musicbrainz" in sources

        # Playlist signals derived
        ps = conn.execute("SELECT * FROM playlist_signals").fetchone()
        assert ps is not None
        assert ps["decade_bucket"] == "2000s"
        assert ps["camelot_key"] == "8A"
        assert ps["energy_tier"] == "medium"
        assert ps["popularity_tier"] is not None

        conn.close()
