from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from musesleuth.cli import cli
from musesleuth.db import create_schema, generate_metadata_id
from musesleuth.subsonic import (
    IndexedSong,
    SongIndex,
    SyncResult,
    delete_playlist_with_remote,
    local_path_candidates,
    resolve_track_to_song_id,
    sync_playlist_to_subsonic,
)
from musesleuth.web import create_app


@pytest.fixture
def db(tmp_path: Path) -> tuple[sqlite3.Connection, Path]:
    db_path = tmp_path / "subsonic_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    create_schema(conn)
    return conn, db_path


def _seed_playlist(conn: sqlite3.Connection, *, name: str = "Test Playlist", full_path: str = r"E:\ms\t\Set\Artist - Track.mp3") -> tuple[str, str]:
    metadata_id = generate_metadata_id()
    playlist_id = generate_metadata_id()
    conn.execute(
        "INSERT INTO tracks (metadata_id, title, artist, album, file_path, filename, full_path) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (metadata_id, "Track", "Artist", "Album", str(Path(full_path).parent) + "\\", Path(full_path).name, full_path),
    )
    conn.execute(
        "INSERT INTO playlists (playlist_id, name, strategy, track_count) VALUES (?, ?, ?, ?)",
        (playlist_id, name, "manual", 1),
    )
    conn.execute(
        "INSERT INTO playlist_tracks (playlist_id, metadata_id, position) VALUES (?, ?, ?)",
        (playlist_id, metadata_id, 1),
    )
    conn.commit()
    return playlist_id, metadata_id


class FakeClient:
    def __init__(self, index: SongIndex | None = None) -> None:
        self.index = index or SongIndex([])
        self.deleted: list[str] = []
        self.created: list[tuple[str, list[str]]] = []
        self.playlists: list[dict] = []
        self.entries: list[dict] = []
        self.settings = type("Settings", (), {"library_root": r"E:\\ms\\t", "media_folder_name": "EDM"})()

    def build_song_index(self) -> SongIndex:
        return self.index

    def delete_playlist(self, playlist_id: str) -> None:
        self.deleted.append(playlist_id)

    def create_playlist(self, name: str, song_ids: list[str]) -> str:
        self.created.append((name, song_ids))
        return "subsonic-new-id"

    def list_playlists(self) -> list[dict]:
        return self.playlists

    def get_playlist_entries(self, playlist_id: str) -> list[dict]:
        return self.entries


class TestPathMapping:
    def test_builds_media_folder_candidates(self) -> None:
        candidates = local_path_candidates(r"E:\ms\t\Mixes\DJ\track.mp3", r"E:\ms\t", "EDM")
        assert "edm/mixes/dj/track.mp3" in candidates
        assert "mixes/dj/track.mp3" in candidates

    def test_builds_media_folder_candidates_from_relative_db_path(self) -> None:
        candidates = local_path_candidates(r"t\Mixes\DJ\track.mp3", r"E:\ms", "Electronic")
        assert "electronic/t/mixes/dj/track.mp3" in candidates
        assert "t/mixes/dj/track.mp3" in candidates

    def test_resolves_track_by_path_first(self) -> None:
        row = {"full_path": r"E:\ms\t\Mixes\DJ\track.mp3", "title": "Track", "artist": "Artist"}
        index = SongIndex([
            IndexedSong(
                song_id="song-1",
                title="Track",
                artist="Artist",
                album="Album",
                path="edm/mixes/dj/track.mp3",
                title_key="track",
                artist_key="artist",
            )
        ])
        assert resolve_track_to_song_id(row, index, type("Settings", (), {"library_root": r"E:\ms\t", "media_folder_name": "EDM"})()) == "song-1"

    def test_resolves_relative_db_path_by_path_first(self) -> None:
        row = {"full_path": r"t\Mixes\DJ\track.mp3", "title": "Track", "artist": "Artist"}
        index = SongIndex([
            IndexedSong(
                song_id="song-1",
                title="Track",
                artist="Artist",
                album="Album",
                path="electronic/t/mixes/dj/track.mp3",
                title_key="track",
                artist_key="artist",
            )
        ])
        settings = type("Settings", (), {"library_root": r"E:\ms", "media_folder_name": "Electronic"})()
        assert resolve_track_to_song_id(row, index, settings) == "song-1"


