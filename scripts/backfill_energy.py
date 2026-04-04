"""Backfill energy/dynamics for already-analyzed tracks, then push incomplete tracks through remaining stages."""
import time
from musesleuth.db import get_connection
from musesleuth.bpm_analyzer import analyze_energy, clear_audio_cache
from musesleuth.pipeline import PipelineOrchestrator

conn = get_connection("music.db")

# 1. Find tracks with analyze done but energy=NULL
rows = conn.execute("""
    SELECT j.metadata_id, t.full_path
    FROM jobs j
    JOIN tracks t ON t.metadata_id = j.metadata_id
    JOIN musical_features mf ON mf.metadata_id = j.metadata_id
    WHERE j.stage = 'analyze' AND j.status = 'done' AND mf.energy IS NULL
""").fetchall()

print(f"=== Backfilling energy for {len(rows)} tracks ===")
for i, row in enumerate(rows):
    mid, path = row["metadata_id"], row["full_path"]
    try:
        er = analyze_energy(path)
        conn.execute("""
            UPDATE musical_features
            SET energy = ?, dynamic_range = ?, onset_density = ?, peak_rms = ?,
                analyzed_at = datetime('now')
            WHERE metadata_id = ?
        """, (er.energy, er.dynamic_range, er.onset_density, er.peak_rms, mid))
        conn.commit()
        clear_audio_cache()
        print(f"  [{i+1}/{len(rows)}] {mid}: energy={er.energy:.3f}")
    except Exception as exc:
        print(f"  [{i+1}/{len(rows)}] {mid}: ERROR {exc}")

# 2. Push tracks that have analyze done but later stages pending
pending = conn.execute("""
    SELECT DISTINCT j.metadata_id
    FROM jobs j
    WHERE j.stage IN ('fingerprint_match', 'enrich', 'derive_signals')
      AND j.status = 'pending'
      AND j.metadata_id IN (SELECT metadata_id FROM jobs WHERE stage = 'analyze' AND status = 'done')
""").fetchall()

if pending:
    mids = [r["metadata_id"] for r in pending]
    print(f"\n=== Pushing {len(mids)} tracks through remaining stages ===")
    orch = PipelineOrchestrator(conn, worker_id="backfill")
    for stage in ["fingerprint_match", "enrich", "derive_signals"]:
        count = 0
        while orch.process_next(stage):
            count += 1
        print(f"  {stage}: processed {count}")

# 3. Re-derive signals for backfilled tracks (energy was NULL before)
redone = conn.execute("""
    SELECT j.metadata_id FROM jobs j
    WHERE j.stage = 'derive_signals' AND j.status = 'done'
      AND j.metadata_id IN (
          SELECT metadata_id FROM musical_features WHERE energy IS NOT NULL
      )
""").fetchall()

if redone:
    from musesleuth.signals import run_derive_signals_for_track
    print(f"\n=== Re-deriving signals for {len(redone)} tracks ===")
    for r in redone:
        run_derive_signals_for_track(conn, r["metadata_id"])
    print("  Done.")

conn.close()
print("\nBackfill complete.")
