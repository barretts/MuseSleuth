from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from musesleuth.db import create_schema, get_connection
from musesleuth.subsonic import (
    SubsonicSettings,
    get_cached_or_live_song_index,
    local_path_candidates,
    resolve_playlist,
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
    parser = argparse.ArgumentParser(description="Audit playlist track path resolution against the cached Subsonic song index.")
    parser.add_argument("--db", required=True)
    parser.add_argument("--playlist", required=True)
    parser.add_argument("--env-file", default=str(ROOT / ".env"))
    parser.add_argument("--base-url")
    parser.add_argument("--username")
    parser.add_argument("--password")
    parser.add_argument("--media-folder")
    parser.add_argument("--library-root")
    return parser


def resolve_local_fs_path(full_path: str, library_root: str) -> Path:
    value = str(full_path or "").strip()
    if not value:
        return Path(library_root)
    path = Path(value)
    if path.drive:
        return path
    return Path(library_root) / path


def main() -> int:
    args = build_parser().parse_args()
    env_values = load_env_file(Path(args.env_file))
    settings = SubsonicSettings(
        base_url=env_or_arg(args, env_values, "base_url", "MUSESLEUTH_SUBSONIC_BASE_URL"),
        username=env_or_arg(args, env_values, "username", "MUSESLEUTH_SUBSONIC_USERNAME"),
        password=env_or_arg(args, env_values, "password", "MUSESLEUTH_SUBSONIC_PASSWORD"),
        media_folder_name=env_or_arg(args, env_values, "media_folder", "MUSESLEUTH_SUBSONIC_MEDIA_FOLDER", "Electronic"),
        library_root=env_or_arg(args, env_values, "library_root", "MUSESLEUTH_SUBSONIC_LIBRARY_ROOT", r"E:\ms"),
    )

    conn = get_connection(Path(args.db))
    create_schema(conn)
    playlist = resolve_playlist(conn, args.playlist)
    index = get_cached_or_live_song_index(conn, settings=settings)
    print(f"AUDIT PLAYLIST | {playlist['name']} | playlist_id={playlist['playlist_id']} | cache_songs={len(index.songs)}")

    rows = conn.execute(
        """
        SELECT t.metadata_id, t.title, t.artist, t.album, t.full_path
        FROM playlist_tracks pt
        JOIN tracks t ON t.metadata_id = pt.metadata_id
        WHERE pt.playlist_id = ?
        ORDER BY pt.position ASC
        """,
        (playlist["playlist_id"],),
    ).fetchall()

    misses = 0
    for row in rows:
        song_id = resolve_track_to_song_id(row, index, settings)
        if song_id:
            continue
        misses += 1
        full_path = str(row["full_path"] or "")
        local_fs_path = resolve_local_fs_path(full_path, settings.library_root)
        exists = local_fs_path.exists()
        candidates = local_path_candidates(full_path, settings.library_root, settings.media_folder_name)
        path_hits = [candidate for candidate in candidates if candidate in index.by_path]
        print(f"MISS | artist={row['artist']} | title={row['title']}")
        print(f"  DB_PATH | {full_path}")
        print(f"  LOCAL_PATH | {local_fs_path}")
        print(f"  LOCAL_EXISTS | {exists}")
        print(f"  CANDIDATES | {' | '.join(candidates[:8])}")
        if path_hits:
            print(f"  CACHE_PATH_HITS | {' | '.join(path_hits)}")
        else:
            print("  CACHE_PATH_HITS | none")

    print(f"AUDIT DONE | misses={misses} | total_tracks={len(rows)}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
