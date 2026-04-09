---
description: Sync a MuseSleuth playlist to the Subsonic server
---

## Prerequisites

The following environment variables must be set in the shell:

```powershell
$env:MUSESLEUTH_SUBSONIC_BASE_URL='http://music.sosuke.com:5221'
$env:MUSESLEUTH_SUBSONIC_USERNAME='barrett'
$env:MUSESLEUTH_SUBSONIC_PASSWORD='!37rQe8j#1t!htas'
$env:MUSESLEUTH_SUBSONIC_MEDIA_FOLDER='Electronic'
$env:MUSESLEUTH_SUBSONIC_LIBRARY_ROOT='E:\ms'
```

The `MEDIA_FOLDER` and `LIBRARY_ROOT` values **must** match the cached song index in the database (`Electronic` / `E:\ms`), otherwise the CLI will do a full Subsonic library walk which takes a long time.

## Sync a playlist

// turbo
1. Run the sync command with the playlist ID:

```powershell
$env:MUSESLEUTH_SUBSONIC_BASE_URL='http://music.sosuke.com:5221'; $env:MUSESLEUTH_SUBSONIC_USERNAME='barrett'; $env:MUSESLEUTH_SUBSONIC_PASSWORD='!37rQe8j#1t!htas'; $env:MUSESLEUTH_SUBSONIC_MEDIA_FOLDER='Electronic'; $env:MUSESLEUTH_SUBSONIC_LIBRARY_ROOT='E:\ms'; musesleuth playlist sync-subsonic --db Z:\music_new.db --id <PLAYLIST_ID>
```

Replace `<PLAYLIST_ID>` with the actual playlist ID (e.g. `01KNR0HF01W48HGYB9C43BCM2N`).

## Audit a synced playlist

// turbo
2. To verify the remote playlist matches:

```powershell
$env:MUSESLEUTH_SUBSONIC_BASE_URL='http://music.sosuke.com:5221'; $env:MUSESLEUTH_SUBSONIC_USERNAME='barrett'; $env:MUSESLEUTH_SUBSONIC_PASSWORD='!37rQe8j#1t!htas'; $env:MUSESLEUTH_SUBSONIC_MEDIA_FOLDER='Electronic'; $env:MUSESLEUTH_SUBSONIC_LIBRARY_ROOT='E:\ms'; musesleuth playlist audit-subsonic --db Z:\music_new.db --id <PLAYLIST_ID>
```

## Notes

- The `SubsonicClient.api()` method auto-appends `/rest/` to the base URL if not present.
- The song index is cached in the `subsonic_song_cache` SQLite table. Track resolution is path-first, then falls back to fuzzy title/artist matching.
- Syncing a playlist that was previously synced will delete the old remote playlist before creating the replacement.
