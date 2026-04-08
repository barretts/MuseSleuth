from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import unicodedata
import urllib.parse
import urllib.request
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path


@dataclass(frozen=True)
class SubsonicSettings:
    base_url: str
    username: str
    password: str
    media_folder_name: str
    library_root: str
    client_name: str = "musesleuth"
    api_version: str = "1.16.1"
    response_format: str = "json"
    excluded_dirs: tuple[str, ...] = ()


@dataclass(frozen=True)
class IndexedSong:
    song_id: str
    title: str
    artist: str
    album: str
    path: str
    title_key: str
    artist_key: str


@dataclass(frozen=True)
class SyncResult:
    playlist_id: str
    playlist_name: str
    subsonic_playlist_id: str
    matched_count: int
    missed_count: int
    missed_tracks: list[str]


@dataclass(frozen=True)
class AuditResult:
    playlist_id: str
    subsonic_playlist_id: str
    track_count: int
    duplicate_id_count: int
    fuzzy_duplicate_count: int


class SongIndex:
    def __init__(self, songs: list[IndexedSong]) -> None:
        self.songs = songs
        self.by_path: dict[str, str] = {}
        self.by_title_key: dict[str, list[IndexedSong]] = {}
        for song in songs:
            if song.path:
                self.by_path[song.path] = song.song_id
                path_without_root = _path_without_media_folder(song.path)
                if path_without_root:
                    self.by_path.setdefault(path_without_root, song.song_id)
            if song.title_key:
                self.by_title_key.setdefault(song.title_key, []).append(song)


def song_index_from_rows(rows: list[sqlite3.Row]) -> SongIndex:
    songs = [
        IndexedSong(
            song_id=str(row["subsonic_song_id"]),
            title=str(row["title"] or ""),
            artist=str(row["artist"] or ""),
            album=str(row["album"] or ""),
            path=str(row["path"] or ""),
            title_key=str(row["title_key"] or ""),
            artist_key=str(row["artist_key"] or ""),
        )
        for row in rows
    ]
    return SongIndex(songs)


