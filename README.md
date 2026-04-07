# MuseSleuth

A resumable music metadata enrichment pipeline that transforms a raw mp3tag CSV export into a fully enriched, playlist-ready database with BPM, key, Camelot notation, genre tags, popularity signals, and more.

## Architecture

```
CSV Import → Probe → Analyze → Fingerprint Match → Enrich → Derive Signals → Writeback
```

Each stage is driven by a **resumable job queue** backed by SQLite. Jobs can be claimed, released, failed, retried, and expired — enabling safe concurrent and interrupted runs.

## Features

- **CSV Import** — Parse semicolon-delimited mp3tag exports, deduplicate, assign ULIDs
- **Probe** — ffprobe technical metadata, BLAKE3 partial/full hashing, embedded tag reading via mutagen
- **Analyze** — BPM estimation with aubio+librosa cross-check (octave error handling), Chromaprint fingerprinting, musical key detection
- **Fingerprint Match** — AcoustID lookup with candidate ranking and fuzzy metadata fallback
- **Enrich** — Last.fm and MusicBrainz API adapters with response caching and rate limiting
- **Derive Signals** — Camelot wheel key mapping, decade/BPM/energy/popularity bucketing
- **Writeback** — Write BPM, genre, key back into MP3 ID3 tags with full change logging
- **Duplicate Detection** — BLAKE3 hash-based exact matching + fuzzy title/artist/duration matching
- **Rich TUI Dashboard** — Color-coded pipeline status and per-stage breakdown
- **Export** — CSV or JSON export of all enriched track data
- **Subsonic Playlist Sync** — Push MuseSleuth playlists to Subsonic with persistent local-to-remote mapping and delete propagation

## Installation

```bash
python -m venv .venv
.venv\Scripts\activate  # Windows
pip install -e .
```

### External Dependencies

