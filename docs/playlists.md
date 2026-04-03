# Playlist Guide

MuseSleuth generates playlists from your enriched music library using six strategies, each targeting a different use case.

## Quick Start

```bash
# Generate a single playlist via CLI
musesleuth playlist generate --db music_new.db \
  --strategy genre --param genre=progressive_trance \
  --name "Prog Trance Session" --limit 50

# Or use the web UI
musesleuth web --db music_new.db
# Navigate to /playlists → "Generate New Playlist" form
```

## Strategies

### `genre` — Group by ML-classified genre

Selects tracks matching a genre and orders by classification confidence (strongest matches first).

```bash
musesleuth playlist generate --db music_new.db \
  --strategy genre --param genre=deep_house \
  --name "Deep House Selections" --limit 40
```

Available genres depend on your library. Common ones from the EDM classifier:

`progressive_trance` · `uplifting_trance` · `psy_trance` · `tech_trance` ·
`deep_house` · `progressive_house` · `tech_house` · `future_house` · `house` ·
`electro_house` · `minimal_techno` · `dub_techno` · `downtempo` ·
`drumnbass` · `liquid` · `jungle` · `dubstep` · `trap` · `bass` ·
`breaks` · `garage` · `hardcore` · `dance` · `hip-hop` · `reggae`

### `bpm_range` — Tempo window

Selects tracks within a BPM range, sorted ascending. Great for building sets with smooth tempo transitions.

```bash
musesleuth playlist generate --db music_new.db \
  --strategy bpm_range --param bpm_min=128 --param bpm_max=140 \
  --name "128-140 BPM Zone" --limit 50
```

### `year_range` — Custom year window

Selects tracks released within an exact year range. More flexible than decade — perfect for "my high school years" or "the era of X and Y."

```bash
# Chronological order (default)
musesleuth playlist generate --db music_new.db \
  --strategy year_range --param year_min=1998 --param year_max=2002 \
  --name "Turn of the Millennium" --limit 50

# Top tracks by popularity (Last.fm listeners)
musesleuth playlist generate --db music_new.db \
  --strategy year_range --param year_min=1998 --param year_max=2002 \
  --param sort_by=popularity \
  --name "Top Tracks 1998-2002" --limit 50
```

`sort_by` options: `year` (default — chronological then BPM), `popularity` (most-listened first via Last.fm).

### `camelot_chain` — Harmonic mixing

Starts from a seed track and builds a chain of Camelot-compatible transitions. Each track is harmonically mixable with the next (same key, ±1 on the wheel, or relative major/minor).

```bash
musesleuth playlist generate --db music_new.db \
  --strategy camelot_chain --param seed_id=YOUR_TRACK_ID \
  --name "Harmonic Flow" --limit 30
```

Find track IDs on the web UI track detail page or via `musesleuth export`.

### `energy_arc` — Build up and cool down

Sorts tracks by energy into an arc: ascending energy to a peak, then descending. Mimics a DJ set structure.

```bash
musesleuth playlist generate --db music_new.db \
  --strategy energy_arc --param peak_position=0.6 \
  --name "Energy Arc Set" --limit 50
```

`peak_position` is 0.0–1.0 (default 0.6 = peak at 60% through the playlist).

### `decade` — Era-based

Selects tracks from a decade bucket, sorted by year then BPM.

```bash
musesleuth playlist generate --db music_new.db \
  --strategy decade --param decade=2000s \
  --name "2000s Throwback" --limit 50
```

### `mood` — Mood tag matching

Selects tracks whose ML mood tags match a keyword, sorted by danceability and energy.

```bash
musesleuth playlist generate --db music_new.db \
  --strategy mood --param mood=energetic \
  --name "High Energy" --limit 50
```

Common mood tags: `energetic` · `calm` · `relaxed` · `dark` · `bright` · `uplifting` · `danceable` · `melancholic` · `dynamic` · `monotoned`

## Curated Multi-Dimensional Playlists

For more advanced playlists that combine genre + mood + BPM + energy + decade + danceability + harmonic compatibility, use the curated script:

```bash
python scripts/generate_curated_playlists.py music_new.db
```

This creates 15 hand-crafted playlists like:

| Playlist | BPM | Energy | Sort |
|---|---|---|---|
| Late Night Psy Journey | 138–148 | — | harmonic chain |
| Peak Hour Bangers | 130–150 | high | energy arc |
| Downtempo Lounge | 80–120 | low | BPM smooth |
| Warehouse Minimal Session | 122–134 | low | harmonic chain |
| Progressive Sunrise Set | 128–142 | — | energy arc |
| Liquid DnB Smooth Ride | 160–180 | — | harmonic chain |
| Sunday Morning Recovery | 60–115 | very low | BPM smooth |
| 90s Eurodance Revival | 125–155 | — | BPM smooth |
| 2000s Trance Classics | 130–145 | — | harmonic chain |
| 2010s Electronic Anthems | 126–140 | — | harmonic chain |

See `scripts/generate_curated_playlists.py` for the full list and parameters.

## Custom Playlist Builder (Web UI)

The web UI at `/playlists` features a full playlist builder that lets you combine any filters:

- **Year range** — exact from/to years
- **BPM range** — tempo window
- **Energy range** — 0.0 to 1.0
- **Genres** — multi-select checkboxes (leave unchecked for any)
- **Moods** — include and/or exclude mood tags
- **Min danceability** — floor for danceability score
- **Min genre confidence** — only include strong ML classifications
- **Arrange by** — Most Popular, Harmonic Mix (Camelot), Energy Arc, BPM Smooth, Danceability, Chronological, or Shuffle

All filters are optional. Leave a field blank and it won't constrain the results. The old single-strategy generators are still available under "Quick generators."

## Sorting in the Web UI

Playlists are generated with a fixed track order, but the web UI provides a **Sort by** dropdown on every playlist detail page. Sort presets:

| Sort | Description |
|---|---|
| Original Order | The order from generation time |
| BPM (Low → High) | Ascending tempo |
| BPM (High → Low) | Descending tempo |
| Energy (Build Up) | Low to high energy |
| Energy (Cool Down) | High to low energy |
| Key (Camelot Wheel) | Numeric Camelot order (1A → 12B) |
| Group by Genre | Grouped by genre, then artist |
| Artist A–Z | Alphabetical by artist |

Sorting is view-only — it doesn't change the stored playlist.

## Exporting

Export any playlist as M3U8 for use in DJ software, media players, or other tools:

```bash
# CLI
musesleuth playlist export --db music_new.db \
  --id PLAYLIST_ID --output my_set.m3u8

# Web UI
# Click "Export M3U8" on any playlist detail page
```

## Refreshing After New Data

After re-running `enrich` or `derive_signals`, existing playlists are **not** automatically updated. To get fresh playlists with updated metadata:

1. Re-derive signals so buckets reflect new data:
   ```bash
   musesleuth run --db music_new.db --stage derive_signals --refresh
   ```

2. Regenerate playlists (CLI or web UI).

3. Or regenerate the full curated set:
   ```bash
   python scripts/generate_curated_playlists.py music_new.db
   ```

## Listing Playlists

```bash
# CLI
musesleuth playlist list --db music_new.db

# Web UI
# Navigate to /playlists
```
