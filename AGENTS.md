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
