"""Spotify adapter -- fetch audio features and track metadata."""
from __future__ import annotations

import base64
from typing import Any

import requests

from musesleuth.adapters.base import AdapterResult, BaseAdapter
from musesleuth.adapters.cache import ScraperCache
from musesleuth.adapters.rate_limiter import RateLimiter


class SpotifyAdapter(BaseAdapter):
    """Adapter for Spotify API - audio features and track metadata."""

    BASE_URL = "https://api.spotify.com/v1"
    AUTH_URL = "https://accounts.spotify.com/api/token"

    def __init__(
        self,
        cache: ScraperCache,
        client_id: str,
        client_secret: str,
    ) -> None:
        self._cache = cache
        self._client_id = client_id
        self._client_secret = client_secret
        self._rate_limiter = RateLimiter(max_calls=10, period=1.0)
        self._access_token: str | None = None

    @property
    def name(self) -> str:
        return "spotify"

    def _get_access_token(self) -> str | None:
        """Get or refresh Spotify access token."""
        if self._access_token:
            return self._access_token

        if not self._client_id or not self._client_secret:
            return None

        auth_string = f"{self._client_id}:{self._client_secret}"
        auth_bytes = auth_string.encode("utf-8")
        auth_b64 = base64.b64encode(auth_bytes).decode("utf-8")

        try:
            resp = requests.post(
                self.AUTH_URL,
                headers={"Authorization": f"Basic {auth_b64}"},
                data={"grant_type": "client_credentials"},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            self._access_token = data.get("access_token")
            return self._access_token
        except requests.RequestException:
            return None

    def _api_get(self, endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Make authenticated GET request to Spotify API."""
        token = self._get_access_token()
        if not token:
            return None

        self._rate_limiter.wait()

        try:
            resp = requests.get(
                f"{self.BASE_URL}{endpoint}",
                headers={"Authorization": f"Bearer {token}"},
                params=params,
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException:
            return None

    def search_track(self, artist: str, title: str) -> str | None:
        """Search for a track and return its Spotify ID."""
        cache_key = f"spotify:search:{artist}:{title}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        query = f'artist:"{artist}" track:"{title}"'
        data = self._api_get("/search", {"q": query, "type": "track", "limit": 1})

        if not data:
            return None

        tracks = data.get("tracks", {}).get("items", [])
        if not tracks:
            return None

        track_id = tracks[0]["id"]
        self._cache.set(cache_key, track_id)
        return track_id

    def get_audio_features(self, track_id: str) -> dict[str, Any] | None:
        """Get audio features for a track."""
        cache_key = f"spotify:features:{track_id}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        data = self._api_get(f"/audio-features/{track_id}")
        if not data:
            return None

        features = {
            "danceability": data.get("danceability"),
            "energy": data.get("energy"),
            "key": data.get("key"),
            "loudness": data.get("loudness"),
            "mode": data.get("mode"),
            "speechiness": data.get("speechiness"),
            "acousticness": data.get("acousticness"),
            "instrumentalness": data.get("instrumentalness"),
            "liveness": data.get("liveness"),
            "valence": data.get("valence"),
            "tempo": data.get("tempo"),
            "duration_ms": data.get("duration_ms"),
            "time_signature": data.get("time_signature"),
        }

        self._cache.set(cache_key, features)
        return features

    def fetch_track_info(self, artist: str, title: str) -> AdapterResult:
        """Fetch track info and audio features from Spotify."""
        if not self._client_id or not self._client_secret:
            return AdapterResult(
                source=self.name,
                success=False,
                error="SPOTIFY_CLIENT_ID or SPOTIFY_CLIENT_SECRET not set",
            )

        track_id = self.search_track(artist, title)
        if not track_id:
            return AdapterResult(
                source=self.name,
                success=False,
                error="Track not found on Spotify",
            )

        features = self.get_audio_features(track_id)
        if not features:
            return AdapterResult(
                source=self.name,
                success=False,
                error="Could not fetch audio features",
            )

        # Get track info
        track_data = self._api_get(f"/tracks/{track_id}")
        if track_data:
            features["track_name"] = track_data.get("name")
            features["artist_name"] = ", ".join(
                a.get("name", "") for a in track_data.get("artists", [])
            )
            features["album_name"] = track_data.get("album", {}).get("name")
            features["popularity"] = track_data.get("popularity")
            features["preview_url"] = track_data.get("preview_url")
            features["external_url"] = track_data.get("external_urls", {}).get("spotify")

        return AdapterResult(
            source=self.name,
            success=True,
            data=features,
        )

    def fetch_artist_info(self, artist: str) -> AdapterResult:
        """Fetch artist info from Spotify."""
        if not self._client_id or not self._client_secret:
            return AdapterResult(
                source=self.name,
                success=False,
                error="SPOTIFY_CLIENT_ID or SPOTIFY_CLIENT_SECRET not set",
            )

        cache_key = f"spotify:artist:{artist}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return AdapterResult(
                source=self.name,
                success=True,
                data=cached,
                cached=True,
            )

        data = self._api_get("/search", {"q": f'artist:"{artist}"', "type": "artist", "limit": 1})
        if not data:
            return AdapterResult(
                source=self.name,
                success=False,
                error="Artist search failed",
            )

        artists = data.get("artists", {}).get("items", [])
        if not artists:
            return AdapterResult(
                source=self.name,
                success=False,
                error="Artist not found",
            )

        artist_data = artists[0]
        result = {
            "id": artist_data.get("id"),
            "name": artist_data.get("name"),
            "genres": artist_data.get("genres", []),
            "popularity": artist_data.get("popularity"),
            "followers": artist_data.get("followers", {}).get("total"),
            "external_url": artist_data.get("external_urls", {}).get("spotify"),
        }

        self._cache.set(cache_key, result)
        return AdapterResult(
            source=self.name,
            success=True,
            data=result,
        )