"""Backfill enrichment data from existing scraper_cache.

Re-parses cached Last.fm and MusicBrainz API responses to extract fields
that weren't stored during the original enrich run:

  - Last.fm artist genres -> genres_tags (source='lastfm_artist')
  - Last.fm artist bio/url -> artist_stats
  - Last.fm track wiki/url -> track_stats
  - Last.fm track album -> tracks.album (fills empty only)
  - MusicBrainz release date -> tracks.year (fills empty only)
  - MusicBrainz artist disambiguation -> artist_stats

Run: python scripts/backfill_enrich_extras.py --db music_new.db [--dry-run]
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from musesleuth.db import get_connection, create_schema


def backfill(db_path: str, dry_run: bool = False) -> None:
    conn = get_connection(Path(db_path))
    create_schema(conn)

    # Build mapping: metadata_id -> enriched track's artist+title (for cache key matching)
    # The cache keys are like "track:{artist}:{title}" and "artist:{artist}"
    # But we can join via jobs to find which metadata_ids have been enriched
    enriched = conn.execute(
        "SELECT metadata_id FROM jobs WHERE stage = 'enrich' AND status = 'done'"
    ).fetchall()
    enriched_ids = {r["metadata_id"] for r in enriched}

    tracks = conn.execute(
        "SELECT metadata_id, title, artist, album, year FROM tracks"
    ).fetchall()
    track_map: dict[str, dict] = {}
    for t in tracks:
        track_map[t["metadata_id"]] = {
            "title": t["title"] or "",
            "artist": t["artist"] or "",
            "album": t["album"] or "",
            "year": t["year"] or "",
        }

    # --- Last.fm artist genres -> genres_tags ---
    print("=== Backfilling Last.fm artist genres -> genres_tags ===")
    artist_genre_count = 0
    rows = conn.execute(
        "SELECT metadata_id, genres FROM artist_stats WHERE source = 'lastfm' AND genres IS NOT NULL"
    ).fetchall()
    for r in rows:
        mid = r["metadata_id"]
        try:
            genres = json.loads(r["genres"])
        except (json.JSONDecodeError, TypeError):
            continue
        for tag in genres:
            if not tag:
                continue
            if not dry_run:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO genres_tags
                        (metadata_id, tag_type, tag_value, source)
                    VALUES (?, ?, ?, ?)
                    """,
                    (mid, "genre", tag, "lastfm_artist"),
                )
            artist_genre_count += 1
    if not dry_run:
        conn.commit()
    print(f"  {'Would insert' if dry_run else 'Inserted'} {artist_genre_count} artist genre tags "
          f"from {len(rows)} artist_stats rows")

    # --- Last.fm artist bio/url from cache ---
    print("\n=== Backfilling Last.fm artist bio + url -> artist_stats ===")
    bio_count = 0
    cache_rows = conn.execute(
        "SELECT cache_key, payload FROM scraper_cache "
        "WHERE adapter_name = 'lastfm' AND cache_key LIKE 'artist:%'"
    ).fetchall()

    artist_cache: dict[str, dict] = {}
    for cr in cache_rows:
        key = cr["cache_key"]
        artist_name = key[len("artist:"):]
        try:
            data = json.loads(cr["payload"])
        except (json.JSONDecodeError, TypeError):
            continue
        artist = data.get("artist", {})
        if not artist:
            continue
        bio = artist.get("bio", {}).get("summary", "").strip() or None
        url = artist.get("url") or None
        if bio or url:
            artist_cache[artist_name.lower()] = {"bio": bio, "url": url}

    for mid, info in track_map.items():
        if mid not in enriched_ids:
            continue
        artist_key = info["artist"].lower()
        cached = artist_cache.get(artist_key)
        if cached and (cached["bio"] or cached["url"]):
            if not dry_run:
                conn.execute(
                    """
                    UPDATE artist_stats SET bio = COALESCE(bio, ?), url = COALESCE(url, ?)
                    WHERE metadata_id = ? AND source = 'lastfm'
                    """,
                    (cached["bio"], cached["url"], mid),
                )
            bio_count += 1
    if not dry_run:
        conn.commit()
    print(f"  {'Would update' if dry_run else 'Updated'} {bio_count} artist_stats rows with bio/url")

    # --- Last.fm track wiki/url + album from cache ---
    print("\n=== Backfilling Last.fm track wiki + url -> track_stats, album -> tracks ===")
    wiki_count = 0
    album_count = 0
    track_cache_rows = conn.execute(
        "SELECT cache_key, payload FROM scraper_cache "
        "WHERE adapter_name = 'lastfm' AND cache_key LIKE 'track:%'"
    ).fetchall()

    track_cache: dict[str, dict] = {}
    for cr in track_cache_rows:
        key = cr["cache_key"]
        parts = key[len("track:"):].rsplit(":", 1)
        if len(parts) != 2:
            continue
        cache_artist, cache_title = parts[0].lower(), parts[1].lower()
        try:
            data = json.loads(cr["payload"])
        except (json.JSONDecodeError, TypeError):
            continue
        track = data.get("track", {})
        if not track:
            continue
        wiki = track.get("wiki", {})
        wiki_summary = wiki.get("summary", "").strip() if isinstance(wiki, dict) else None
        url = track.get("url") or None
        album = track.get("album", {})
        album_title = album.get("title") if isinstance(album, dict) else None
        track_cache[(cache_artist, cache_title)] = {
            "wiki": wiki_summary or None,
            "url": url,
            "album": album_title,
        }

    for mid, info in track_map.items():
        if mid not in enriched_ids:
            continue
        cache_key = (info["artist"].lower(), info["title"].lower())
        cached = track_cache.get(cache_key)
        if not cached:
            continue

        if cached["wiki"] or cached["url"]:
            if not dry_run:
                conn.execute(
                    """
                    UPDATE track_stats SET wiki = COALESCE(wiki, ?), url = COALESCE(url, ?)
                    WHERE metadata_id = ? AND source = 'lastfm'
                    """,
                    (cached["wiki"], cached["url"], mid),
                )
            wiki_count += 1

        if cached["album"] and not info["album"]:
            if not dry_run:
                conn.execute(
                    """
                    UPDATE tracks SET album = ?, updated_at = datetime('now')
                    WHERE metadata_id = ? AND (album IS NULL OR album = '')
                    """,
                    (cached["album"], mid),
                )
            album_count += 1

    if not dry_run:
        conn.commit()
    print(f"  {'Would update' if dry_run else 'Updated'} {wiki_count} track_stats rows with wiki/url")
    print(f"  {'Would fill' if dry_run else 'Filled'} {album_count} empty album fields")

    # --- MusicBrainz release date -> year, disambiguation -> artist_stats ---
    print("\n=== Backfilling MusicBrainz year + disambiguation ===")
    year_count = 0
    disambig_count = 0
    mb_cache_rows = conn.execute(
        "SELECT cache_key, payload FROM scraper_cache "
        "WHERE adapter_name = 'musicbrainz' AND cache_key LIKE 'recording:%'"
    ).fetchall()

    mb_cache: dict[str, dict] = {}
    for cr in mb_cache_rows:
        key = cr["cache_key"]
        parts = key[len("recording:"):].rsplit(":", 1)
        if len(parts) != 2:
            continue
        cache_artist, cache_title = parts[0].lower(), parts[1].lower()
        try:
            data = json.loads(cr["payload"])
        except (json.JSONDecodeError, TypeError):
            continue
        recs = data.get("recordings", [])
        if not recs:
            continue
        rec = recs[0]

        first_release_date = rec.get("first-release-date") or ""
        release_date = ""
        releases = rec.get("releases", [])
        if releases:
            release_date = releases[0].get("date", "") or ""

        best_date = first_release_date or release_date
        year = str(best_date)[:4] if best_date else ""

        disambiguation = None
        artist_credits = rec.get("artist-credit", [])
        if artist_credits:
            artist_obj = artist_credits[0].get("artist", {})
            disambiguation = artist_obj.get("disambiguation") or None

        mb_cache[(cache_artist, cache_title)] = {
            "year": year if year and year.isdigit() else "",
            "disambiguation": disambiguation,
        }

    for mid, info in track_map.items():
        if mid not in enriched_ids:
            continue
        cache_key = (info["artist"].lower(), info["title"].lower())
        cached = mb_cache.get(cache_key)
        if not cached:
            continue

        if cached["year"] and not info["year"]:
            if not dry_run:
                conn.execute(
                    """
                    UPDATE tracks SET year = ?, updated_at = datetime('now')
                    WHERE metadata_id = ? AND (year IS NULL OR year = '')
                    """,
                    (cached["year"], mid),
                )
            year_count += 1

        if cached["disambiguation"]:
            if not dry_run:
                conn.execute(
                    """
                    UPDATE artist_stats SET disambiguation = ?
                    WHERE metadata_id = ? AND source = 'musicbrainz'
                    """,
                    (cached["disambiguation"], mid),
                )
            disambig_count += 1

    if not dry_run:
        conn.commit()
    print(f"  {'Would fill' if dry_run else 'Filled'} {year_count} empty year fields from MB release dates")
    print(f"  {'Would set' if dry_run else 'Set'} {disambig_count} artist disambiguation values")

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Backfill enrichment extras from scraper cache")
    parser.add_argument("--db", required=True, help="SQLite database path")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change without modifying")
    args = parser.parse_args()
    backfill(args.db, dry_run=args.dry_run)
