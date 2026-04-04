"""Debug enrich adapter responses for one track."""
import os
import json
from pathlib import Path

from musesleuth.db import get_connection
from musesleuth.adapters.cache import ScraperCache
from musesleuth.adapters.lastfm import LastFMAdapter
from musesleuth.adapters.musicbrainz import MusicBrainzAdapter

# Load .env file
env_path = Path(__file__).resolve().parents[1] / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())

conn = get_connection("music_new.db")
cache = ScraperCache(conn)

# Pick a well-known track from the DB
track = conn.execute(
    "SELECT metadata_id, title, artist FROM tracks WHERE title LIKE '%Inner Space%' LIMIT 1"
).fetchone()
print(f"Track: {track['artist']} - {track['title']}")

# Test Last.fm
api_key = os.environ.get("LASTFM_API_KEY", "")
print(f"\nLast.fm API key: {'SET' if api_key else 'NOT SET'}")
lfm = LastFMAdapter(cache=cache, api_key=api_key)
result = lfm.fetch_track_info(track["artist"], track["title"])
print(f"Last.fm track: success={result.success}, cached={result.cached}")
if result.error:
    print(f"  Error: {result.error}")
if result.data:
    print(f"  Data: {json.dumps(result.data, indent=2)[:300]}")

# Test MusicBrainz
mb = MusicBrainzAdapter(cache=cache)
result = mb.fetch_track_info(track["artist"], track["title"])
print(f"\nMusicBrainz: success={result.success}, cached={result.cached}")
if result.error:
    print(f"  Error: {result.error}")
if result.data:
    print(f"  Data: {json.dumps(result.data, indent=2)[:300]}")

conn.close()
