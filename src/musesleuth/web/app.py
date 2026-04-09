"""FastAPI application factory and configuration."""
from __future__ import annotations

import os
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from musesleuth.db import get_connection

_FRONTEND_DIST_DIR = Path(__file__).parents[3] / "frontend" / "dist"


def _distinct(conn: sqlite3.Connection, table: str, column: str) -> list[str]:
    rows = conn.execute(
        f"SELECT DISTINCT {column} FROM {table} WHERE {column} IS NOT NULL AND {column} != '' ORDER BY {column}"
    ).fetchall()
    return [str(r[0]) for r in rows]


def _load_filter_cache(conn: sqlite3.Connection) -> dict[str, list[str]]:
    return {
        "decades": _distinct(conn, "playlist_signals", "decade_bucket"),
        "bpm_buckets": _distinct(conn, "playlist_signals", "bpm_bucket"),
        "energy_tiers": _distinct(conn, "playlist_signals", "energy_tier"),
        "camelot_keys": _distinct(conn, "playlist_signals", "camelot_key"),
        "genres": _distinct(conn, "ml_features", "genre_primary"),
        "vocal_types": _distinct(conn, "ml_features", "vocal_type"),
    }


def _get_db(request: Request) -> sqlite3.Connection:
    """Retrieve the DB connection stored on app state."""
    return request.app.state.db


def build_app(db_path: Path) -> FastAPI:
    """Construct and return the FastAPI app wired to *db_path*."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.db = get_connection(db_path)
        app.state.filter_cache = _load_filter_cache(app.state.db)

        client_id = os.environ.get("MUSESLEUTH_SPOTIFY_CLIENT_ID", "")
        client_secret = os.environ.get("MUSESLEUTH_SPOTIFY_CLIENT_SECRET", "")
        if client_id and client_secret:
            from musesleuth.adapters.cache import ScraperCache
            from musesleuth.adapters.spotify import SpotifyAdapter
            app.state.spotify_adapter = SpotifyAdapter(
                cache=ScraperCache(app.state.db),
                client_id=client_id,
                client_secret=client_secret,
            )
        else:
            app.state.spotify_adapter = None

        yield
        app.state.db.close()

    app = FastAPI(
        title="MuseSleuth",
        description="Music metadata explorer",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5184", "http://127.0.0.1:5184"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from musesleuth.web.routes import tracks, track_detail, audio, status, playlists, spotify_import

    app.include_router(tracks.router, prefix="/api")
    app.include_router(track_detail.router, prefix="/api")
    app.include_router(audio.router, prefix="/api")
    app.include_router(status.router, prefix="/api")
    app.include_router(playlists.router, prefix="/api")
    app.include_router(spotify_import.router, prefix="/api")

    if _FRONTEND_DIST_DIR.exists():
        assets_dir = _FRONTEND_DIST_DIR / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="frontend-assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        requested = _FRONTEND_DIST_DIR / full_path
        if full_path and requested.is_file():
            return FileResponse(requested)
        index_file = _FRONTEND_DIST_DIR / "index.html"
        if index_file.exists():
            return FileResponse(index_file)
        return {"detail": "Frontend build not found. Run `npm run build` in frontend/."}

    return app
