"""FastAPI application factory and configuration."""
from __future__ import annotations

import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from musesleuth.db import get_connection

_FRONTEND_DIST_DIR = Path(__file__).parents[3] / "frontend" / "dist"


def _get_db(request: Request) -> sqlite3.Connection:
    """Retrieve the DB connection stored on app state."""
    return request.app.state.db


def build_app(db_path: Path) -> FastAPI:
    """Construct and return the FastAPI app wired to *db_path*."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.db = get_connection(db_path)
        yield
        app.state.db.close()

    app = FastAPI(
        title="MuseSleuth",
        description="Music metadata explorer",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from musesleuth.web.routes import tracks, track_detail, audio, status, playlists

    app.include_router(tracks.router, prefix="/api")
    app.include_router(track_detail.router, prefix="/api")
    app.include_router(audio.router, prefix="/api")
    app.include_router(status.router, prefix="/api")
    app.include_router(playlists.router, prefix="/api")

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
