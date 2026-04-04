"""Push the 5 analyzed tracks through fingerprint_match, enrich, derive_signals."""
import os
import time

from musesleuth.db import get_connection
from musesleuth.pipeline import PipelineOrchestrator

conn = get_connection("music.db")
orch = PipelineOrchestrator(conn, worker_id="validate")

for stage in ["fingerprint_match", "enrich", "derive_signals"]:
    print(f"\n=== {stage} ===")
    start = time.time()
    count = 0
    while count < 5:
        if not orch.process_next(stage):
            break
        count += 1
    elapsed = time.time() - start

    # Check for failures
    fails = conn.execute(
        "SELECT COUNT(*) as c FROM jobs WHERE stage=? AND status='failed'",
        (stage,),
    ).fetchone()["c"]

    print(f"  Processed: {count}, Failed: {fails}, Time: {elapsed:.1f}s")

# Summary
print("\n=== Validation Summary ===")
for stage in ["fingerprint_match", "enrich", "derive_signals"]:
    done = conn.execute(
        "SELECT COUNT(*) as c FROM jobs WHERE stage=? AND status='done'",
        (stage,),
    ).fetchone()["c"]
    failed = conn.execute(
        "SELECT COUNT(*) as c FROM jobs WHERE stage=? AND status='failed'",
        (stage,),
    ).fetchone()["c"]
    print(f"  {stage:25s} done={done}, failed={failed}")

conn.close()
