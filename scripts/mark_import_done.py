"""Write .dlpmeta sidecars for all imported tracks, then mark import jobs done."""
import sys
from pathlib import Path

from musesleuth.db import get_connection
from musesleuth.sidecar import SidecarData, write_sidecar

conn = get_connection("music.db")

tracks = conn.execute(
    "SELECT metadata_id, full_path FROM tracks"
).fetchall()

written = 0
skipped = 0
errors = 0

for t in tracks:
    mid = t["metadata_id"]
    fpath = t["full_path"]
    audio_path = Path(fpath)

    if not audio_path.exists():
        skipped += 1
        continue

    # Check if sidecar already exists
    sc_path = Path(str(audio_path) + ".dlpmeta")
    if sc_path.exists():
        skipped += 1
        continue

    try:
        stat = audio_path.stat()
        sc_data = SidecarData(
            metadata_id=mid,
            path=fpath,
            size=stat.st_size,
            mtime=stat.st_mtime,
        )
        write_sidecar(audio_path, sc_data)

        # Record in DB
        conn.execute(
            """
            INSERT OR REPLACE INTO track_sidecars
                (metadata_id, sidecar_path)
            VALUES (?, ?)
            """,
            (mid, str(sc_path)),
        )
        written += 1
    except Exception as exc:
        errors += 1
        if errors <= 5:
            print(f"  Error: {fpath}: {exc}", file=sys.stderr)

    if (written + skipped + errors) % 5000 == 0:
        conn.commit()
        print(f"  Progress: {written} written, {skipped} skipped, {errors} errors")

conn.commit()
print(f"Sidecars: {written} written, {skipped} skipped, {errors} errors")

# Now mark import jobs as done
cur = conn.execute(
    "UPDATE jobs SET status='done', completed_at=datetime('now'), updated_at=datetime('now') "
    "WHERE stage='import' AND status='pending'"
)
conn.commit()
print(f"Marked {cur.rowcount} import jobs as done")
conn.close()
