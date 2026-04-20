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

## DJ Playlist Pipeline

### Current behavior

- MuseSleuth can generate DJ-oriented playlists with `playlist generate --strategy dj_flow`.
- DJ flow ordering uses transition scoring plus greedy ordering and 2-opt refinement.
- Mix plans can be emitted with `playlist mix-plan` and include cue points, transition type, and transition cost.
- Playlist metrics can be emitted with `playlist evaluate` and optionally compared against another playlist with `--compare-id`.
- The web API exposes DJ flow strategy selection and a `POST /playlists/{playlist_id}/mix-plan` endpoint.

### Commands

```bash
musesleuth playlist generate --db E:\ms\music_new.db --strategy dj_flow --name "Warmup Set" --limit 25
musesleuth playlist mix-plan --db E:\ms\music_new.db --id <playlist-id>
musesleuth playlist evaluate --db E:\ms\music_new.db --id <playlist-id>
musesleuth playlist evaluate --db E:\ms\music_new.db --id <playlist-a-id> --compare-id <playlist-b-id>
```

### Metrics and outputs

- `playlist mix-plan` emits JSON entries with `cue_in_s`, `cue_out_s`, `transition_type`, `transition_cost`, `bpm`, and `camelot_key`.
- `playlist evaluate` emits JSON metrics including `total_transition_cost`, `mean_transition_cost`, `max_transition_cost`, `bpm_range`, `key_compatibility_pct`, and `energy_smoothness`.

### Implementation touchpoints

- `src/musesleuth/filters.py`
- `src/musesleuth/similarity.py`
- `src/musesleuth/transition.py`
- `src/musesleuth/dj_optimizer.py`
- `src/musesleuth/mix_plan.py`
- `src/musesleuth/evaluation.py`
- `src/musesleuth/playlist_generator.py`
- `src/musesleuth/cli.py`
- `src/musesleuth/web/routes/playlists.py`
- `tests/test_filters.py`
- `tests/test_similarity.py`
- `tests/test_transition.py`
- `tests/test_dj_optimizer.py`
- `tests/test_mix_plan.py`
- `tests/test_cli_dj.py`
- `tests/test_evaluation.py`

## Prefer Official Tracks

### Current behavior

- By default, playlist generation (every strategy in
  `src/musesleuth/playlist_generator.py`) drops 8-bit / karaoke / tribute /
  kids-bop style covers and, when multiple versions of a song are
  clustered into the same `remix_group` or `duplicate_group`, keeps the
  official release instead of the cover.
- Filter runs inside `_dedup_candidates` *before* the first-wins dedup, so
  it benefits every existing strategy with no per-strategy SQL changes.
- Two signals drive the filter:
  - **Hard artist blocklist** in
    `src/musesleuth/prefer_official.py::HARD_ARTIST_BLOCKLIST`. Exact
    lowercase match on `tracks.artist`. Always dropped. Edit the set to
    extend it.
  - **Soft regex** over artist + title (`SOFT_PATTERNS`). Matches
    `8[- ]?bit`, `karaoke`, `instrumental version`, `in the style of`,
    `lullaby(...)?`, `tribute to`, `made famous by`. Deprioritized within
    each remix/duplicate group and dropped entirely when the track's
    `track_stats.listener_count` (Last.fm) is below `popularity_floor`.

### Params / flags

- `include_covers: bool = False` — bypasses both filters for intentional
  cover playlists.
- `popularity_floor: int = 1000` — threshold for the soft-match drop.

### Commands

```bash
musesleuth playlist generate --db E:\ms\music_new.db --strategy genre \
    --name "Nu-Metal" --param genre=nu-metal
musesleuth playlist generate --db E:\ms\music_new.db --strategy genre \
    --name "Nu-Metal (Covers OK)" --param genre=nu-metal --include-covers
musesleuth playlist generate --db E:\ms\music_new.db --strategy genre \
    --name "Nu-Metal strict" --param genre=nu-metal --popularity-floor 5000
```

Web API: `POST /playlists/generate` accepts `include_covers: bool` and
`popularity_floor: int` alongside existing strategy params.

### Implementation touchpoints

- `src/musesleuth/prefer_official.py`
- `src/musesleuth/playlist_generator.py` (`_dedup_candidates`)
- `src/musesleuth/cli.py` (`playlist generate`)
- `src/musesleuth/web/routes/playlists.py`
- `tests/test_prefer_official.py`

## Metadata Sidecars

MuseSleuth can export every per-track DB row to two independent JSON sidecars
beside each audio file, and re-import them later to reconstruct a database.

### Current behavior

- Two sidecar files are written per audio track:
  - `<audio>.msmeta.json` — full dump of every per-track DB row (all feature,
    enrichment, embedding, structure, lyric, writeback-log, and outgoing
    similarity-edge tables).
  - `<audio>.dlpmeta` — compact identity/hash record shared with the live
    pipeline (`src/musesleuth/sidecar.py`).
- Both files embed an HMAC-SHA256 `signature` block. `sha256` is always
  populated (tamper detection); `hmac` is populated only when the signing
  key is configured.
- When no signing key is configured, files are written with a `.bak` suffix
  (`*.msmeta.json.bak`, `*.dlpmeta.bak`) so unsigned best-effort exports are
  never mistaken for trusted snapshots.
- On import, signatures are verified; corrupt payloads (SHA-256 mismatch) are
  refused, and unsigned / key-mismatched payloads are imported but the live
  sidecar file on disk is renamed to `.bak`.
- A `backup-db` subcommand creates an exact SQLite backup via the built-in
  SQLite backup API.
- `scripts/metadata_sidecar.py` maintains a `TABLE_DISPOSITION` map covering
  every `CREATE TABLE` in the schema; a unit test guards against silently
  dropping newly-added tables from exports.

### Required environment variables

None. The feature works unsigned.

### Optional environment variables

```bash
MUSESLEUTH_SIDECAR_KEY   # shared secret used for HMAC-SHA256 signing
```

### Commands

```bash
python scripts/metadata_sidecar.py backup-db --db E:\ms\music_new.db
python scripts/metadata_sidecar.py export-sidecars --db E:\ms\music_new.db
python scripts/metadata_sidecar.py export-sidecars --db E:\ms\music_new.db --workers 8
python scripts/metadata_sidecar.py export-sidecars --db Y:\music.db --workers 8 --path-prefix-map "I:\Music=Y:"
python scripts/metadata_sidecar.py import-sidecars --db E:\ms\music_new.db E:\ms\t
python scripts/metadata_sidecar.py import-sidecars --db E:\ms\music_new.db E:\ms\t --create-missing
```

### Sidecar schema / signing

- `.msmeta.json` payload `schema_version` = 2. v1 (unsigned) is readable on
  import.
- `.dlpmeta` payload `v` = 2. v1 (unsigned) is readable on import.
- Canonical bytes for signing: sorted-key JSON with `signature`,
  `exported_at`, and `updated_at` excluded so timestamps don't invalidate
  signatures on idempotent round-trips.
- `similarity_edges` is exported only for outgoing edges (`src_id =
  metadata_id`); on import, rows whose `dst_id` is not known to the target DB
  are silently dropped.

### Implementation touchpoints

- `scripts/metadata_sidecar.py`
- `src/musesleuth/sidecar.py`
- `src/musesleuth/db/schema.py`
- `tests/test_metadata_sidecar.py`
