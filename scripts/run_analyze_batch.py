"""Run analyze stage on a small batch of probed tracks to validate."""
import sys
import time

from musesleuth.db import get_connection
from musesleuth.pipeline import PipelineOrchestrator

BATCH_SIZE = int(sys.argv[1]) if len(sys.argv) > 1 else 5

conn = get_connection("music.db")
orch = PipelineOrchestrator(conn, worker_id="analyze-test")

start = time.time()
processed = 0
while processed < BATCH_SIZE:
    if not orch.process_next("analyze"):
        print("No more analyze jobs available.")
        break
    processed += 1
    elapsed = time.time() - start
    print(f"  [{processed}/{BATCH_SIZE}] {elapsed:.1f}s elapsed")

elapsed = time.time() - start
print(f"\nAnalyzed {processed} tracks in {elapsed:.1f}s ({elapsed/max(processed,1):.1f}s per track)")
conn.close()
