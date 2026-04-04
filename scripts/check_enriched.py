"""Check enriched data for the 5 validated tracks."""
from musesleuth.db import get_connection

conn = get_connection("music.db")

rows = conn.execute("""
    SELECT t.title, t.artist, t.year,
           mf.bpm_final, mf.key_name, mf.key_mode,
           ps.decade_bucket, ps.bpm_bucket, ps.camelot_key, ps.energy_tier, ps.popularity_tier,
           ts_stat.listener_count, ts_stat.play_count
    FROM tracks t
    JOIN musical_features mf ON t.metadata_id = mf.metadata_id
    LEFT JOIN playlist_signals ps ON t.metadata_id = ps.metadata_id
    LEFT JOIN track_stats ts_stat ON t.metadata_id = ts_stat.metadata_id
    LIMIT 5
""").fetchall()

for r in rows:
    print(f"{r['artist']} - {r['title']} ({r['year']})")
    print(f"  BPM: {r['bpm_final']:.1f}  Key: {r['key_name']} {r['key_mode']}  Camelot: {r['camelot_key']}")
    print(f"  Decade: {r['decade_bucket']}  BPM bucket: {r['bpm_bucket']}  Energy: {r['energy_tier']}  Popularity: {r['popularity_tier']}")
    print(f"  Last.fm: {r['listener_count']} listeners, {r['play_count']} plays")
    print()

# External IDs
eids = conn.execute("SELECT source, COUNT(*) as c FROM external_ids GROUP BY source").fetchall()
print("External IDs:", {r['source']: r['c'] for r in eids})

# Genres/tags
tags = conn.execute("SELECT tag_value, COUNT(*) as c FROM genres_tags GROUP BY tag_value ORDER BY c DESC LIMIT 10").fetchall()
print("Top tags:", [(r['tag_value'], r['c']) for r in tags])

conn.close()