class TestSyncAndDelete:
    def test_sync_persists_mapping(self, db: tuple[sqlite3.Connection, Path]) -> None:
        conn, _db_path = db
        playlist_id, _metadata_id = _seed_playlist(conn)
        fake_client = FakeClient(
            SongIndex([
                IndexedSong(
                    song_id="song-1",
                    title="Track",
                    artist="Artist",
                    album="Album",
                    path="edm/set/artist - track.mp3",
                    title_key="track",
                    artist_key="artist",
                )
            ])
        )

        result = sync_playlist_to_subsonic(conn, playlist_id, client=fake_client)
        assert result.subsonic_playlist_id == "subsonic-new-id"
        mapping = conn.execute(
            "SELECT subsonic_playlist_id, subsonic_playlist_name FROM subsonic_playlist_sync WHERE playlist_id = ?",
            (playlist_id,),
        ).fetchone()
        assert mapping["subsonic_playlist_id"] == "subsonic-new-id"
        assert fake_client.created[0][1] == ["song-1"]

    def test_delete_removes_remote_and_local_mapping(self, db: tuple[sqlite3.Connection, Path]) -> None:
        conn, _db_path = db
        playlist_id, _metadata_id = _seed_playlist(conn)
        conn.execute(
            "INSERT INTO subsonic_playlist_sync (playlist_id, subsonic_playlist_id, subsonic_playlist_name) VALUES (?, ?, ?)",
            (playlist_id, "remote-1", "Test Playlist"),
        )
        conn.commit()
        fake_client = FakeClient()

        deleted_id = delete_playlist_with_remote(conn, playlist_id, client=fake_client)
        assert deleted_id == playlist_id
        assert fake_client.deleted == ["remote-1"]
        assert conn.execute("SELECT 1 FROM playlists WHERE playlist_id = ?", (playlist_id,)).fetchone() is None
        assert conn.execute("SELECT 1 FROM subsonic_playlist_sync WHERE playlist_id = ?", (playlist_id,)).fetchone() is None


class TestCliCommands:
    def test_playlist_delete_command_invokes_shared_delete(self, db: tuple[sqlite3.Connection, Path], monkeypatch: pytest.MonkeyPatch) -> None:
        conn, db_path = db
        playlist_id, _metadata_id = _seed_playlist(conn)
        conn.close()
        calls: list[str] = []

        def fake_delete(conn: sqlite3.Connection, playlist_identifier: str, *, settings=None, client=None) -> str:
            calls.append(playlist_identifier)
            return playlist_identifier

        monkeypatch.setattr("musesleuth.subsonic.delete_playlist_with_remote", fake_delete)
        runner = CliRunner()
        result = runner.invoke(cli, ["playlist", "delete", "--db", str(db_path), "--id", playlist_id])
        assert result.exit_code == 0, result.output
        assert calls == [playlist_id]

    def test_playlist_sync_command_outputs_summary(self, db: tuple[sqlite3.Connection, Path], monkeypatch: pytest.MonkeyPatch) -> None:
        conn, db_path = db
        playlist_id, _metadata_id = _seed_playlist(conn)
        conn.close()

        def fake_sync(conn: sqlite3.Connection, playlist_identifier: str, *, target_name=None, settings=None, client=None) -> SyncResult:
            return SyncResult(
                playlist_id=playlist_identifier,
                playlist_name="Test Playlist",
                subsonic_playlist_id="remote-99",
                matched_count=1,
                missed_count=1,
                missed_tracks=["Artist - Missed"],
            )

        monkeypatch.setattr("musesleuth.subsonic.sync_playlist_to_subsonic", fake_sync)
        runner = CliRunner()
        result = runner.invoke(cli, ["playlist", "sync-subsonic", "--db", str(db_path), "--id", playlist_id])
        assert result.exit_code == 0, result.output
        assert "remote-99" in result.output
        assert "Artist - Missed" in result.output


class TestWebDelete:
    def test_web_delete_uses_shared_helper(self, db: tuple[sqlite3.Connection, Path], monkeypatch: pytest.MonkeyPatch) -> None:
        conn, db_path = db
        playlist_id, _metadata_id = _seed_playlist(conn)
        conn.close()
        called: list[str] = []

        def fake_delete(conn: sqlite3.Connection, playlist_identifier: str, *, settings=None, client=None) -> str:
            called.append(playlist_identifier)
            return playlist_identifier

        monkeypatch.setattr("musesleuth.subsonic.delete_playlist_with_remote", fake_delete)
        app = create_app(db_path)
        with TestClient(app) as client:
            response = client.delete(f"/api/playlists/{playlist_id}")
        assert response.status_code == 200
        assert response.json() == {"ok": True}
        assert called == [playlist_id]
