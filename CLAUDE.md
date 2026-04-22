# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

### Environment Setup
```bash
# Create virtual environment and install package
python -m venv .venv
# Windows
.venv\Scripts\activate
# Unix/macOS
source .venv/bin/activate
pip install -e .
# Install dev dependencies
pip install -e .[dev]
```

### Running the Application
```bash
# Scan directory for audio files (creates database)
musesleuth scan "PATH_TO_MUSIC" --db "PATH/TO/music.db"

# Preview scan without writing
musesleuth scan "PATH_TO_MUSIC" --db "PATH/TO/music.db" --dry-run

# Run full enrichment pipeline
musesleuth run --db "PATH/TO/music.db"

# Run specific pipeline stage
musesleuth run --db "PATH/TO/music.db" --stage probe
musesleuth run --db "PATH/TO/music.db" --stage enrich
musesleuth run --db "PATH/TO/music.db" --stage analyze
# etc. for: import, probe, analyze, fingerprint_match, enrich, derive_signals, writeback

# Start web backend (FastAPI)
musesleuth web --db "PATH/TO/music.db" --host 0.0.0.0 --port 8484

# Start frontend dev server
cd frontend
npm install  # first time only
npm run dev  # Vite at http://localhost:5184

# Build frontend for production
cd frontend
npm run build  # outputs to frontend/dist/
```

### Pipeline Management
```bash
# Check pipeline status
musesleuth status --db "PATH/TO/music.db"

# Rich TUI dashboard
musesleuth dashboard --db "PATH/TO/music.db"

# Retry failed jobs
musesleuth retry --db "PATH/TO/music.db"
musesleuth retry --db "PATH/TO/music.db" --stage enrich --max-attempts 3

# Relocate after moving files
musesleuth relocate "PATH_TO_MUSIC" --db "PATH/TO/music.db"
musesleuth relocate "PATH_TO_MUSIC" --db "PATH/TO/music.db" --dry-run
musesleuth relocate "PATH_TO_MUSIC" --db "PATH/TO/music.db" --verify-hash

# Write enriched tags back to audio files
musesleuth writeback --db "PATH/TO/music.db" --fields bpm,genre,key

# Export data
musesleuth export --db "PATH/TO/music.db" --format json --output enriched.json
musesleuth export --db "PATH/TO/music.db" --format csv --output enriched.csv
```

### Subsonic Integration
```bash
# Set required environment variables (Windows)
set MUSESLEUTH_SUBSONIC_BASE_URL=http://localhost:4040/rest
set MUSESLEUTH_SUBSONIC_USERNAME=your-user
set MUSESLEUTH_SUBSONIC_PASSWORD=your-password

# Optional overrides
set MUSESLEUTH_SUBSONIC_MEDIA_FOLDER=EDM
set MUSESLEUTH_SUBSONIC_LIBRARY_ROOT=E:\ms\t
set MUSESLEUTH_SUBSONIC_CLIENT_NAME=musesleuth

# Sync playlists
musesleuth playlist sync-subsonic --db "PATH/TO/music.db" --id <playlist-id>
musesleuth playlist audit-subsonic --db "PATH/TO/music.db" --id <playlist-id>
musesleuth playlist delete --db "PATH/TO/music.db" --id <playlist-id>
```

### DJ Playlist Workflow
```bash
# Full DJ workflow
musesleuth run --db "PATH/TO/music.db"
musesleuth playlist generate --db "PATH/TO/music.db" --strategy dj_flow --name "DJ Set" --limit 25
musesleuth playlist mix-plan --db "PATH/TO/music.db" --id <playlist-id>
musesleuth playlist evaluate --db "PATH/TO/music.db" --id <playlist-id>
```

### Testing
```bash
# Run all tests
pytest tests/ -v

# Run by test type
pytest tests/ -m unit          # Pure logic tests
pytest tests/ -m integration   # Tests with real SQLite DB
pytest tests/ -m cli           # CLI entry point tests
pytest tests/ -m adapter       # Adapter tests against fixtures
pytest tests/ -m e2e           # Full pipeline end-to-end tests
pytest tests/ -m slow          # Tests that take significant time

# Run DJ-specific test suite
python -m pytest tests/test_filters.py tests/test_similarity.py tests/test_transition.py tests/test_dj_optimizer.py tests/test_mix_plan.py tests/test_cli_dj.py tests/test_evaluation.py tests/test_version_groups.py -v

# Run broader audio/DJ feature suite
python -m pytest tests/test_timbre.py tests/test_embeddings.py tests/test_structure.py tests/test_beatgrid.py tests/test_version_groups.py tests/test_filters.py tests/test_similarity.py tests/test_transition.py tests/test_dj_optimizer.py tests/test_mix_plan.py tests/test_cli_dj.py tests/test_evaluation.py tests/test_dedup.py -v
```

