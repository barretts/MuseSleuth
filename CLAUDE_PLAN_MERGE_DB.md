# Plan: Database Merge Utility

## Context

Two MuseSleuth SQLite databases need to be merged into one. The user has confirmed no `full_path` collisions exist between them, meaning every track file path is unique across both databases. This simplifies the merge to a matter of combining tables and reassigning ULIDs to avoid conflicts.

## Assumptions

- Both DBs share the same schema (same tables, same columns)
- No track in db_a has the same `full_path` as any track in db_b
- ULIDs from both DBs may overlap — they are reassigned on merge to avoid FK conflicts
- db_a is the destination; db_b is merged into it (destination-first)
- Playlists with the same name: rename incoming with numeric suffix ("DJ Set" → "DJ Set (2)"). Order doesn't matter.

## Approach

### Core Strategy: Attach-and-Copy with ULID Remapping

1. **Attach db_b to db_a** using SQLite `ATTACH DATABASE`
2. **Build a ULID mapping table**: for each track in db_b, generate a new ULID and map old → new
3. **Copy tracks** from db_b into db_a using the new ULIDs (preserving `full_path`)
4. **Copy all related feature tables** (technical_features, musical_features, etc.) using the remapped ULIDs
5. **Handle playlists** separately: if same name exists, rename incoming with suffix
6. **Skip scraper_cache and jobs** — these are ephemeral/pipeline state, not needed in merged DB

### Critical Files

- `src/musesleuth/db.py` — Schema reference, `generate_metadata_id()` for ULID generation
- `src/musesleuth/db/connection.py` — Connection handling, PRAGMA settings

### Tables to Merge (ordered by FK dependency)

| Table | Key Remapping | Notes |
|-------|---------------|-------|
| `tracks` | metadata_id → new ULID | Core. Copy all columns. `full_path` is unique, no collision. |
| `track_sidecars` | metadata_id → new ULID | FK to tracks.metadata_id |
| `technical_features` | metadata_id → new ULID | FK to tracks.metadata_id |
| `musical_features` | metadata_id → new ULID | FK to tracks.metadata_id |
| `ml_features` | metadata_id → new ULID | FK to tracks.metadata_id |
| `loudness_features` | metadata_id → new ULID | FK to tracks.metadata_id |
| `timbre_features` | metadata_id → new ULID | FK to tracks.metadata_id |
| `tag_snapshot_raw` | metadata_id → new ULID | FK to tracks.metadata_id |
| `external_ids` | metadata_id → new ULID | FK to tracks.metadata_id |
| `artist_stats` | metadata_id → new ULID | FK to tracks.metadata_id |
| `track_stats` | metadata_id → new ULID | FK to tracks.metadata_id |
| `genres_tags` | metadata_id → new ULID | FK to tracks.metadata_id |
| `playlist_signals` | metadata_id → new ULID | FK to tracks.metadata_id |
| `embeddings` | metadata_id → new ULID | FK to tracks.metadata_id |
| `structure_segments` | metadata_id → new ULID | FK to tracks.metadata_id |
| `version_groups` | metadata_id → new ULID | FK to tracks.metadata_id |
| `similarity_edges` | src_id, dst_id → new ULIDs | Bidirectional FK to tracks.metadata_id |
| `beat_grids` | metadata_id → new ULID | FK to tracks.metadata_id |
| `track_search_tokens` | metadata_id → new ULID | FK to tracks.metadata_id |
| `tag_writeback_log` | metadata_id → new ULID | FK to tracks.metadata_id |
| `track_lyrics` | metadata_id → new ULID | FK to tracks.metadata_id |
| `playlists` | playlist_id → new ULID | No FK to tracks. Merge by name, numeric suffix on collision. |
| `playlist_tracks` | playlist_id, metadata_id → remapped | Dual FK: playlist_id + metadata_id |
| `subsonic_playlist_sync` | playlist_id → new ULID | FK to playlists.playlist_id |
| `subsonic_song_cache` | (none) | Standalone cache. Copy as-is. |
| `jobs` | (skip) | Pipeline state. Don't copy. |
| `scraper_cache` | (skip) | API cache. Don't copy. |

### Implementation Steps

1. Add `merge` command to `cli.py` with:
   - `--db-destination` (required): Destination DB (db_a)
   - `--db-source` (required): Source DB to merge in (db_b)
   - `--dry-run` flag: Preview counts without writing

2. Create merge logic in `src/musesleuth/db/merge.py`:
   - `merge_databases(dst_db: Path, src_db: Path, dry_run: bool) -> dict`
   - Attach src as `source`
   - Build ULID mapping via `CREATE TEMP TABLE ulid_map(old_id, new_id) AS SELECT ...`
   - Execute INSERT INTO ... SELECT ... FROM source.tablename JOIN ulid_map
   - Use `INSERT OR IGNORE` for tables with UNIQUE constraints to skip any edge-case collisions
   - Handle playlist name collision: rename "DJ Set" → "DJ Set (2)"

3. Verify with:
   - Dry run shows expected row counts per table
   - Full merge produces valid DB: all FK constraints pass, job counts zeroed, track counts add up

### Reusable Utilities Found

- `generate_metadata_id()` in `db/connection.py` — generates fresh ULIDs for new tracks
- `connection.py` already handles `PRAGMA foreign_keys=ON` and WAL mode — merge should use same pattern
- No existing merge code to reuse — this is net new

### Edge Cases

- **Playlist name collision**: If playlist name exists in both DBs, rename the incoming one with a numeric suffix ("DJ Set" → "DJ Set (2)"). Order doesn't matter.
- **Autoincrement IDs**: Tables like `artist_stats`, `track_stats` use `INTEGER PRIMARY KEY AUTOINCREMENT` — SQLite remaps these automatically when copying; just copy values directly
- **UNIQUE constraints**: Use `INSERT OR IGNORE` as a safety net for any latent collisions on non-full_path columns

### DB File Locations

The user indicated Y: and Z: drives but these were not accessible from this environment. The merge tool will accept any valid file paths for `--db-destination` and `--db-source`.

### Verification

1. Run `--dry-run` first: confirms table counts before committing
2. After merge: `musesleuth status --db merged.db` shows expected track count (db_a + db_b)
3. `SELECT COUNT(*) FROM tracks` should equal sum of both DBs' track counts
4. FK integrity check: `PRAGMA foreign_key_check` returns empty
