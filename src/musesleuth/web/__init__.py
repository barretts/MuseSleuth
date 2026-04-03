"""MuseSleuth web interface for exploring the music database."""
from __future__ import annotations

from pathlib import Path

from musesleuth.web.app import build_app


def create_app(db_path: Path) -> "fastapi.FastAPI":
    """Factory that returns a configured FastAPI application."""
    return build_app(db_path)
