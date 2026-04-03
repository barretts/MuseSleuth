"""Audio streaming endpoint with HTTP Range support."""
from __future__ import annotations

import mimetypes
import os
from pathlib import Path

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse, Response

router = APIRouter()

_CHUNK = 1024 * 256  # 256 KB chunks


@router.get("/audio/{metadata_id}")
async def stream_audio(request: Request, metadata_id: str):
    """Stream the audio file for a track, supporting Range requests for seeking."""
    db = request.app.state.db

    row = db.execute(
        "SELECT full_path FROM tracks WHERE metadata_id = ?", (metadata_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Track not found")

    file_path = Path(row["full_path"])
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Audio file not found on disk")

    file_size = file_path.stat().st_size
    content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"

    range_header = request.headers.get("range")

    if range_header:
        return _range_response(file_path, file_size, content_type, range_header)

    def _full_iter():
        with open(file_path, "rb") as f:
            while chunk := f.read(_CHUNK):
                yield chunk

    return StreamingResponse(
        _full_iter(),
        media_type=content_type,
        headers={
            "Accept-Ranges": "bytes",
            "Content-Length": str(file_size),
        },
    )


def _range_response(
    file_path: Path,
    file_size: int,
    content_type: str,
    range_header: str,
) -> Response:
    """Build a 206 Partial Content response."""
    try:
        unit, ranges = range_header.split("=", 1)
        start_str, end_str = ranges.split("-", 1)
        start = int(start_str) if start_str else 0
        end = int(end_str) if end_str else file_size - 1
    except (ValueError, TypeError):
        raise HTTPException(status_code=416, detail="Invalid range")

    if start >= file_size or end >= file_size:
        raise HTTPException(
            status_code=416,
            detail="Range not satisfiable",
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    length = end - start + 1

    def _range_iter():
        with open(file_path, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk_size = min(_CHUNK, remaining)
                data = f.read(chunk_size)
                if not data:
                    break
                remaining -= len(data)
                yield data

    return StreamingResponse(
        _range_iter(),
        status_code=206,
        media_type=content_type,
        headers={
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(length),
        },
    )
