from pathlib import Path

from musesleuth.db import get_connection
from musesleuth.db.search import search_tracks, tokenize

conn = get_connection(Path(r"Z:/music_new.db"))
query = "I'll fly with you"
print("TOKENS", sorted(tokenize(query)))
ids = search_tracks(conn, query)
print("MATCH_COUNT", len(ids))
print("MATCH_IDS", ids[:10])
rows = conn.execute(
    "SELECT metadata_id, title, artist, album FROM tracks WHERE metadata_id IN ({})".format(
        ",".join("?" for _ in ids[:10])
    ),
    ids[:10],
).fetchall() if ids else []
for row in rows:
    print(dict(row))
