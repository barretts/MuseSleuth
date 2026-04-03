"""Last.fm API adapter for track and artist metadata."""
from __future__ import annotations

import json
from typing import Optional

import requests

from musesleuth.adapters.base import BaseAdapter, AdapterResult
from musesleuth.adapters.cache import ScraperCache
from musesleuth.adapters.rate_limiter import RateLimiter


LASTFM_API_URL = "https://ws.audioscrobbler.com/2.0/"


class LastFMAdapter(BaseAdapter):
    """Adapter that fetches metadata from the Last.fm API."""

    def __init__(self, cache: ScraperCache, api_key: str = "") -> None:
        self._cache = cache
        self._api_key = api_key
        self._rate_limiter = RateLimiter(max_calls=5, period=1.0)

    @property
    def name(self) -> str:
        return "lastfm"

    def fetch_track_info(self, artist: str, title: str) -> AdapterResult:
        """Fetch track info from Last.fm."""
        cache_key = f"track:{artist}:{title}"

        cached = self._cache.get(self.name, cache_key)
        if cached is not None:
            try:
                data = json.loads(cached)
                return AdapterResult(
                    source=self.name, success=True,
                    data=_parse_track_response(data), cached=True,
                )
            except (json.JSONDecodeError, KeyError):
                pass

        try:
            raw = self._api_request({
                "method": "track.getInfo",
                "artist": artist,
                "track": title,
                "api_key": self._api_key,
                "format": "json",
            })
            self._cache.put(self.name, cache_key, json.dumps(raw))
            return AdapterResult(
                source=self.name, success=True,
                data=_parse_track_response(raw),
            )
        except Exception as exc:
            return AdapterResult(source=self.name, success=False, error=str(exc))

    def fetch_artist_info(self, artist: str) -> AdapterResult:
        """Fetch artist info from Last.fm."""
        cache_key = f"artist:{artist}"

        cached = self._cache.get(self.name, cache_key)
        if cached is not None:
            try:
                data = json.loads(cached)
                return AdapterResult(
                    source=self.name, success=True,
                    data=_parse_artist_response(data), cached=True,
                )
            except (json.JSONDecodeError, KeyError):
                pass

        try:
            raw = self._api_request({
                "method": "artist.getInfo",
                "artist": artist,
                "api_key": self._api_key,
                "format": "json",
            })
            self._cache.put(self.name, cache_key, json.dumps(raw))
            return AdapterResult(
                source=self.name, success=True,
                data=_parse_artist_response(raw),
            )
        except Exception as exc:
            return AdapterResult(source=self.name, success=False, error=str(exc))

    def _api_request(self, params: dict) -> dict:
        """Make a Last.fm API request. Tests can mock this method."""
        self._rate_limiter.wait()
        resp = requests.get(LASTFM_API_URL, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()


def _parse_track_response(data: dict) -> dict:
    """Extract structured fields from Last.fm track.getInfo response."""
    track = data.get("track", {})
    tags_raw = track.get("toptags", {}).get("tag", [])
    tags = []
    for t in tags_raw:
        if isinstance(t, dict) and "name" in t:
            tags.append({
                "name": t["name"],
                "count": int(t.get("count", 0)),
            })

    return {
        "listeners": int(track.get("listeners", 0)),
        "play_count": int(track.get("playcount", 0)),
        "duration_ms": int(track.get("duration", 0)),
        "tags": tags,
        "album": track.get("album", {}).get("title"),
        "url": track.get("url"),
        "wiki": track.get("wiki", {}).get("summary"),
    }


def _parse_artist_response(data: dict) -> dict:
    """Extract structured fields from Last.fm artist.getInfo response."""
    artist = data.get("artist", {})
    stats = artist.get("stats", {})
    similar_raw = artist.get("similar", {}).get("artist", [])
    similar = [s["name"] for s in similar_raw if isinstance(s, dict) and "name" in s]
    tags_raw = artist.get("tags", {}).get("tag", [])
    genres = [t["name"] for t in tags_raw if isinstance(t, dict) and "name" in t]

    return {
        "listeners": int(stats.get("listeners", 0)),
        "play_count": int(stats.get("playcount", 0)),
        "similar_artists": similar,
        "genres": genres,
        "bio": artist.get("bio", {}).get("summary"),
        "url": artist.get("url"),
    }