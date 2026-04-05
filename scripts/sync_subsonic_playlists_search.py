from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from musesleuth.db import create_schema, get_connection
from musesleuth.subsonic import (
    SubsonicClient,
    SubsonicSettings,
    artist_key_variants,
    candidate_title_key,
    canonicalize_subsonic_path,
    get_cached_or_live_song_index,
    local_path_candidates,
    make_key,
    normalize_artist,
    resolve_track_to_song_id,
)


def load_env_file(env_path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not env_path.exists():
        return values
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def env_or_arg(args: argparse.Namespace, env_values: dict[str, str], arg_name: str, env_name: str, default: str | None = None) -> str:
    current = getattr(args, arg_name)
    if current:
        return str(current)
    env_value = os.environ.get(env_name) or env_values.get(env_name)
    if env_value:
        return str(env_value)
    if default is not None:
        return default
    raise ValueError(f"Missing required setting: {env_name}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sync MuseSleuth playlists to Subsonic using search-based resolution.")
    parser.add_argument("--db", required=True)
    parser.add_argument("--env-file", default=str(ROOT / ".env"))
    parser.add_argument("--base-url")
    parser.add_argument("--username")
    parser.add_argument("--password")
    parser.add_argument("--media-folder")
    parser.add_argument("--library-root")
    parser.add_argument("--playlist", action="append", default=[])
    parser.add_argument("--limit-missed", type=int, default=10)
    return parser


def ratio(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio() * 100.0


def resolve_playlist_rows(conn: sqlite3.Connection, selected: list[str]) -> list[sqlite3.Row]:
    if not selected:
        return conn.execute("SELECT playlist_id, name FROM playlists ORDER BY created_at DESC").fetchall()
    rows: list[sqlite3.Row] = []
    for item in selected:
        row = conn.execute(
            "SELECT playlist_id, name FROM playlists WHERE playlist_id = ?",
            (item,),
        ).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT playlist_id, name FROM playlists WHERE name = ? ORDER BY created_at DESC LIMIT 1",
                (item,),
            ).fetchone()
        if row is None:
            raise ValueError(f"Playlist not found: {item}")
        rows.append(row)
    return rows


def search_songs(client: SubsonicClient, query: str, song_count: int = 25) -> list[dict]:
    data = client.api(
        "search3.view",
        query=query,
        artistCount=0,
        albumCount=0,
        songCount=song_count,
    )
    songs = data.get("searchResult3", {}).get("song", [])
    if isinstance(songs, dict):
        songs = [songs]
    return songs


def choose_song_id(track: sqlite3.Row, client: SubsonicClient, index, library_root: str, media_folder: str) -> str | None:
    path_song_id = resolve_track_to_song_id(track, index, client.settings)
    if path_song_id:
        return path_song_id

    expected_paths = local_path_candidates(str(track["full_path"] or ""), library_root, media_folder)
    title = str(track["title"] or "")
    artist = str(track["artist"] or "")
    title_key = candidate_title_key(title)
    artist_variants = artist_key_variants(artist)

    queries: list[str] = []
    filename = Path(str(track["full_path"] or "")).name
    stem = Path(filename).stem
    if stem:
        queries.append(stem)
    if artist and title:
        queries.append(f"{artist} {title}")
        queries.append(f"{artist} - {title}")
    if title:
        queries.append(title)

    seen_queries: set[str] = set()
    best_song_id: str | None = None
    best_score = 0.0

    for query in queries:
        query = query.strip()
        if not query or query in seen_queries:
            continue
        seen_queries.add(query)
        songs = search_songs(client, query)
        for song in songs:
            song_id = str(song.get("id", "") or "").strip()
            if not song_id:
                continue
            song_path = canonicalize_subsonic_path(str(song.get("path", "") or ""))
            if song_path and song_path in expected_paths:
                return song_id

            candidate_title = candidate_title_key(str(song.get("title", "") or ""))
            candidate_artist = make_key(normalize_artist(str(song.get("artist", "") or "")))
            title_score = ratio(title_key, candidate_title)
            artist_score = max((ratio(variant, candidate_artist) for variant in artist_variants), default=0.0)

            if title_score < 70 or artist_score < 70:
                continue
            if title_score < 90 and artist_score < 85:
                continue

            combined = (title_score * 0.6) + (artist_score * 0.4)
            if combined > best_score:
                best_score = combined
                best_song_id = song_id

    return best_song_id


def delete_playlist_by_name(client: SubsonicClient, name: str) -> None:
    playlists = client.list_playlists()
    for playlist in playlists:
        if playlist.get("name") == name and playlist.get("id"):
            client.delete_playlist(str(playlist["id"]))
            return


def sync_playlist(conn: sqlite3.Connection, client: SubsonicClient, index, playlist_row: sqlite3.Row, limit_missed: int) -> tuple[str, int, int, list[str], str | None]:
    playlist_id = str(playlist_row["playlist_id"])
    playlist_name = str(playlist_row["name"])
    tracks = conn.execute(
        """
        SELECT t.metadata_id, t.title, t.artist, t.album, t.full_path
        FROM playlist_tracks pt
        JOIN tracks t ON t.metadata_id = pt.metadata_id
        WHERE pt.playlist_id = ?
        ORDER BY pt.position ASC
        """,
        (playlist_id,),
    ).fetchall()
    print(f"SYNC START | {playlist_name} | playlist_id={playlist_id} | tracks={len(tracks)}")

    matched_ids: list[str] = []
    missed: list[str] = []
    seen_ids: set[str] = set()

    for track_index, track in enumerate(tracks, start=1):
        if track_index == 1 or track_index % 25 == 0 or track_index == len(tracks):
            print(f"TRACK PROGRESS | {playlist_name} | {track_index}/{len(tracks)} | matched={len(matched_ids)} | missed={len(missed)}")
        song_id = choose_song_id(track, client, index, client.settings.library_root, client.settings.media_folder_name)
        if song_id and song_id not in seen_ids:
            matched_ids.append(song_id)
            seen_ids.add(song_id)
        elif not song_id:
            expected_paths = local_path_candidates(str(track["full_path"] or ""), client.settings.library_root, client.settings.media_folder_name)
            print(f"MISS DETAIL | {playlist_name} | artist={track['artist']} | title={track['title']} | full_path={track['full_path']}")
            print(f"MISS CANDIDATES | {playlist_name} | {' | '.join(expected_paths[:6])}")
            missed.append(f"{track['artist']} - {track['title']}")

    if len(matched_ids) < 1:
        print(f"SYNC EMPTY | {playlist_name} | no tracks resolved")
        return playlist_name, len(matched_ids), len(missed), missed[:limit_missed], None

    existing = conn.execute(
        "SELECT subsonic_playlist_id FROM subsonic_playlist_sync WHERE playlist_id = ?",
        (playlist_id,),
    ).fetchone()
    if existing and existing["subsonic_playlist_id"]:
        print(f"REMOTE DELETE | {playlist_name} | mapped_remote={existing['subsonic_playlist_id']}")
        client.delete_playlist(str(existing["subsonic_playlist_id"]))
    else:
        print(f"REMOTE LOOKUP | {playlist_name} | checking same-name remote playlist")
        delete_playlist_by_name(client, playlist_name)

    print(f"REMOTE CREATE | {playlist_name} | songs={len(matched_ids)}")
    remote_id = client.create_playlist(playlist_name, matched_ids)
    conn.execute(
        """
        INSERT INTO subsonic_playlist_sync
            (playlist_id, subsonic_playlist_id, subsonic_playlist_name, last_synced_at)
        VALUES (?, ?, ?, datetime('now'))
        ON CONFLICT(playlist_id) DO UPDATE SET
            subsonic_playlist_id = excluded.subsonic_playlist_id,
            subsonic_playlist_name = excluded.subsonic_playlist_name,
            last_synced_at = excluded.last_synced_at
        """,
        (playlist_id, remote_id, playlist_name),
    )
    conn.commit()
    print(f"SYNC DONE | {playlist_name} | remote={remote_id} | matched={len(matched_ids)} | missed={len(missed)}")
    return playlist_name, len(matched_ids), len(missed), missed[:limit_missed], remote_id


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    env_values = load_env_file(Path(args.env_file))

    settings = SubsonicSettings(
        base_url=env_or_arg(args, env_values, "base_url", "MUSESLEUTH_SUBSONIC_BASE_URL"),
        username=env_or_arg(args, env_values, "username", "MUSESLEUTH_SUBSONIC_USERNAME"),
        password=env_or_arg(args, env_values, "password", "MUSESLEUTH_SUBSONIC_PASSWORD"),
        media_folder_name=env_or_arg(args, env_values, "media_folder", "MUSESLEUTH_SUBSONIC_MEDIA_FOLDER", "Electronic"),
        library_root=env_or_arg(args, env_values, "library_root", "MUSESLEUTH_SUBSONIC_LIBRARY_ROOT", r"E:\ms"),
    )
    client = SubsonicClient(settings)
    conn = get_connection(Path(args.db))
    create_schema(conn)
    print("INDEX LOAD | loading cached or live Subsonic song index")
    index = get_cached_or_live_song_index(conn, client=client)
    print(f"INDEX READY | songs={len(index.songs)}")
    playlists = resolve_playlist_rows(conn, args.playlist)
    print(f"Syncing {len(playlists)} playlists...")

    successes = 0
    failures = 0
    for playlist_row in playlists:
        try:
            name, matched, missed_count, missed_preview, remote_id = sync_playlist(conn, client, index, playlist_row, args.limit_missed)
            if remote_id is None:
                failures += 1
                print(f"ERR | {name} | no songs resolved | missed={missed_count}")
            else:
                successes += 1
                print(f"OK | {name} | remote={remote_id} | matched={matched} | missed={missed_count}")
            for item in missed_preview:
                print(f"  MISS | {item}")
            if missed_count > len(missed_preview):
                print(f"  MISS | ... and {missed_count - len(missed_preview)} more")
        except Exception as exc:
            failures += 1
            print(f"FAIL TRACE | playlist={playlist_row['name']} | type={type(exc).__name__}")
            print(f"ERR | {playlist_row['name']} | {exc}")

    conn.close()
    print(f"DONE success={successes} failed={failures}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
