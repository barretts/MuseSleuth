"""Export playlists to standard formats (M3U8, etc.)."""
from __future__ import annotations

import sqlite3
from pathlib import Path


def export_m3u8(conn: sqlite3.Connection, playlist_id: str) -> str:
    """Render a playlist as an M3U8 string.

    Returns the full text content ready to be written to a file.
    """
    rows = conn.execute(
        """
        SELECT t.full_path, t.title, t.artist, tf.duration_ms
        FROM playlist_tracks pt
        JOIN tracks t ON t.metadata_id = pt.metadata_id
        LEFT JOIN technical_features tf ON tf.metadata_id = pt.metadata_id
        WHERE pt.playlist_id = ?
        ORDER BY pt.position ASC
        """,
        (playlist_id,),
    ).fetchall()

    lines: list[str] = ["#EXTM3U"]
    for row in rows:
        duration_secs = int((row["duration_ms"] or 0) / 1000)
        artist = row["artist"] or "Unknown"
        title = row["title"] or "Unknown"
        lines.append(f"#EXTINF:{duration_secs},{artist} - {title}")
        lines.append(row["full_path"])

    return "\n".join(lines) + "\n"


def write_m3u8(conn: sqlite3.Connection, playlist_id: str, output_path: Path) -> int:
    """Write an M3U8 file for the given playlist. Returns track count."""
    content = export_m3u8(conn, playlist_id)
    output_path.write_text(content, encoding="utf-8")
    track_count = content.count("#EXTINF:")
    return track_count
