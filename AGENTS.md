# AGENTS

## Subsonic Playlist Sync

MuseSleuth is the source of truth for playlists.

### Current behavior

- Local MuseSleuth playlists can be pushed to Subsonic with `playlist sync-subsonic`.
- Track resolution is path-first using `tracks.full_path`.
- Local files rooted at `E:\ms\t` are mapped to Subsonic media paths under the `EDM` media folder.
- If a path match fails, sync falls back to normalized title/artist fuzzy matching.
- Sync state is persisted in the `subsonic_playlist_sync` SQLite table.
- Deleting a playlist from either the CLI or the web API should also delete the mapped Subsonic playlist.

### Required environment variables

```bash
MUSESLEUTH_SUBSONIC_BASE_URL
MUSESLEUTH_SUBSONIC_USERNAME
MUSESLEUTH_SUBSONIC_PASSWORD
```

### Optional environment variables

```bash
MUSESLEUTH_SUBSONIC_MEDIA_FOLDER=EDM
MUSESLEUTH_SUBSONIC_LIBRARY_ROOT=E:\ms\t
MUSESLEUTH_SUBSONIC_CLIENT_NAME=musesleuth
```

### Commands

```bash
musesleuth playlist sync-subsonic --db E:\ms\music_new.db --id <playlist-id-or-name>
musesleuth playlist audit-subsonic --db E:\ms\music_new.db --id <playlist-id-or-name>
musesleuth playlist delete --db E:\ms\music_new.db --id <playlist-id-or-name>
```

### Sync semantics

- A sync creates or replaces the remote Subsonic playlist.
- If a local playlist was previously synced, the old remote playlist is deleted before the new one is created.
- If no stored mapping exists yet, a same-name remote playlist may be deleted before creating the replacement.
- The new remote playlist ID is persisted after a successful sync.

### Implementation touchpoints

- `src/musesleuth/subsonic.py`
- `src/musesleuth/cli.py`
- `src/musesleuth/web/routes/playlists.py`
- `src/musesleuth/db.py`
- `tests/test_subsonic.py`
