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

## CLI Commands

```bash
# Import tracks from mp3tag CSV
musicmeta import tracks.csv --db music.db

# Run the full enrichment pipeline
musicmeta run --db music.db

# Run a single stage
musicmeta run --db music.db --stage probe

# Check pipeline status
musicmeta status --db music.db

# Rich TUI dashboard
musicmeta dashboard --db music.db

# Retry failed jobs
musicmeta retry --db music.db
musicmeta retry --db music.db --stage enrich --max-attempts 3

# Write enriched tags back to audio files
musicmeta writeback --db music.db --fields bpm,genre,key

# Export enriched data
musicmeta export --db music.db --format json --output enriched.json
musicmeta export --db music.db --format csv --output enriched.csv
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

13 tables in SQLite with WAL mode and foreign key constraints:

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
