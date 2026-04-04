# Opus Migration Runbook

This runbook converts an existing library to Opus in a mirror tree while
keeping MuseSleuth paths synchronized through targeted relocate scans.

Defaults:
- If `--target-root` is omitted, conversion writes into `--source-root`.
- If manifest path/folder is omitted, manifests are written under `--source-root`.
- If `--source-root` is omitted, all DB tracks are eligible.
- If both roots are omitted, output is in-place (`track.ext` -> `track.opus` in same folder).

## 1) Backup + baseline inventory

```powershell
musesleuth opus backup-baseline `
  --db "C:\code\MuseSleuth\music_new.db" `
  --backup-dir "D:\migration\baseline"
```

Outputs:
- SQLite backup copy in `--backup-dir`
- CSV inventory (`track-inventory-<db_stem>.csv`) with current DB paths

## 2) Pilot conversion

```powershell
musesleuth opus pilot `
  --db "C:\code\MuseSleuth\music_new.db" `
  --source-root "D:\Music\Library" `
  --target-root "H:\music_opus_shadow" `
  --manifest "D:\migration\manifests\pilot.csv" `
  --limit 1000 `
  --bitrate 160 `
  --scaling `
  --workers 16 `
  --relocate-workers 8
```

Use `--dry-run` first if you want to verify planned paths without encoding.

## 3) Full batch rollout

```powershell
musesleuth opus batch-rollout `
  --db "C:\code\MuseSleuth\music_new.db" `
  --source-root "D:\Music\Library" `
  --target-root "H:\music_opus_shadow" `
  --manifest-dir "D:\migration\manifests\batches" `
  --batch-size 2000 `
  --bitrate 160 `
  --scaling `
  --workers 24 `
  --relocate-workers 8
```

DB-only (no source root scope):

```powershell
musesleuth opus batch-rollout `
  --db "C:\code\MuseSleuth\music_new.db" `
  --batch-size 2000 `
  --bitrate 160 `
  --scaling `
  --workers 24
```

Notes:
- `--workers` is ffmpeg parallelism. For a 1950X, start around 20-28 and tune.
- Each batch writes a CSV manifest and runs targeted DB relocate sync.
- Scaling mode maps lossy source bitrate to Opus equivalent (about 75%) with a hard cap from `--bitrate` (160 by default).

## 4) Final reconcile

After cutover/root switch is complete, run:

```powershell
musesleuth opus final-reconcile `
  --db "C:\code\MuseSleuth\music_new.db" `
  --target-root "H:\music_opus_shadow" `
  --workers 12
```

This runs a final relocate scan and prints total track count vs `.opus` track count.

## Rollback window

Keep the original library untouched for at least 2-4 weeks.
If needed, restore from DB backup + point scanner/paths to original root.
