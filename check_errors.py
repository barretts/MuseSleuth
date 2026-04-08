"""Check loudness job failures."""
from musesleuth.db import get_connection

conn = get_connection(r"Z:\music_new.db")

rows = conn.execute(
    "SELECT last_error, COUNT(*) as cnt FROM jobs "
    "WHERE stage = 'loudness' AND status = 'failed' "
    "GROUP BY last_error ORDER BY cnt DESC LIMIT 10"
).fetchall()

for r in rows:
    err = r["last_error"][:200] if r["last_error"] else "(no error)"
    print(f"{r['cnt']:>6}  {err}")

total = conn.execute(
    "SELECT COUNT(*) FROM jobs WHERE stage = 'loudness' AND status = 'failed'"
).fetchone()[0]
pending = conn.execute(
    "SELECT COUNT(*) FROM jobs WHERE stage = 'loudness' AND status = 'pending'"
).fetchone()[0]
done = conn.execute(
    "SELECT COUNT(*) FROM jobs WHERE stage = 'loudness' AND status = 'done'"
).fetchone()[0]
print(f"\nLoudness: done={done}, pending={pending}, failed={total}")

conn.close()