class SubsonicClient:
    def __init__(self, settings: SubsonicSettings) -> None:
        self.settings = settings

    @classmethod
    def from_settings(cls, settings: SubsonicSettings | None = None) -> "SubsonicClient":
        return cls(settings or load_subsonic_settings_from_env())

    def api(self, endpoint: str, **params):
        payload = {
            "u": self.settings.username,
            "p": self.settings.password,
            "v": self.settings.api_version,
            "c": self.settings.client_name,
            "f": self.settings.response_format,
            **params,
        }
        url = f"{self.settings.base_url.rstrip('/')}/{endpoint}?{urllib.parse.urlencode(payload)}"
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                with urllib.request.urlopen(url, timeout=90) as response:
                    data = json.load(response)["subsonic-response"]
                if data.get("status") != "ok":
                    raise RuntimeError(f"Subsonic API error for {endpoint}: {data}")
                return data
            except Exception as exc:
                last_error = exc
                print(f"API RETRY | endpoint={endpoint} | attempt={attempt}/3 | error={type(exc).__name__}: {exc}")
                if attempt < 3:
                    time.sleep(2 * attempt)
        assert last_error is not None
        raise last_error

    def get_music_folder_id(self, folder_name: str) -> str | None:
        data = self.api("getMusicFolders.view")
        folders = data.get("musicFolders", {}).get("musicFolder", [])
        if isinstance(folders, dict):
            folders = [folders]
        for folder in folders:
            if folder.get("name") == folder_name:
                return str(folder.get("id"))
        return None

    def build_song_index(self) -> SongIndex:
        songs: list[IndexedSong] = []
        seen_ids: set[str] = set()
        seen_dirs: set[str] = set()
        print("Building Subsonic song index...")
        self._collect_artist_album_songs(songs, seen_ids)
        print(f"Phase 1 complete: {len(seen_ids)} songs from artist/album walk")

        folder_id = self.get_music_folder_id(self.settings.media_folder_name)
        indexes_kwargs = {"musicFolderId": folder_id} if folder_id else {}
        data = self.api("getIndexes.view", **indexes_kwargs)
        indexes = data.get("indexes", {})
        seed_dirs: list[str] = []
        grouped_indexes = indexes.get("index", [])
        if isinstance(grouped_indexes, dict):
            grouped_indexes = [grouped_indexes]
        for index in grouped_indexes:
            artists = index.get("artist", [])
            if isinstance(artists, dict):
                artists = [artists]
            for artist in artists:
                if artist.get("id"):
                    seed_dirs.append(str(artist["id"]))
        top_children = indexes.get("child", [])
        if isinstance(top_children, dict):
            top_children = [top_children]
        for child in top_children:
            if child.get("isDir") and child.get("id"):
                seed_dirs.append(str(child["id"]))
        print(f"Phase 2 starting: {len(seed_dirs)} seed folders from getIndexes")
        for folder in seed_dirs:
            self._walk_directory(folder, songs, seen_ids, seen_dirs)
        print(f"Phase 2 complete: {len(seen_ids)} total songs after folder walk")
        return SongIndex(songs)

    def _collect_artist_album_songs(self, songs: list[IndexedSong], seen_ids: set[str]) -> None:
        data = self.api("getArtists.view")
        artists_root = data.get("artists", {})
        artist_groups = artists_root.get("index", [])
        if isinstance(artist_groups, dict):
            artist_groups = [artist_groups]
        artist_refs: list[dict] = []
        for group in artist_groups:
            artists = group.get("artist", [])
            if isinstance(artists, dict):
                artists = [artists]
            artist_refs.extend(artists)
        print(f"Found {len(artist_refs)} artists in Subsonic")

        for artist_index, artist_ref in enumerate(artist_refs, start=1):
            artist_id = artist_ref.get("id")
            if not artist_id:
                continue
            if artist_index == 1 or artist_index % 100 == 0:
                print(f"Artist walk progress: {artist_index}/{len(artist_refs)} artists, {len(seen_ids)} songs")
            artist_data = self.api("getArtist.view", id=str(artist_id))
            albums = artist_data.get("artist", {}).get("album", [])
            if isinstance(albums, dict):
                albums = [albums]
            for album_index, album in enumerate(albums, start=1):
                album_id = album.get("id")
                if not album_id:
                    continue
                if album_index == 1 or album_index % 100 == 0:
                    print(f"  Album walk progress: artist={artist_index}/{len(artist_refs)} album={album_index}/{len(albums)} songs={len(seen_ids)}")
                album_data = self.api("getAlbum.view", id=str(album_id))
                album_songs = album_data.get("album", {}).get("song", [])
                if isinstance(album_songs, dict):
                    album_songs = [album_songs]
                for song in album_songs:
                    self._append_song(song, songs, seen_ids)

    def _walk_directory(
        self,
        directory_id: str,
        songs: list[IndexedSong],
        seen_ids: set[str],
        seen_dirs: set[str],
    ) -> None:
        if directory_id in seen_dirs:
            return
        seen_dirs.add(directory_id)
        if len(seen_dirs) == 1 or len(seen_dirs) % 100 == 0:
            print(f"Folder walk progress: {len(seen_dirs)} directories, {len(seen_ids)} songs")
        try:
            data = self.api("getMusicDirectory.view", id=directory_id)
        except Exception as exc:
            print(f"FOLDER SKIP | id={directory_id} | error={type(exc).__name__}: {exc}")
            return
        children = data.get("directory", {}).get("child", [])
        if isinstance(children, dict):
            children = [children]
        for child in children:
            if child.get("isDir"):
                child_id = child.get("id")
                if child_id:
                    self._walk_directory(str(child_id), songs, seen_ids, seen_dirs)
                continue
            self._append_song(child, songs, seen_ids)

    def _append_song(self, payload: dict, songs: list[IndexedSong], seen_ids: set[str]) -> None:
        song_id = str(payload.get("id", "")).strip()
        if not song_id or song_id in seen_ids:
            return
        seen_ids.add(song_id)
        title = str(payload.get("title", "") or "")
        artist = str(payload.get("artist", "") or "")
        album = str(payload.get("album", "") or "")
        path = canonicalize_subsonic_path(str(payload.get("path", "") or ""))
        songs.append(
            IndexedSong(
                song_id=song_id,
                title=title,
                artist=artist,
                album=album,
                path=path,
                title_key=candidate_title_key(title),
                artist_key=make_key(normalize_artist(artist)),
            )
        )

    def list_playlists(self) -> list[dict]:
        data = self.api("getPlaylists.view")
        playlists = data.get("playlists", {}).get("playlist", [])
        if isinstance(playlists, dict):
            playlists = [playlists]
        return playlists

    def create_playlist(self, name: str, song_ids: list[str]) -> str:
        if not song_ids:
            raise ValueError("Cannot create a Subsonic playlist with no tracks")
        params = {
            "name": name,
            "songId": song_ids,
        }
        payload = {
            "u": self.settings.username,
            "p": self.settings.password,
            "v": self.settings.api_version,
            "c": self.settings.client_name,
            "f": self.settings.response_format,
        }
        query_parts: list[tuple[str, str]] = list(payload.items()) + [("name", name)]
        query_parts.extend(("songId", song_id) for song_id in song_ids)
        url = f"{self.settings.base_url.rstrip('/')}/createPlaylist.view?{urllib.parse.urlencode(query_parts)}"
        with urllib.request.urlopen(url, timeout=30) as response:
            data = json.load(response)["subsonic-response"]
        if data.get("status") != "ok":
            raise RuntimeError(f"Subsonic API error for createPlaylist.view: {data}")
        playlist = data.get("playlist", {})
        playlist_id = str(playlist.get("id", "")).strip()
        if not playlist_id:
            raise RuntimeError("Subsonic did not return a playlist id")
        return playlist_id

    def delete_playlist(self, playlist_id: str) -> None:
        self.api("deletePlaylist.view", id=playlist_id)

    def get_playlist_entries(self, playlist_id: str) -> list[dict]:
        data = self.api("getPlaylist.view", id=playlist_id)
        entries = data.get("playlist", {}).get("entry", [])
        if isinstance(entries, dict):
            entries = [entries]
        return entries


