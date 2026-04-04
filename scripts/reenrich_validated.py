"""Clear stale MB cache, reset enrich+signals jobs for the 5 analyzed tracks, re-run them."""
import time
from musesleuth.db import get_connection
from musesleuth.pipeline import PipelineOrchestrator

conn = get_connection("music.db")

# Get the 5 tracks that have musical_features (i.e. were analyzed)
mids = conn.execute(
    "SELECT metadata_id FROM musical_features"
).fetchall()
mid_list = [r["metadata_id"] for r in mids]
print(f"Re-enriching {len(mid_list)} tracks")

# Clear stale MB cache for these tracks
conn.execute("DELETE FROM scraper_cache WHERE adapter_name = 'musicbrainz'")
# Clear stale LFM cache too
conn.execute("DELETE FROM scraper_cache WHERE adapter_name = 'lastfm'")
# Clear existing enrichment data
for mid in mid_list:
    conn.execute("DELETE FROM track_stats WHERE metadata_id = ?", (mid,))
    conn.execute("DELETE FROM artist_stats WHERE metadata_id = ?", (mid,))
    conn.execute("DELETE FROM genres_tags WHERE metadata_id = ?", (mid,))
    conn.execute("DELETE FROM external_ids WHERE metadata_id = ? AND source IN ('musicbrainz','isrc')", (mid,))
    conn.execute("DELETE FROM playlist_signals WHERE metadata_id = ?", (mid,))

# Reset enrich + derive_signals jobs to pending
for stage in ["enrich", "derive_signals"]:
    placeholders = ",".join("?" * len(mid_list))
    conn.execute(
        f"UPDATE jobs SET status='pending', completed_at=NULL, last_error=NULL "
        f"WHERE stage=? AND metadata_id IN ({placeholders})",
        [stage] + mid_list,
    )
conn.commit()

# Re-run enrich + derive_signals
orch = PipelineOrchestrator(conn, worker_id="reenrich")
for stage in ["enrich", "derive_signals"]:
    print(f"\n=== {stage} ===")
    start = time.time()
    count = 0
    while count < len(mid_list):
        if not orch.process_next(stage):
            break
        count += 1
    elapsed = time.time() - start
    fails = conn.execute(
        "SELECT COUNT(*) as c FROM jobs WHERE stage=? AND status='failed' AND metadata_id IN ({})".format(
            ",".join("?" * len(mid_list))
        ),
        [stage] + mid_list,
    ).fetchone()["c"]
    print(f"  Processed: {count}, Failed: {fails}, Time: {elapsed:.1f}s")

conn.close()
