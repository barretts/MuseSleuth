from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from musesleuth.subsonic import SubsonicClient, SubsonicSettings


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
    parser = argparse.ArgumentParser(description="Diagnose Subsonic API access for MuseSleuth playlist sync.")
    parser.add_argument("--env-file", default=str(ROOT / ".env"))
    parser.add_argument("--base-url")
    parser.add_argument("--username")
    parser.add_argument("--password")
    parser.add_argument("--media-folder")
    parser.add_argument("--library-root")
    parser.add_argument("--client-name", default="musesleuth")
    parser.add_argument("--query", default="test")
    return parser


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
        client_name=args.client_name,
    )
    client = SubsonicClient(settings)

    checks: list[tuple[str, dict]] = [
        ("getMusicFolders.view", {}),
        ("getIndexes.view", {}),
        ("getPlaylists.view", {}),
        ("search3.view", {"query": args.query, "artistCount": 0, "albumCount": 0, "songCount": 5}),
    ]

    music_folder_id: str | None = None
    for endpoint, params in checks:
        try:
            data = client.api(endpoint, **params)
            print(f"OK  {endpoint}")
            if endpoint == "getMusicFolders.view":
                folders = data.get("musicFolders", {}).get("musicFolder", [])
                if isinstance(folders, dict):
                    folders = [folders]
                for folder in folders:
                    print(f"  musicFolder id={folder.get('id')} name={folder.get('name')}")
                    if folder.get("name") == args.media_folder:
                        music_folder_id = str(folder.get("id"))
            elif endpoint == "getIndexes.view":
                indexes = data.get("indexes", {})
                child = indexes.get("child", [])
                index = indexes.get("index", [])
                if isinstance(child, dict):
                    child = [child]
                if isinstance(index, dict):
                    index = [index]
                print(f"  top-level child entries={len(child)} grouped indexes={len(index)}")
            elif endpoint == "getPlaylists.view":
                playlists = data.get("playlists", {}).get("playlist", [])
                if isinstance(playlists, dict):
                    playlists = [playlists]
                print(f"  playlists visible={len(playlists)}")
            elif endpoint == "search3.view":
                search = data.get("searchResult3", {})
                songs = search.get("song", [])
                if isinstance(songs, dict):
                    songs = [songs]
                print(f"  search songs={len(songs)}")
                for song in songs[:5]:
                    print(f"    song id={song.get('id')} title={song.get('title')} path={song.get('path')}")
        except Exception as exc:
            print(f"ERR {endpoint}: {exc}")

    if music_folder_id:
        try:
            data = client.api("getIndexes.view", musicFolderId=music_folder_id)
            indexes = data.get("indexes", {})
            child = indexes.get("child", [])
            index = indexes.get("index", [])
            if isinstance(child, dict):
                child = [child]
            if isinstance(index, dict):
                index = [index]
            print(f"OK  getIndexes.view musicFolderId={music_folder_id}")
            print(f"  top-level child entries={len(child)} grouped indexes={len(index)}")

            directory_id = None
            for item in child:
                if item.get("isDir") and item.get("id"):
                    directory_id = str(item.get("id"))
                    break
            if directory_id is None:
                for group in index:
                    artists = group.get("artist", [])
                    if isinstance(artists, dict):
                        artists = [artists]
                    for artist in artists:
                        if artist.get("id"):
                            directory_id = str(artist.get("id"))
                            break
                    if directory_id:
                        break
            if directory_id:
                try:
                    data = client.api("getMusicDirectory.view", id=directory_id)
                    children = data.get("directory", {}).get("child", [])
                    if isinstance(children, dict):
                        children = [children]
                    print(f"OK  getMusicDirectory.view id={directory_id}")
                    print(f"  child entries={len(children)}")
                except Exception as exc:
                    print(f"ERR getMusicDirectory.view id={directory_id}: {exc}")
        except Exception as exc:
            print(f"ERR getIndexes.view musicFolderId={music_folder_id}: {exc}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
