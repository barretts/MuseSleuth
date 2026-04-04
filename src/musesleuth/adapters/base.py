"""Base adapter interface for all metadata source adapters."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class AdapterResult:
    """Standardized result from any adapter call."""

    source: str
    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    cached: bool = False


class BaseAdapter(ABC):
    """Abstract base class for metadata source adapters.

    All adapters must implement fetch_track_info and fetch_artist_info.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the adapter's unique name."""
        ...

    @abstractmethod
    def fetch_track_info(self, artist: str, title: str) -> AdapterResult:
        """Fetch metadata for a track."""
        ...

    @abstractmethod
    def fetch_artist_info(self, artist: str) -> AdapterResult:
        """Fetch metadata for an artist."""
        ...