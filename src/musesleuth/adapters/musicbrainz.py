"""MusicBrainz API adapter for recording and artist metadata."""
from __future__ import annotations

import json
from typing import Optional

import requests

from musesleuth.adapters.base import BaseAdapter, AdapterResult
from musesleuth.adapters.cache import ScraperCache
from musesleuth.adapters.rate_limiter import RateLimiter


MB_API_URL = "https://musicbrainz.org/ws/2"
MB_USER_AGENT = "MuseSleuth/0.1.0 (https://github.com/musesleuth)"


class MusicBrainzAdapter(BaseAdapter):
    """Adapter that fetches metadata from the MusicBrainz API."""

    def __init__(self, cache: ScraperCache) -> None:
        self._cache = cache
        self._rate_limiter = RateLimiter(max_calls=1, period=1.0)

    @property
    def name(self) -> str:
        return "musicbrainz"

    def fetch_track_info(self, artist: str, title: str) -> AdapterResult:
        """Search for a recording on MusicBrainz."""
        cache_key = f"recording:{artist}:{title}"

        cached = self._cache.get(self.name, cache_key)
        if cached is not None:
            try:
                data = json.loads(cached)
                return AdapterResult(
                    source=self.name, success=True,
                    data=_parse_recording(data), cached=True,
                )
            except (json.JSONDecodeError, KeyError):
                pass

        try:
            raw = self._api_request({
                "query": f'artist:"{artist}" AND recording:"{title}"',
                "fmt": "json",
            })
            self._cache.put(self.name, cache_key, json.dumps(raw))
            return AdapterResult(
                source=self.name, success=True,
                data=_parse_recording(raw),
            )
        except Exception as exc:
            return AdapterResult(source=self.name, success=False, error=str(exc))

    def fetch_artist_info(self, artist: str) -> AdapterResult:
        """Lookup artist info on MusicBrainz."""
        cache_key = f"artist:{artist}"

        cached = self._cache.get(self.name, cache_key)
        if cached is not None:
            try:
                data = json.loads(cached)
                return AdapterResult(
                    source=self.name, success=True,
                    data=_parse_artist(data), cached=True,
                )
            except (json.JSONDecodeError, KeyError):
                pass

        try:
            raw = self._api_request({
                "query": f'artist:"{artist}"',
                "fmt": "json",
            }, endpoint="artist")
            self._cache.put(self.name, cache_key, json.dumps(raw))
            return AdapterResult(
                source=self.name, success=True,
                data=_parse_artist(raw),
            )
        except Exception as exc:
            return AdapterResult(source=self.name, success=False, error=str(exc))

    def _api_request(self, params: dict, endpoint: str = "recording") -> dict:
        """Make a MusicBrainz API request. Tests can mock this method."""
        self._rate_limiter.wait()
        headers = {"User-Agent": MB_USER_AGENT, "Accept": "application/json"}
        resp = requests.get(
            f"{MB_API_URL}/{endpoint}/",
            params=params,
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()


def _parse_recording(data: dict) -> dict:
    """Extract structured fields from a MusicBrainz recording search response."""
    result: dict = {}

    # Search endpoint returns {"recordings": [...]}, single lookup returns {"id": ...}
    recordings = data.get("recordings", [])
    if recordings:
        data = recordings[0]  # use best match
    elif data.get("id") is None:
        # Empty search result
        result["recording_id"] = None
        result["title"] = None
        result["length_ms"] = None
        result["isrcs"] = []
        result["tags"] = []
        return result

    result["recording_id"] = data.get("id")
    result["title"] = data.get("title")
    result["length_ms"] = data.get("length")

    # ISRCs
    result["isrcs"] = data.get("isrcs", [])

    # Artist credit
    artist_credits = data.get("artist-credit", [])
    if artist_credits:
        first_artist = artist_credits[0].get("artist", {})
        result["artist_id"] = first_artist.get("id")
        result["artist_name"] = first_artist.get("name")
        result["artist_country"] = first_artist.get("country")
        result["artist_disambiguation"] = first_artist.get("disambiguation")

    result["first_release_date"] = data.get("first-release-date")

    # Release info
    releases = data.get("releases", [])
    if releases:
        first_release = releases[0]
        result["release_title"] = first_release.get("title")
        result["release_date"] = first_release.get("date")
        result["release_country"] = first_release.get("country")
        rg = first_release.get("release-group", {})
        result["release_type"] = rg.get("primary-type")

    # Tags
    tags_raw = data.get("tags", [])
    result["tags"] = [t["name"] for t in tags_raw if isinstance(t, dict) and "name" in t]

    return result


def _parse_artist(data: dict) -> dict:
    """Extract structured fields from a MusicBrainz artist search response."""
    result: dict = {}

    artists = data.get("artists", [])
    if artists:
        data = artists[0]
    elif data.get("id") is None:
        return {"artist_id": None, "name": None, "type": None, "country": None,
                "active_years": None, "tags": []}

    result["artist_id"] = data.get("id")
    result["name"] = data.get("name")
    result["type"] = data.get("type")
    result["country"] = data.get("country")

    life_span = data.get("life-span", {})
    begin = life_span.get("begin", "")
    end = life_span.get("end", "")
    if begin:
        result["active_years"] = f"{begin}-{end}" if end else f"{begin}-present"
    else:
        result["active_years"] = None

    tags_raw = data.get("tags", [])
    result["tags"] = [t["name"] for t in tags_raw if isinstance(t, dict) and "name" in t]

    return result