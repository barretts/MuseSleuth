from pathlib import Path
import sqlite3

DB = Path(r"Z:/music_new.db")
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

print("-- titles containing 'fly' --")
rows = conn.execute(
    """
    SELECT metadata_id, title, artist, album
    FROM tracks
    WHERE lower(title) LIKE '%fly%'
    ORDER BY title
    LIMIT 30
    """
).fetchall()
for row in rows:
    print(dict(row))

print("\n-- artist/title hints for gigi/amour --")
rows = conn.execute(
    """
    SELECT metadata_id, title, artist, album
    FROM tracks
    WHERE lower(artist) LIKE '%gigi%'
       OR lower(title) LIKE '%amour%'
       OR lower(album) LIKE '%amour%'
    ORDER BY artist, title
    LIMIT 30
    """
).fetchall()
for row in rows:
    print(dict(row))

print("\n-- token counts --")
rows = conn.execute(
    """
    SELECT token, COUNT(*) AS cnt
    FROM track_search_tokens
    WHERE token IN ('fly', 'll', 'with', 'you')
    GROUP BY token
    ORDER BY token
    """
).fetchall()
for row in rows:
    print(dict(row))
