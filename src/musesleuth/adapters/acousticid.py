"""AcousticID adapter -- lookup tracks by Chromaprint fingerprint."""
from __future__ import annotations

import json
from typing import Any

import requests

from musesleuth.adapters.base import AdapterResult, BaseAdapter
from musesleuth.adapters.cache import ScraperCache
from musesleuth.adapters.rate_limiter import RateLimiter


class AcousticIDAdapter(BaseAdapter):
    """Adapter for AcousticID fingerprint-based track lookup."""

    BASE_URL = "https://api.acoustid.org/v2"

    def __init__(self, cache: ScraperCache, api_key: str) -> None:
        self._cache = cache
        self._api_key = api_key
        self._rate_limiter = RateLimiter(max_calls=3, period=1.0)  # 3 calls per second

    @property
    def name(self) -> str:
        return "acousticid"

    def lookup_by_fingerprint(
        self,
        fingerprint: str,
        duration: int | None = None,
    ) -> AdapterResult:
        """Lookup a track by its Chromaprint fingerprint.

        Args:
            fingerprint: The Chromaprint fingerprint string
            duration: Track duration in seconds (improves accuracy)

        Returns:
            AdapterResult with matched recording info
        """
        cache_key = f"acousticid:fp:{fingerprint[:50]}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return AdapterResult(
                source=self.name,
                success=True,
                data=cached,
                cached=True,
            )

        if not self._api_key:
            return AdapterResult(
                source=self.name,
                success=False,
                error="ACOUSTID_API_KEY not set",
            )

        self._rate_limiter.wait()

        params: dict[str, Any] = {
            "client": self._api_key,
            "meta": "recordings releaseids sources",
            "fingerprint": fingerprint,
        }
        if duration:
            params["duration"] = str(duration)

        try:
            resp = requests.post(
                f"{self.BASE_URL}/lookup",
                data=params,
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            return AdapterResult(
                source=self.name,
                success=False,
                error=str(exc),
            )

        if data.get("status") != "ok":
            return AdapterResult(
                source=self.name,
                success=False,
                error=f"AcousticID error: {data.get('error', {}).get('message', 'Unknown')}",
            )

        results = data.get("results", [])
        if not results:
            return AdapterResult(
                source=self.name,
                success=False,
                error="No matches found",
            )

        # Get best match (highest score)
        best = max(results, key=lambda r: r.get("score", 0))
        score = best.get("score", 0)

        # Extract recording info
        recordings = best.get("recordings", [])
        if not recordings:
            return AdapterResult(
                source=self.name,
                success=False,
                error="No recording data in match",
            )

        recording = recordings[0]  # Take first recording
        result_data = {
            "score": score,
            "recording_id": recording.get("id"),
            "recording_title": recording.get("title"),
            "duration": recording.get("duration"),
            "artists": [
                {"id": a.get("id"), "name": a.get("name")}
                for a in recording.get("artists", [])
            ],
            "release_ids": [
                r.get("id") for r in recording.get("releases", [])
            ],
            "sources": recording.get("sources", []),
        }

        # Cache the result
        self._cache.set(cache_key, result_data)

        return AdapterResult(
            source=self.name,
            success=True,
            data=result_data,
        )

    def fetch_track_info(self, artist: str, title: str) -> AdapterResult:
        """Not implemented - use lookup_by_fingerprint instead."""
        return AdapterResult(
            source=self.name,
            success=False,
            error="AcousticID requires fingerprint, not artist/title",
        )

    def fetch_artist_info(self, artist: str) -> AdapterResult:
        """Not implemented - AcousticID is track-focused."""
        return AdapterResult(
            source=self.name,
            success=False,
            error="AcousticID does not support artist lookup",
        )