def load_subsonic_settings_from_env() -> SubsonicSettings:
    base_url = os.environ.get("MUSESLEUTH_SUBSONIC_BASE_URL", "").strip()
    username = os.environ.get("MUSESLEUTH_SUBSONIC_USERNAME", "").strip()
    password = os.environ.get("MUSESLEUTH_SUBSONIC_PASSWORD", "").strip()
    media_folder_name = os.environ.get("MUSESLEUTH_SUBSONIC_MEDIA_FOLDER", "").strip() or "EDM"
    library_root = os.environ.get("MUSESLEUTH_SUBSONIC_LIBRARY_ROOT", "").strip() or r"E:\ms\t"
    client_name = os.environ.get("MUSESLEUTH_SUBSONIC_CLIENT_NAME", "").strip() or "musesleuth"
    missing = [
        name
        for name, value in (
            ("MUSESLEUTH_SUBSONIC_BASE_URL", base_url),
            ("MUSESLEUTH_SUBSONIC_USERNAME", username),
            ("MUSESLEUTH_SUBSONIC_PASSWORD", password),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(f"Missing Subsonic configuration: {', '.join(missing)}")
    excluded_dirs_raw = os.environ.get("MUSESLEUTH_SUBSONIC_EXCLUDED_DIRS", "").strip()
    excluded_dirs: tuple[str, ...] = tuple(
        d.strip() for d in excluded_dirs_raw.split(",") if d.strip()
    )
    return SubsonicSettings(
        base_url=base_url,
        username=username,
        password=password,
        media_folder_name=media_folder_name,
        library_root=library_root,
        client_name=client_name,
        excluded_dirs=excluded_dirs,
    )


def resolve_playlist(conn: sqlite3.Connection, identifier: str):
    row = conn.execute(
        "SELECT playlist_id, name FROM playlists WHERE playlist_id = ?",
        (identifier,),
    ).fetchone()
    if row:
        return row
    row = conn.execute(
        "SELECT playlist_id, name FROM playlists WHERE name = ? ORDER BY created_at DESC LIMIT 1",
        (identifier,),
    ).fetchone()
    if row:
        return row
    raise ValueError(f"Playlist not found: {identifier}")


def save_song_index_cache(conn: sqlite3.Connection, index: SongIndex, settings: SubsonicSettings) -> int:
    conn.execute(
        "DELETE FROM subsonic_song_cache WHERE media_folder_name = ? AND library_root = ?",
        (settings.media_folder_name, settings.library_root),
    )
    conn.executemany(
        """
        INSERT OR REPLACE INTO subsonic_song_cache
            (subsonic_song_id, path, title, artist, album, title_key, artist_key, media_folder_name, library_root, cached_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        [
            (
                song.song_id,
                song.path,
                song.title,
                song.artist,
                song.album,
                song.title_key,
                song.artist_key,
                settings.media_folder_name,
                settings.library_root,
            )
            for song in index.songs
        ],
    )
    conn.commit()
    return len(index.songs)


def load_song_index_cache(conn: sqlite3.Connection, settings: SubsonicSettings) -> SongIndex:
    rows = conn.execute(
        """
        SELECT subsonic_song_id, path, title, artist, album, title_key, artist_key
        FROM subsonic_song_cache
        WHERE media_folder_name = ? AND library_root = ?
        ORDER BY path ASC
        """,
        (settings.media_folder_name, settings.library_root),
    ).fetchall()
    return song_index_from_rows(rows)


def refresh_subsonic_song_cache(
    conn: sqlite3.Connection,
    *,
    settings: SubsonicSettings | None = None,
    client: SubsonicClient | None = None,
) -> int:
    client = client or SubsonicClient.from_settings(settings)
    index = client.build_song_index()
    return save_song_index_cache(conn, index, client.settings)


def get_cached_or_live_song_index(
    conn: sqlite3.Connection,
    *,
    settings: SubsonicSettings | None = None,
    client: SubsonicClient | None = None,
) -> SongIndex:
    client = client or SubsonicClient.from_settings(settings)
    cached = load_song_index_cache(conn, client.settings)
    if cached.songs:
        return cached
    live = client.build_song_index()
    save_song_index_cache(conn, live, client.settings)
    return live


def sync_playlist_to_subsonic(
    conn: sqlite3.Connection,
    playlist_identifier: str,
    *,
    target_name: str | None = None,
    settings: SubsonicSettings | None = None,
    client: SubsonicClient | None = None,
) -> SyncResult:
    playlist = resolve_playlist(conn, playlist_identifier)
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
    client = client or SubsonicClient.from_settings(settings)
    index = get_cached_or_live_song_index(conn, client=client)
    matched_ids: list[str] = []
    missed_tracks: list[str] = []
    seen: set[str] = set()
    for row in rows:
        full_path = str(row["full_path"] or "")
        if is_path_excluded(full_path, client.settings.excluded_dirs):
            missed_tracks.append(f"{row['artist']} - {row['title']} [excluded]")
            continue
        song_id = resolve_track_to_song_id(row, index, client.settings)
        if song_id and song_id not in seen:
            matched_ids.append(song_id)
            seen.add(song_id)
        elif not song_id:
            missed_tracks.append(f"{row['artist']} - {row['title']}")
    if not matched_ids:
        raise RuntimeError("No playlist tracks could be resolved to Subsonic song IDs")
    playlist_name = target_name or str(playlist["name"] or playlist["playlist_id"])
    existing_mapping = conn.execute(
        "SELECT subsonic_playlist_id FROM subsonic_playlist_sync WHERE playlist_id = ?",
        (playlist["playlist_id"],),
    ).fetchone()
    if existing_mapping and existing_mapping["subsonic_playlist_id"]:
        client.delete_playlist(str(existing_mapping["subsonic_playlist_id"]))
    else:
        for remote in client.list_playlists():
            if remote.get("name") == playlist_name and remote.get("id"):
                client.delete_playlist(str(remote["id"]))
                break
    subsonic_playlist_id = client.create_playlist(playlist_name, matched_ids)
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
        (playlist["playlist_id"], subsonic_playlist_id, playlist_name),
    )
    conn.commit()
    return SyncResult(
        playlist_id=str(playlist["playlist_id"]),
        playlist_name=playlist_name,
        subsonic_playlist_id=subsonic_playlist_id,
        matched_count=len(matched_ids),
        missed_count=len(missed_tracks),
        missed_tracks=missed_tracks,
    )


def delete_playlist_with_remote(
    conn: sqlite3.Connection,
    playlist_identifier: str,
    *,
    settings: SubsonicSettings | None = None,
    client: SubsonicClient | None = None,
) -> str:
    playlist = resolve_playlist(conn, playlist_identifier)
    mapping = conn.execute(
        "SELECT subsonic_playlist_id FROM subsonic_playlist_sync WHERE playlist_id = ?",
        (playlist["playlist_id"],),
    ).fetchone()
    if mapping and mapping["subsonic_playlist_id"]:
        client = client or SubsonicClient.from_settings(settings)
        client.delete_playlist(str(mapping["subsonic_playlist_id"]))
    conn.execute("DELETE FROM playlist_tracks WHERE playlist_id = ?", (playlist["playlist_id"],))
    conn.execute("DELETE FROM subsonic_playlist_sync WHERE playlist_id = ?", (playlist["playlist_id"],))
    conn.execute("DELETE FROM playlists WHERE playlist_id = ?", (playlist["playlist_id"],))
    conn.commit()
    return str(playlist["playlist_id"])


def audit_synced_playlist(
    conn: sqlite3.Connection,
    playlist_identifier: str,
    *,
    settings: SubsonicSettings | None = None,
    client: SubsonicClient | None = None,
) -> AuditResult:
    playlist = resolve_playlist(conn, playlist_identifier)
    mapping = conn.execute(
        "SELECT subsonic_playlist_id FROM subsonic_playlist_sync WHERE playlist_id = ?",
        (playlist["playlist_id"],),
    ).fetchone()
    if not mapping or not mapping["subsonic_playlist_id"]:
        raise ValueError("Playlist is not synced to Subsonic")
    client = client or SubsonicClient.from_settings(settings)
    entries = client.get_playlist_entries(str(mapping["subsonic_playlist_id"]))
    duplicate_id_count = len(entries) - len({str(entry.get('id', '')) for entry in entries})
    title_keys: dict[str, str] = {}
    fuzzy_duplicate_count = 0
    for entry in entries:
        artist = str(entry.get("artist", "") or "")
        title = str(entry.get("title", "") or "")
        key = f"{make_key(normalize_artist(artist))}|{candidate_title_key(title)}"
        if key in title_keys:
            fuzzy_duplicate_count += 1
            continue
        for existing_key in title_keys:
            existing_artist, existing_title = existing_key.split("|", 1)
            if existing_artist != make_key(normalize_artist(artist)):
                continue
            if _ratio(existing_title, candidate_title_key(title)) >= 85:
                fuzzy_duplicate_count += 1
                break
        else:
            title_keys[key] = f"{artist} - {title}"
    return AuditResult(
        playlist_id=str(playlist["playlist_id"]),
        subsonic_playlist_id=str(mapping["subsonic_playlist_id"]),
        track_count=len(entries),
        duplicate_id_count=duplicate_id_count,
        fuzzy_duplicate_count=fuzzy_duplicate_count,
    )


def is_path_excluded(full_path: str, excluded_dirs: tuple[str, ...]) -> bool:
    """Return True if full_path starts with any of the excluded directory prefixes."""
    if not excluded_dirs or not full_path:
        return False
    normalized = _normalize_fs_path(full_path)
    for excl in excluded_dirs:
        norm_excl = _normalize_fs_path(excl)
        if norm_excl and (normalized == norm_excl or normalized.startswith(norm_excl.rstrip("/") + "/")):
            return True
    return False


def resolve_track_to_song_id(row: sqlite3.Row, index: SongIndex, settings: SubsonicSettings) -> str | None:
    full_path = str(row["full_path"] or "")
    for candidate in local_path_candidates(full_path, settings.library_root, settings.media_folder_name):
        match = index.by_path.get(candidate)
        if match:
            return match
    title_key = candidate_title_key(str(row["title"] or ""))
    artist_variants = artist_key_variants(str(row["artist"] or ""))
    candidates = index.by_title_key.get(title_key) or index.songs
    best_song_id: str | None = None
    best_score = 0.0
    for song in candidates:
        title_score = _ratio(title_key, song.title_key)
        artist_score = max(_ratio(variant, song.artist_key) for variant in artist_variants) if artist_variants else 0.0
        if title_score < 70 or artist_score < 70:
            continue
        score = (title_score * 0.6) + (artist_score * 0.4)
        if score > best_score:
            best_song_id = song.song_id
            best_score = score
    return best_song_id


def local_path_candidates(full_path: str, library_root: str, media_folder_name: str) -> list[str]:
    normalized_full = _normalize_fs_path(full_path)
    normalized_root = _normalize_fs_path(library_root)
    candidates: list[str] = []
    relative_candidate = _relative_media_path(normalized_full, normalized_root)
    if relative_candidate:
        candidates.append(canonicalize_subsonic_path(f"{media_folder_name}/{relative_candidate}"))
        candidates.append(canonicalize_subsonic_path(relative_candidate))
    if normalized_root and normalized_full.startswith(normalized_root.rstrip("/") + "/"):
        rel = normalized_full[len(normalized_root.rstrip("/")) + 1:]
        candidates.append(canonicalize_subsonic_path(f"{media_folder_name}/{rel}"))
        candidates.append(canonicalize_subsonic_path(rel))
    candidates.append(canonicalize_subsonic_path(normalized_full))
    seen: set[str] = set()
    result: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in seen:
            result.append(candidate)
            seen.add(candidate)
    return result


def canonicalize_subsonic_path(path: str) -> str:
    normalized = path.replace("\\", "/").strip().strip("/")
    normalized = re.sub(r"/+", "/", normalized)
    return normalized.casefold()


def _normalize_fs_path(path: str) -> str:
    raw = str(Path(path)) if path else ""
    return raw.replace("\\", "/").rstrip("/").casefold()


def _relative_media_path(normalized_full: str, normalized_root: str) -> str:
    if not normalized_full:
        return ""
    if normalized_root and normalized_full.startswith(normalized_root.rstrip("/") + "/"):
        return normalized_full[len(normalized_root.rstrip("/")) + 1:]
    drive_match = re.match(r"^[a-z]:/", normalized_full)
    if drive_match:
        return ""
    return normalized_full.lstrip("/")


def _path_without_media_folder(path: str) -> str:
    if "/" not in path:
        return path
    return path.split("/", 1)[1]


def normalize(text: str) -> str:
    value = (text or "").strip()
    for _ in range(3):
        previous = value
        value = _STRIP_SUFFIXES.sub("", value)
        value = _STRIP_PARENS.sub("", value)
        value = _FEAT_PATTERN.sub("", value)
        value = value.strip().rstrip("-–—").strip()
        if value == previous:
            break
    return value


def strip_all_parens(text: str) -> str:
    value = re.sub(r"\s*\([^)]*\)", "", text or "")
    value = re.sub(r"\s*\[[^\]]*\]", "", value)
    return value.strip()


def normalize_artist(text: str) -> str:
    value = (text or "").strip()
    for separator in (",", ";", " & ", " x ", " X "):
        if separator in value:
            value = value.split(separator)[0].strip()
    return value


def make_key(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")
    ascii_text = ascii_text.lower()
    ascii_text = re.sub(r"[^a-z0-9\s]", "", ascii_text)
    ascii_text = re.sub(r"\s+", " ", ascii_text).strip()
    return ascii_text


def candidate_title_key(text: str) -> str:
    return make_key(strip_all_parens(normalize(text)))


def artist_key_variants(artist: str) -> list[str]:
    base = make_key(normalize_artist(artist))
    variants = [base] if base else []
    if base.startswith("the "):
        variants.append(base[4:])
    elif base:
        variants.append(f"the {base}")
    return [variant for variant in dict.fromkeys(variants) if variant]


def _ratio(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio() * 100.0


_STRIP_SUFFIXES = re.compile(
    r"\s*[-–—]\s*("
    r"remaster(ed)?(\s+\d{4})?"
    r"|(\d{4}\s+)?remaster(ed)?"
    r"|original(\s+album)?\s+version"
    r"|deluxe(\s+edition)?"
    r"|bonus\s+track(\s+version)?"
    r"|single\s+version"
    r"|album\s+version"
    r"|radio\s+edit"
    r"|mono"
    r"|stereo"
    r"|live"
    r"|remix"
    r"|.+\s+remix"
    r"|.+\s+mix"
    r"|.+\s+version"
    r"|.+\s+edit"
    r"|explicit"
    r"|clean"
    r"|\d{4}"
    r")\s*$",
    re.IGNORECASE,
)

_STRIP_PARENS = re.compile(
    r"\s*\(("
    r"remaster(ed)?(\s+\d{4})?"
    r"|(\d{4}\s+)?remaster(ed)?"
    r"|original(\s+album)?\s+version"
    r"|deluxe(\s+edition)?"
    r"|bonus\s+track"
    r"|single\s+version"
    r"|album\s+version"
    r"|radio\s+edit"
    r"|from\s+.+"
    r"|feat\.?\s+.+"
    r"|ft\.?\s+.+"
    r"|with\s+.+"
    r"|mono"
    r"|stereo"
    r"|live.*"
    r"|remix.*"
    r"|explicit"
    r"|clean"
    r"|7\"\s*single\s*version"
    r"|\d+\s*remaster.*"
    r")\)\s*$",
    re.IGNORECASE,
)

_FEAT_PATTERN = re.compile(r"\s*(feat\.?|ft\.?|featuring)\s+.+$", re.IGNORECASE)
