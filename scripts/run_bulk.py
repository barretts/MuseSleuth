"""Run probe and analyze stages in sequence with progress reporting."""
import sys
import time

from musesleuth.db import get_connection
from musesleuth.pipeline import PipelineOrchestrator
from musesleuth.bpm_analyzer import clear_audio_cache

conn = get_connection("music.db")
orch = PipelineOrchestrator(conn, worker_id="bulk")

stages = sys.argv[1:] if len(sys.argv) > 1 else ["probe", "analyze"]

for stage in stages:
    print(f"\n=== {stage} ===")
    start = time.time()
    count = 0
    errors = 0
    while True:
        try:
            if not orch.process_next(stage):
                break
        except Exception as exc:
            errors += 1
            if errors <= 3:
                print(f"  Error: {exc}", file=sys.stderr)
        count += 1
        if stage == "analyze":
            clear_audio_cache()
        if count % 500 == 0:
            elapsed = time.time() - start
            rate = count / elapsed if elapsed > 0 else 0
            print(f"  {stage}: {count} done, {errors} errors, {rate:.1f}/s")
    elapsed = time.time() - start
    rate = count / elapsed if elapsed > 0 else 0
    print(f"  {stage} complete: {count} processed, {errors} errors, {elapsed:.0f}s ({rate:.1f}/s)")

conn.close()