### PowerShell Drive Mapping
```powershell
# Always use 'idm' to access mapped drives Z: and Y:
# After establishing network connection with 'net use Z:' or 'net use Y:'
idm Z:    # Maps and makes Z: drive accessible at /z/ in bash
idm Y:    # Maps and makes Y: drive accessible at /y/ in bash

# To make 'idm' available globally in PowerShell:
# 1. Copy Invoke-DriveMap.ps1 to a permanent location
# 2. Add this line to your PowerShell profile (notepad $PROFILE):
#    . "C:\path\to\Invoke-DriveMap.ps1"
# 3. Restart PowerShell
# 4. Use: idm Z: or idm Y:

# After mapping, access drives in bash/windows subsystem at:
#   /z/  or  /mnt/z/  for Z: drive
#   /y/  or  /mnt/y/  for Y: drive
```

## Code Architecture

### Pipeline Stages (Resumeable Job Queue)
The system uses a SQLite-backed job queue (`jobs` table) where each track progresses through stages:
1. **import** - CSV parsing, deduplication, ULID assignment, `.dlpmeta` sidecar creation
2. **probe** - ffprobe technical metadata, BLAKE3 hashing, embedded tag reading via mutagen
3. **analyze** - BPM estimation (aubio/librosa cross-check), musical key detection, Chromaprint fingerprinting
4. **fingerprint_match** - AcoustID lookup with candidate ranking and fuzzy metadata fallback
5. **enrich** - Last.fm and MusicBrainz API enrichment with response caching and rate limiting
6. **derive_signals** - Camelot wheel key mapping, decade/BPM/energy/popularity bucketing
7. **writeback** - Write BPM/genre/key back to MP3 ID3 tags with full change logging

Each stage is implemented as a runner (`*_runner.py`) that processes jobs from the queue, with individual stage logic in corresponding modules.

### Core Modules
- **cli.py** - Click-based command interface exposing all functionality
- **db.py** - SQLite schema management, connection handling, ULID generation
- **job_queue.py** - Resumable per-track per-stage job queue with claim/release/fail/retry/expire mechanics
- **pipeline.py** - Stage sequencing orchestrator
- **sidecar.py** - Atomic `.dlpmeta` sidecar file handling for track identity preservation
- **hasher.py** - BLAKE3 partial/full hashing implementation
- **tag_reader.py** - Embedded tag reading via mutagen
- **probe.py** - ffprobe JSON parser for technical metadata
- **bpm_analyzer.py** - BPM/key analysis using aubio + librosa with octave error handling
- **fingerprint.py** - Chromaprint fpcalc wrapper for acoustic fingerprinting
- **matcher.py** - AcoustID matching with fuzzy fallback logic
- **enrich_runner.py** - Orchestrates Last.fm and MusicBrainz API calls
- **adapters/** - API adapter implementations:
  - **base.py** - Abstract adapter interface
  - **rate_limiter.py** - Per-adapter rate limiting
  - **cache.py** - SQLite-backed response cache
  - **lastfm.py** - Last.fm API adapter
  - **musicbrainz.py** - MusicBrainz API adapter
  - **spotify.py** - Spotify API adapter (additional)
- **signals.py** - Playlist signal derivation + Camelot wheel calculations
- **tag_writer.py** - ID3 tag writeback with comprehensive audit logging
- **dedup.py** - Duplicate detection (BLAKE3 hash + fuzzy title/artist/duration matching)
- **dashboard.py** - Rich TUI dashboard tables with color-coded pipeline status
- **subsonic.py** - Subsonic API client, path mapping, playlist sync helpers
- **web/** - FastAPI backend serving REST API and frontend SPA:
  - **routes/** - API endpoint handlers
  - **templates/** - HTML templates
  - **static/** - CSS and static assets
- **frontend/** - React/Vite SPA for web interface

### Database Schema
SQLite database with WAL mode and foreign key constraints containing 14 tables:
- `tracks` - Core track metadata from CSV import
- `track_sidecars` - `.dlpmeta` sidecar file tracking, hashes, fingerprints
- `technical_features` - Codec, bitrate, sample rate, duration, raw ffprobe JSON
- `musical_features` - BPM (aubio/librosa/final), key, mode, energy
- `tag_snapshot_raw` - Raw embedded tag JSON snapshots
- `external_ids` - AcoustID, MusicBrainz, ISRC identifiers with confidence
- `artist_stats` - Listener counts, play counts, similar artists, genres
- `track_stats` - Popularity, listener count, play count per source
- `genres_tags` - Genre/tag values from multiple sources
- `playlist_signals` - Derived buckets: decade, BPM, Camelot key, energy, popularity, duplicate group
- `subsonic_playlist_sync` - Local playlist to Subsonic playlist mapping and sync timestamps
- `scraper_cache` - Cached API responses keyed by adapter+key
- `jobs` - Resumable per-track per-stage job queue
- `tag_writeback_log` - Audit trail of every tag field written back

### Key Features
- **Resumability**: Safe concurrent and interrupted runs via job queue state tracking
- **Sidecar System**: `.dlpmeta` files preserve track identity across filesystem moves
- **Caching**: API response caching to minimize external calls and respect rate limits
- **Logging**: Comprehensive audit trails for all tag writebacks and pipeline operations
- **Extensibility**: Modular adapter system for adding new metadata sources