"""Check failed jobs and print details."""
from musesleuth.db import get_connection

conn = get_connection("music.db")
rows = conn.execute(
    "SELECT j.metadata_id, j.last_error, t.full_path "
    "FROM jobs j JOIN tracks t ON j.metadata_id = t.metadata_id "
    "WHERE j.status = 'failed'"
).fetchall()

for r in rows:
    print(f"{r['full_path']}")
    print(f"  Error: {r['last_error']}")
    print()

print(f"Total failed: {len(rows)}")
conn.close()