| Tool | Purpose | Install |
|------|---------|---------|
| ffprobe | Technical metadata extraction | Part of [FFmpeg](https://ffmpeg.org/) |
| fpcalc | Chromaprint acoustic fingerprinting | Part of [Chromaprint](https://acoustid.org/chromaprint) |

## Running

### Quick Start (scan a music library)

```bash
# Scan a directory tree for audio files and create the database
musesleuth scan "I:\Music" --db "I:\Music\music.db"

# Preview what would be imported without writing anything
musesleuth scan "I:\Music" --db "I:\Music\music.db" --dry-run

# Increase parallel file I/O workers (default 4)
musesleuth scan "I:\Music" --db "I:\Music\music.db" --workers 8
```

### Backend (API server)

The backend is a FastAPI app served by uvicorn. It exposes a REST API at `/api/` and serves the built frontend SPA.

```bash
# Start the backend on the default host/port (127.0.0.1:8484)
musesleuth web --db "I:\Music\music.db"

# Bind to a specific host and port
musesleuth web --db "I:\Music\music.db" --host 0.0.0.0 --port 9000
```

Once running, visit `http://localhost:8484` in your browser.

### Frontend (React dev server)

For development the frontend runs its own Vite dev server with hot-reload, proxying API calls to the backend.

```bash
cd frontend
npm install        # first time only
npm run dev        # starts Vite at http://localhost:5184
```

For production, build the frontend and let the backend serve it:

```bash
cd frontend
npm run build      # outputs to frontend/dist/
```

Then start the backend normally -- it automatically serves `frontend/dist/` as the SPA.

### Enrichment Pipeline

```bash
# Run the full enrichment pipeline (probe -> analyze -> match -> enrich -> signals)
musesleuth run --db "I:\Music\music.db"

# Run a single stage
musesleuth run --db "I:\Music\music.db" --stage probe
musesleuth run --db "I:\Music\music.db" --stage enrich
```

### Relocate (after moving files)

If you reorganize your library on disk, re-scan or run relocate to update DB paths. `.dlpmeta` sidecar files let MuseSleuth re-associate tracks automatically.

```bash
musesleuth relocate "I:\Music" --db "I:\Music\music.db"

# Preview changes
musesleuth relocate "I:\Music" --db "I:\Music\music.db" --dry-run

# Reject files whose content changed (not just moved)
musesleuth relocate "I:\Music" --db "I:\Music\music.db" --verify-hash
```

### Inspect and Reset

```bash
# Check pipeline status in the terminal
musesleuth status --db "I:\Music\music.db"

# Rich TUI dashboard
musesleuth dashboard --db "I:\Music\music.db"

# Retry all failed jobs
musesleuth retry --db "I:\Music\music.db"

# Retry only a specific stage, with attempt cap
musesleuth retry --db "I:\Music\music.db" --stage enrich --max-attempts 3
```

To start completely fresh, delete the database and re-scan. Existing `.dlpmeta` sidecars on disk will re-link tracks:

```bash
del "I:\Music\music.db"
musesleuth scan "I:\Music" --db "I:\Music\music.db"
```

To also remove all sidecar identity files (full reset):

```powershell
Get-ChildItem -Path "I:\Music" -Recurse -Filter "*.dlpmeta" | Remove-Item
del "I:\Music\music.db"
musesleuth scan "I:\Music" --db "I:\Music\music.db"
```

## CLI Commands

```bash
# Import tracks from mp3tag CSV
musesleuth import tracks.csv --db music.db

# Run the full enrichment pipeline
musesleuth run --db music.db

# Run a single stage
musesleuth run --db music.db --stage probe

# Check pipeline status
musesleuth status --db music.db

# Rich TUI dashboard
musesleuth dashboard --db music.db

# Retry failed jobs
musesleuth retry --db music.db
musesleuth retry --db music.db --stage enrich --max-attempts 3

# Write enriched tags back to audio files
musesleuth writeback --db music.db --fields bpm,genre,key

# Export enriched data
musesleuth export --db music.db --format json --output enriched.json
musesleuth export --db music.db --format csv --output enriched.csv

# Sync a MuseSleuth playlist to Subsonic
musesleuth playlist sync-subsonic --db music.db --id <playlist-id>

# Audit the synced Subsonic playlist for duplicate remote entries
musesleuth playlist audit-subsonic --db music.db --id <playlist-id>

# Delete locally and remotely
musesleuth playlist delete --db music.db --id <playlist-id>
```

## Subsonic Playlist Sync

MuseSleuth can act as the source of truth for playlists and push them into Subsonic.

### How it works

- MuseSleuth reads the ordered tracks from a local playlist.
- Each track is matched to a Subsonic song using `tracks.full_path` first.
- Local paths under `E:\ms\t` are translated into Subsonic media paths under the `EDM` media folder.
- If a direct path match is not found, MuseSleuth falls back to normalized title/artist fuzzy matching.
- On every sync, MuseSleuth stores the remote Subsonic playlist ID in SQLite so later deletes can remove the remote playlist safely.
- Deleting a playlist through the CLI or web API also deletes the linked Subsonic playlist when a sync mapping exists.

### Required environment variables

```bash
set MUSESLEUTH_SUBSONIC_BASE_URL=http://localhost:4040/rest
set MUSESLEUTH_SUBSONIC_USERNAME=your-user
set MUSESLEUTH_SUBSONIC_PASSWORD=your-password
```

Optional overrides:

```bash
set MUSESLEUTH_SUBSONIC_MEDIA_FOLDER=EDM
set MUSESLEUTH_SUBSONIC_LIBRARY_ROOT=E:\ms\t
set MUSESLEUTH_SUBSONIC_CLIENT_NAME=musesleuth
```

### Playlist creation and replacement behavior

- `playlist sync-subsonic` syncs an existing MuseSleuth playlist by playlist ID or exact playlist name.
- If the playlist was previously synced, MuseSleuth deletes the old remote playlist before creating the replacement.
- If no stored mapping exists yet, MuseSleuth also checks for an existing remote playlist with the same name and removes it before creating the new one.
- The new remote playlist ID is persisted in the `subsonic_playlist_sync` table.
- You can override the remote name with `--subsonic-name`.

### Example

```bash
musesleuth playlist sync-subsonic --db E:\ms\music_new.db --id 01PLAYLISTID123
musesleuth playlist sync-subsonic --db E:\ms\music_new.db --id "Warmup Set" --subsonic-name "Warmup Set (Subsonic)"
musesleuth playlist audit-subsonic --db E:\ms\music_new.db --id "Warmup Set"
musesleuth playlist delete --db E:\ms\music_new.db --id "Warmup Set"
```

## Pipeline Stages

| # | Stage | Description |
|---|-------|-------------|
| 1 | `import` | CSV parsing, DB seeding, sidecar creation |
| 2 | `probe` | ffprobe, BLAKE3 hashing, embedded tag reading |
| 3 | `analyze` | BPM/key analysis (aubio+librosa), Chromaprint fingerprinting |
| 4 | `fingerprint_match` | AcoustID lookup, candidate ranking, fuzzy fallback |
| 5 | `enrich` | Last.fm + MusicBrainz API enrichment with caching |
| 6 | `derive_signals` | Camelot key, decade/BPM/energy/popularity buckets |
| 7 | `writeback` | Write BPM/genre/key back into audio file tags |

## Database Schema

14 tables in SQLite with WAL mode and foreign key constraints:

- `tracks` — Core track metadata from CSV import
- `track_sidecars` — `.dlpmeta` sidecar file tracking, hashes, fingerprints
- `technical_features` — Codec, bitrate, sample rate, duration, raw ffprobe JSON
- `musical_features` — BPM (aubio/librosa/final), key, mode, energy
- `tag_snapshot_raw` — Raw embedded tag JSON snapshots
- `external_ids` — AcoustID, MusicBrainz, ISRC identifiers with confidence
- `artist_stats` — Listener counts, play counts, similar artists, genres
- `track_stats` — Popularity, listener count, play count per source
- `genres_tags` — Genre/tag values from multiple sources
- `playlist_signals` — Derived buckets: decade, BPM, Camelot key, energy, popularity, duplicate group
 - `subsonic_playlist_sync` — Local playlist to Subsonic playlist mapping and sync timestamps
- `scraper_cache` — Cached API responses keyed by adapter+key
- `jobs` — Resumable per-track per-stage job queue
- `tag_writeback_log` — Audit trail of every tag field written back

## Testing

```bash
# Run all tests
pytest tests/ -v

# Run by marker
pytest tests/ -m unit
pytest tests/ -m integration
pytest tests/ -m cli
pytest tests/ -m adapter
pytest tests/ -m e2e
```

**230 tests** across 21 test files covering all modules with strict TDD.

## Project Structure

```
src/musesleuth/
├── __init__.py
├── cli.py              # Click CLI commands
├── csv_parser.py       # Semicolon-delimited CSV parsing
├── db.py               # SQLite schema, connection, ULID generation
├── sidecar.py          # Atomic .dlpmeta sidecar files
├── job_queue.py        # Resumable per-stage job queue
├── pipeline.py         # Stage sequencing orchestrator
├── probe.py            # ffprobe JSON parser
├── hasher.py           # BLAKE3 partial/full hashing
├── tag_reader.py       # Embedded tag reading (mutagen)
├── probe_runner.py     # Probe stage orchestration
├── bpm_analyzer.py     # BPM/key analysis (aubio+librosa)
├── fingerprint.py      # Chromaprint fpcalc wrapper
├── analyze_runner.py   # Analyze stage orchestration
├── matcher.py          # AcoustID matching + fuzzy fallback
├── enrich_runner.py    # Enrich stage orchestration
├── signals.py          # Playlist signal derivation + Camelot wheel
├── tag_writer.py       # ID3 tag writeback with logging
├── dedup.py            # Duplicate detection (hash + fuzzy)
├── dashboard.py        # Rich TUI dashboard tables
├── subsonic.py         # Subsonic API client, path mapping, playlist sync helpers
└── adapters/
    ├── __init__.py
    ├── base.py         # Abstract adapter interface
    ├── rate_limiter.py # Per-adapter rate limiting
    ├── cache.py        # SQLite-backed response cache
    ├── lastfm.py       # Last.fm API adapter
    └── musicbrainz.py  # MusicBrainz API adapter
```

## License

MIT
