#!/usr/bin/env python
r"""Export/import MuseSleuth per-track metadata sidecars plus SQLite backups.

The script writes two independent sidecar files per audio track:

  * ``<audio>.msmeta.json`` — a full dump of every per-track DB row.
  * ``<audio>.dlpmeta``     — the compact identity/hash record also written
                              by the regular MuseSleuth pipeline.

Both files embed an HMAC-SHA256 ``signature`` block (``sha256`` always
populated; ``hmac`` populated when ``MUSESLEUTH_SIDECAR_KEY`` is set). When
no signing key is configured the files are instead written as ``.bak``
variants (``*.msmeta.json.bak`` / ``*.dlpmeta.bak``) so that unsigned
exports are never mistaken for trusted snapshots.

Usage::

    python scripts/metadata_sidecar.py backup-db --db music.db [--output backup.db]
    python scripts/metadata_sidecar.py export-sidecars --db music.db [--dry-run]
    python scripts/metadata_sidecar.py import-sidecars --db music.db <dir> [--dry-run] [--create-missing]
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sqlite3
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from musesleuth.db import get_connection
from musesleuth.sidecar import (
    SIDECAR_BAK_EXT,
    SIDECAR_EXT,
    SIDECAR_VERSION,
    SIGNATURE_FIELD,
    SidecarData,
    _atomic_write_json,
    _win_long,
    build_dlpmeta_payload,
    get_signing_key,
    read_sidecar_raw,
    sidecar_from_payload,
    sidecar_path_for,
    sign_payload,
    verify_signature,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MSMETA_EXT = ".msmeta.json"
MSMETA_BAK_EXT = ".msmeta.json.bak"
SCHEMA_VERSION = 2
BLOB_MARKER = "__blob_b64__"


# Per-track DB tables that are exported as a single row keyed by ``metadata_id``.
SINGLE_ROW_TABLES: tuple[str, ...] = (
    "tracks",
    "track_sidecars",
    "technical_features",
    "musical_features",
    "ml_features",
    "loudness_features",
    "timbre_features",
    "tag_snapshot_raw",
    "track_lyrics",
    "playlist_signals",
    "beat_grids",
)

# Per-track DB tables that are exported as a list of rows keyed by ``metadata_id``.
MULTI_ROW_TABLES: tuple[str, ...] = (
    "external_ids",
    "artist_stats",
    "track_stats",
    "genres_tags",
    "structure_segments",
    "version_groups",
    "embeddings",
    "tag_writeback_log",
)

# Special case: ``similarity_edges`` is keyed by ``src_id``/``dst_id`` instead
# of ``metadata_id``. We export only outgoing edges (``src_id = metadata_id``)
# and skip rows on import whose ``dst_id`` is not known to the target DB.
OUTGOING_EDGE_TABLE = "similarity_edges"

# Columns that store BLOB data and need base64 encoding on export.
BLOB_COLUMNS: dict[str, set[str]] = {
    "timbre_features": {"mfcc_mean", "mfcc_var"},
    "embeddings": {"vector"},
}

# Auto-increment ID columns to skip on export/import for multi-row tables.
AUTO_ID_COLUMNS = frozenset({"id"})


# Disposition map for every ``CREATE TABLE`` in ``src/musesleuth/db/schema.py``.
# Every schema table must appear here exactly once. A schema-audit test
# asserts this to prevent silent regressions when the schema grows.
TABLE_DISPOSITION: dict[str, str] = {
    # --- included, single-row ---
    "tracks": "included-single",
    "track_sidecars": "included-single",
    "technical_features": "included-single",
    "musical_features": "included-single",
    "ml_features": "included-single",
    "loudness_features": "included-single",
    "timbre_features": "included-single",
    "tag_snapshot_raw": "included-single",
    "track_lyrics": "included-single",
    "playlist_signals": "included-single",
    "beat_grids": "included-single",
    # --- included, multi-row ---
    "external_ids": "included-multi",
    "artist_stats": "included-multi",
    "track_stats": "included-multi",
    "genres_tags": "included-multi",
    "structure_segments": "included-multi",
    "version_groups": "included-multi",
    "embeddings": "included-multi",
    "tag_writeback_log": "included-multi",
    # --- included, special (outgoing pairwise edges) ---
    "similarity_edges": "included-edges",
    # --- skipped, not per-track ---
    "playlists": "skipped-playlist-scoped",
    "playlist_tracks": "skipped-playlist-scoped",
    "subsonic_playlist_sync": "skipped-playlist-scoped",
    "subsonic_song_cache": "skipped-cache",
    "scraper_cache": "skipped-cache",
    "jobs": "skipped-process-state",
    "track_search_tokens": "skipped-derived-fts",
}


# ---------------------------------------------------------------------------
# Schema-audit helpers (used by tests)
# ---------------------------------------------------------------------------

_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)


def schema_table_names() -> set[str]:
    """Parse ``src/musesleuth/db/schema.py`` and return every ``CREATE TABLE`` name."""
    schema_path = SRC / "musesleuth" / "db" / "schema.py"
    text = schema_path.read_text(encoding="utf-8")
    return set(_CREATE_TABLE_RE.findall(text))


def schema_coverage_diff() -> tuple[set[str], set[str]]:
    """Return ``(missing_from_disposition, stale_disposition_entries)``."""
    schema = schema_table_names()
    declared = set(TABLE_DISPOSITION)
    return schema - declared, declared - schema


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [r[1] if isinstance(r, tuple) else r["name"] for r in rows]


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _serialize_value(value: Any, table: str, column: str) -> Any:
    if value is None:
        return None
    blob_cols = BLOB_COLUMNS.get(table, set())
    if column in blob_cols and isinstance(value, (bytes, memoryview)):
        return {BLOB_MARKER: base64.b64encode(bytes(value)).decode("ascii")}
    return value


def _deserialize_value(value: Any, table: str, column: str) -> Any:
    if value is None:
        return None
    if isinstance(value, dict) and BLOB_MARKER in value:
        return base64.b64decode(value[BLOB_MARKER])
    return value


def _serialize_row(
    row: sqlite3.Row,
    table: str,
    skip_columns: set[str] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in row.keys():
        if skip_columns and key in skip_columns:
            continue
        result[key] = _serialize_value(row[key], table, key)
    return result


# ---------------------------------------------------------------------------
# Sidecar path helpers
# ---------------------------------------------------------------------------

def msmeta_path_for(audio_path: Path, *, bak: bool = False) -> Path:
    ext = MSMETA_BAK_EXT if bak else MSMETA_EXT
    return Path(_win_long(str(audio_path) + ext))


def dlpmeta_bak_path_for(audio_path: Path) -> Path:
    return Path(_win_long(str(audio_path) + SIDECAR_BAK_EXT))


# ---------------------------------------------------------------------------
# .msmeta.json export
# ---------------------------------------------------------------------------

def export_track_metadata(
    conn: sqlite3.Connection,
    metadata_id: str,
) -> dict[str, Any] | None:
    """Build the unsigned ``.msmeta.json`` payload for *metadata_id*."""
    track_row = conn.execute(
        "SELECT * FROM tracks WHERE metadata_id = ?", (metadata_id,)
    ).fetchone()
    if track_row is None:
        return None

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "metadata_id": metadata_id,
        "full_path": track_row["full_path"],
        "filename": track_row["filename"],
        "exported_at": _now_iso(),
        "tables": {},
    }

    for table in SINGLE_ROW_TABLES:
        if not _table_exists(conn, table):
            continue
        row = conn.execute(
            f"SELECT * FROM {table} WHERE metadata_id = ?", (metadata_id,)
        ).fetchone()
        if row is not None:
            payload["tables"][table] = _serialize_row(
                row, table, skip_columns={"metadata_id"}
            )

    for table in MULTI_ROW_TABLES:
        if not _table_exists(conn, table):
            continue
        rows = conn.execute(
            f"SELECT * FROM {table} WHERE metadata_id = ?", (metadata_id,)
        ).fetchall()
        if rows:
            payload["tables"][table] = [
                _serialize_row(
                    r, table, skip_columns=AUTO_ID_COLUMNS | {"metadata_id"}
                )
                for r in rows
            ]

    if _table_exists(conn, OUTGOING_EDGE_TABLE):
        edges = conn.execute(
            f"SELECT * FROM {OUTGOING_EDGE_TABLE} WHERE src_id = ?",
            (metadata_id,),
        ).fetchall()
        if edges:
            payload["tables"][OUTGOING_EDGE_TABLE] = [
                _serialize_row(
                    r, OUTGOING_EDGE_TABLE, skip_columns=AUTO_ID_COLUMNS | {"src_id"}
                )
                for r in edges
            ]

    return payload


def write_metadata_sidecar(
    audio_path: Path,
    payload: dict[str, Any],
    *,
    key: bytes | None,
) -> tuple[Path, bool]:
    """Sign *payload* and atomically write it next to *audio_path*.

    Returns ``(written_path, signed)``. When *key* is ``None`` the file is
    written with the ``.msmeta.json.bak`` suffix.
    """
    payload[SIGNATURE_FIELD] = sign_payload(payload, key)
    signed = key is not None
    target = msmeta_path_for(audio_path, bak=not signed)
    _atomic_write_json(target, payload)
    return target, signed


# ---------------------------------------------------------------------------
# .dlpmeta export
# ---------------------------------------------------------------------------

def _sidecar_from_track_row(
    conn: sqlite3.Connection,
    track_row: sqlite3.Row,
    audio_path: Path,
) -> SidecarData:
    """Build a ``SidecarData`` from the ``tracks`` row, enriched with hashes."""
    sc_row = None
    if _table_exists(conn, "track_sidecars"):
        sc_row = conn.execute(
            "SELECT hash_partial, hash_full, fingerprint FROM track_sidecars "
            "WHERE metadata_id = ?",
            (track_row["metadata_id"],),
        ).fetchone()

    try:
        stat = audio_path.stat()
        size = int(stat.st_size)
        mtime = float(stat.st_mtime)
    except OSError:
        size = int(track_row["file_size"] or 0) if track_row["file_size"] else 0
        mtime = 0.0

    return SidecarData(
        metadata_id=track_row["metadata_id"],
        path=track_row["full_path"],
        size=size,
        mtime=mtime,
        hash_partial=sc_row["hash_partial"] if sc_row else None,
        hash_full=sc_row["hash_full"] if sc_row else None,
        fingerprint=sc_row["fingerprint"] if sc_row else None,
        version=SIDECAR_VERSION,
    )


def write_dlpmeta_sidecar(
    audio_path: Path,
    data: SidecarData,
    *,
    key: bytes | None,
) -> tuple[Path, bool]:
    """Sign and atomically write a ``.dlpmeta`` (or ``.dlpmeta.bak``) file."""
    data.updated_at = _now_iso()
    payload = build_dlpmeta_payload(data)
    payload[SIGNATURE_FIELD] = sign_payload(payload, key)
    signed = key is not None
    target = (
        sidecar_path_for(audio_path) if signed else dlpmeta_bak_path_for(audio_path)
    )
    _atomic_write_json(target, payload)
    return target, signed


# ---------------------------------------------------------------------------
# Bulk export
# ---------------------------------------------------------------------------

_STAT_KEYS: tuple[str, ...] = (
    "exported_msmeta",
    "exported_dlpmeta",
    "unsigned_msmeta",
    "unsigned_dlpmeta",
    "skipped_missing",
    "errors",
    "total",
)


def _empty_stats() -> dict[str, int]:
    return {k: 0 for k in _STAT_KEYS}


def parse_path_prefix_maps(raw_maps: Iterable[str]) -> tuple[tuple[str, str], ...]:
    """Parse ``SOURCE=TARGET`` strings into ``(source, target)`` tuples.

    Matches the semantics of ``musesleuth run --path-prefix-map`` in
    ``src/musesleuth/cli.py`` so users can reuse their existing maps.
    """
    parsed: list[tuple[str, str]] = []
    for raw in raw_maps:
        if "=" not in raw:
            raise ValueError(f"Invalid --path-prefix-map {raw!r}. Expected SOURCE=TARGET.")
        source, target = raw.split("=", 1)
        if not source or not target:
            raise ValueError(f"Invalid --path-prefix-map {raw!r}. Expected SOURCE=TARGET.")
        parsed.append((source, target))
    return tuple(parsed)


def apply_path_map(path: str, maps: tuple[tuple[str, str], ...]) -> str:
    """Rewrite *path* with the first matching ``(source, target)`` prefix."""
    for source, target in maps:
        if path.startswith(source):
            return target + path[len(source):]
    return path


def _export_one_track(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    dry_run: bool,
    key: bytes | None,
    path_maps: tuple[tuple[str, str], ...] = (),
) -> dict[str, int]:
    """Export sidecars for a single track using *conn*. Returns a stat delta.

    When *path_maps* is non-empty, ``tracks.full_path`` is rewritten via
    :func:`apply_path_map` before the filesystem existence check and sidecar
    writes. The DB row itself is left alone.
    """
    delta = _empty_stats()
    full_path = apply_path_map(row["full_path"], path_maps) if path_maps else row["full_path"]
    audio_path = Path(full_path)

    if not audio_path.exists():
        delta["skipped_missing"] += 1
        return delta

    payload = export_track_metadata(conn, row["metadata_id"])
    if payload is None:
        delta["errors"] += 1
        return delta

    if dry_run:
        delta["exported_msmeta"] += 1
        delta["exported_dlpmeta"] += 1
        if key is None:
            delta["unsigned_msmeta"] += 1
            delta["unsigned_dlpmeta"] += 1
        return delta

    try:
        _, signed = write_metadata_sidecar(audio_path, payload, key=key)
        delta["exported_msmeta"] += 1
        if not signed:
            delta["unsigned_msmeta"] += 1
    except Exception as exc:
        print(f"  Error writing msmeta for {full_path}: {exc}", file=sys.stderr)
        delta["errors"] += 1

    try:
        data = _sidecar_from_track_row(conn, row, audio_path)
        _, signed = write_dlpmeta_sidecar(audio_path, data, key=key)
        delta["exported_dlpmeta"] += 1
        if not signed:
            delta["unsigned_dlpmeta"] += 1
    except Exception as exc:
        print(f"  Error writing dlpmeta for {full_path}: {exc}", file=sys.stderr)
        delta["errors"] += 1

    return delta


def _merge_stats(dst: dict[str, int], src: dict[str, int]) -> None:
    for k, v in src.items():
        dst[k] = dst.get(k, 0) + v


def export_sidecars(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = False,
    key: bytes | None = None,
    workers: int = 1,
    db_path: Path | None = None,
    path_maps: tuple[tuple[str, str], ...] = (),
) -> dict[str, int]:
    """Export ``.msmeta.json`` + ``.dlpmeta`` sidecars for every track.

    ``workers`` > 1 runs the per-track work in a ``ThreadPoolExecutor``. Each
    worker opens its own ``sqlite3.Connection`` (requires *db_path*) because
    a single connection is not safe to share across threads under default
    Python SQLite settings.
    """
    stats = _empty_stats()

    rows = conn.execute(
        "SELECT metadata_id, full_path, filename, file_size FROM tracks"
    ).fetchall()
    stats["total"] = len(rows)

    if workers <= 1:
        for i, row in enumerate(rows, 1):
            _merge_stats(
                stats,
                _export_one_track(
                    conn, row, dry_run=dry_run, key=key, path_maps=path_maps
                ),
            )
            if i % 500 == 0:
                print(f"  Progress: {i}/{stats['total']}...")
        return stats

    if db_path is None:
        raise ValueError("workers>1 requires db_path so each worker can open its own connection")

    tls = threading.local()

    def _worker_conn() -> sqlite3.Connection:
        existing = getattr(tls, "conn", None)
        if existing is not None:
            return existing
        worker_conn = get_connection(db_path)
        tls.conn = worker_conn
        return worker_conn

    def _task(r: sqlite3.Row) -> dict[str, int]:
        return _export_one_track(
            _worker_conn(), r, dry_run=dry_run, key=key, path_maps=path_maps
        )

    completed = 0
    lock = threading.Lock()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_task, row) for row in rows]
        try:
            for fut in as_completed(futures):
                delta = fut.result()
                with lock:
                    _merge_stats(stats, delta)
                    completed += 1
                    if completed % 500 == 0:
                        print(f"  Progress: {completed}/{stats['total']}...")
        except KeyboardInterrupt:
            for f in futures:
                f.cancel()
            raise

    return stats


# ---------------------------------------------------------------------------
# .msmeta.json import
# ---------------------------------------------------------------------------

def read_metadata_sidecar(sidecar_path: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"  Error reading {sidecar_path}: {exc}", file=sys.stderr)
        return None
    if not isinstance(raw, dict) or "schema_version" not in raw or "metadata_id" not in raw:
        print(f"  Invalid sidecar format: {sidecar_path}", file=sys.stderr)
        return None
    return raw


def _import_single_row_table(
    conn: sqlite3.Connection,
    table: str,
    metadata_id: str,
    data: dict[str, Any],
) -> None:
    """Upsert a single-row, ``metadata_id``-keyed table.

    Uses ``INSERT ... ON CONFLICT(metadata_id) DO UPDATE`` so that columns
    absent from *data* (e.g. partial legacy payloads) keep their existing
    values instead of being reset to ``NULL``.
    """
    columns = _get_table_columns(conn, table)
    incoming: dict[str, Any] = {}
    for col in columns:
        if col == "metadata_id":
            continue
        if col in data:
            incoming[col] = _deserialize_value(data[col], table, col)

    exists = conn.execute(
        f"SELECT 1 FROM {table} WHERE metadata_id = ?", (metadata_id,)
    ).fetchone() is not None

    if exists:
        if not incoming:
            return
        set_clause = ", ".join(f"{c} = ?" for c in incoming)
        values = list(incoming.values()) + [metadata_id]
        conn.execute(
            f"UPDATE {table} SET {set_clause} WHERE metadata_id = ?", values
        )
    else:
        row_data = {"metadata_id": metadata_id, **incoming}
        col_names = list(row_data.keys())
        placeholders = ", ".join("?" for _ in col_names)
        col_str = ", ".join(col_names)
        values = [row_data[c] for c in col_names]
        conn.execute(
            f"INSERT INTO {table} ({col_str}) VALUES ({placeholders})", values
        )


def _import_multi_row_table(
    conn: sqlite3.Connection,
    table: str,
    metadata_id: str,
    rows_data: list[dict[str, Any]],
) -> None:
    conn.execute(f"DELETE FROM {table} WHERE metadata_id = ?", (metadata_id,))
    columns = _get_table_columns(conn, table)
    insert_cols = [c for c in columns if c not in AUTO_ID_COLUMNS]
    for row_data in rows_data:
        values: list[Any] = []
        for col in insert_cols:
            if col == "metadata_id":
                values.append(metadata_id)
            elif col in row_data:
                values.append(_deserialize_value(row_data[col], table, col))
            else:
                values.append(None)
        placeholders = ", ".join("?" for _ in insert_cols)
        col_str = ", ".join(insert_cols)
        conn.execute(
            f"INSERT INTO {table} ({col_str}) VALUES ({placeholders})",
            values,
        )


def _import_similarity_edges(
    conn: sqlite3.Connection,
    metadata_id: str,
    rows_data: list[dict[str, Any]],
) -> None:
    """Replace outgoing edges for *metadata_id*, skipping rows whose ``dst_id`` is unknown."""
    conn.execute(
        f"DELETE FROM {OUTGOING_EDGE_TABLE} WHERE src_id = ?", (metadata_id,)
    )
    columns = _get_table_columns(conn, OUTGOING_EDGE_TABLE)
    insert_cols = [c for c in columns if c not in AUTO_ID_COLUMNS]
    for row_data in rows_data:
        dst_id = row_data.get("dst_id")
        if not dst_id:
            continue
        exists = conn.execute(
            "SELECT 1 FROM tracks WHERE metadata_id = ?", (dst_id,)
        ).fetchone()
        if exists is None:
            continue
        values: list[Any] = []
        for col in insert_cols:
            if col == "src_id":
                values.append(metadata_id)
            elif col in row_data:
                values.append(_deserialize_value(row_data[col], OUTGOING_EDGE_TABLE, col))
            else:
                values.append(None)
        placeholders = ", ".join("?" for _ in insert_cols)
        col_str = ", ".join(insert_cols)
        conn.execute(
            f"INSERT INTO {OUTGOING_EDGE_TABLE} ({col_str}) VALUES ({placeholders})",
            values,
        )


def import_track_from_sidecar(
    conn: sqlite3.Connection,
    sidecar_path: Path,
    *,
    create_missing: bool = False,
    key: bytes | None = None,
) -> str:
    """Import a single ``.msmeta.json`` sidecar.

    Returns one of: ``updated``, ``created``, ``skipped``, ``error``,
    ``unsigned_updated``, ``unsigned_created``, ``corrupt``.
    """
    payload = read_metadata_sidecar(sidecar_path)
    if payload is None:
        return "error"

    sig_status = verify_signature(payload, key)
    if sig_status == "corrupt":
        print(f"  SHA-256 mismatch, refusing: {sidecar_path}", file=sys.stderr)
        return "corrupt"

    metadata_id = payload["metadata_id"]
    tables = payload.get("tables", {})

    existing = conn.execute(
        "SELECT metadata_id FROM tracks WHERE metadata_id = ?", (metadata_id,)
    ).fetchone()

    if existing is None and not create_missing:
        return "skipped"
    if existing is None and "tracks" not in tables:
        return "error"

    try:
        for table in SINGLE_ROW_TABLES:
            if table in tables and _table_exists(conn, table):
                _import_single_row_table(conn, table, metadata_id, tables[table])
        for table in MULTI_ROW_TABLES:
            if table in tables and _table_exists(conn, table):
                _import_multi_row_table(conn, table, metadata_id, tables[table])
        if (
            OUTGOING_EDGE_TABLE in tables
            and _table_exists(conn, OUTGOING_EDGE_TABLE)
        ):
            _import_similarity_edges(conn, metadata_id, tables[OUTGOING_EDGE_TABLE])
        conn.commit()
    except Exception as exc:
        print(f"  Error importing {sidecar_path}: {exc}", file=sys.stderr)
        conn.rollback()
        return "error"

    base = "created" if existing is None else "updated"
    if sig_status != "valid":
        _demote_to_bak(sidecar_path, MSMETA_EXT, MSMETA_BAK_EXT)
        return f"unsigned_{base}"
    return base


# ---------------------------------------------------------------------------
# .dlpmeta import
# ---------------------------------------------------------------------------

def import_dlpmeta_from_file(
    conn: sqlite3.Connection,
    sidecar_path: Path,
    *,
    create_missing: bool = False,
    key: bytes | None = None,
) -> str:
    """Import a ``.dlpmeta`` file into ``tracks`` + ``track_sidecars``.

    Returns one of the ``msmeta`` status strings, sharing the same vocabulary.
    """
    raw = read_sidecar_raw(sidecar_path)
    if raw is None:
        print(f"  Error reading {sidecar_path}", file=sys.stderr)
        return "error"

    sig_status = verify_signature(raw, key)
    if sig_status == "corrupt":
        print(f"  SHA-256 mismatch, refusing: {sidecar_path}", file=sys.stderr)
        return "corrupt"

    data = sidecar_from_payload(raw)
    if not data.metadata_id or not data.path:
        return "error"

    existing = conn.execute(
        "SELECT metadata_id FROM tracks WHERE metadata_id = ?", (data.metadata_id,)
    ).fetchone()

    try:
        if existing is None:
            if not create_missing:
                return "skipped"
            conn.execute(
                "INSERT INTO tracks (metadata_id, file_path, filename, full_path, file_size) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    data.metadata_id,
                    str(Path(data.path).parent) + os.sep,
                    Path(data.path).name,
                    data.path,
                    str(data.size) if data.size else None,
                ),
            )

        conn.execute(
            "INSERT OR REPLACE INTO track_sidecars "
            "(metadata_id, sidecar_path, hash_partial, hash_full, fingerprint, sidecar_version) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                data.metadata_id,
                str(sidecar_path_for(Path(data.path))),
                data.hash_partial,
                data.hash_full,
                data.fingerprint,
                data.version,
            ),
        )
        conn.commit()
    except Exception as exc:
        print(f"  Error importing dlpmeta {sidecar_path}: {exc}", file=sys.stderr)
        conn.rollback()
        return "error"

    base = "created" if existing is None else "updated"
    if sig_status != "valid":
        _demote_to_bak(sidecar_path, SIDECAR_EXT, SIDECAR_BAK_EXT)
        return f"unsigned_{base}"
    return base


# ---------------------------------------------------------------------------
# Bulk import
# ---------------------------------------------------------------------------

def _demote_to_bak(sidecar_path: Path, live_ext: str, bak_ext: str) -> None:
    """If *sidecar_path* has *live_ext*, rename it in-place to *bak_ext*."""
    name = str(sidecar_path)
    if not name.endswith(live_ext) or name.endswith(bak_ext):
        return
    new_name = name[: -len(live_ext)] + bak_ext
    try:
        os.replace(_win_long(name), _win_long(new_name))
    except OSError as exc:
        print(f"  Warning: could not rename {sidecar_path} to .bak: {exc}", file=sys.stderr)


def _walk_sidecars(directory: Path) -> tuple[list[Path], list[Path]]:
    """Return ``(dlpmeta_files, msmeta_files)`` including ``.bak`` variants."""
    dlp: list[Path] = []
    msm: list[Path] = []
    for dirpath, _, filenames in os.walk(directory):
        for fname in filenames:
            full = Path(dirpath) / fname
            lower = fname.lower()
            if lower.endswith(MSMETA_BAK_EXT) or lower.endswith(MSMETA_EXT):
                msm.append(full)
            elif lower.endswith(SIDECAR_BAK_EXT) or lower.endswith(SIDECAR_EXT):
                dlp.append(full)
    return dlp, msm


def import_sidecars(
    conn: sqlite3.Connection,
    directory: Path,
    *,
    dry_run: bool = False,
    create_missing: bool = False,
    key: bytes | None = None,
) -> dict[str, int]:
    """Walk *directory*; import ``.dlpmeta`` first, then ``.msmeta.json``."""
    stats: dict[str, int] = {
        "msmeta_updated": 0,
        "msmeta_created": 0,
        "msmeta_unsigned": 0,
        "msmeta_skipped": 0,
        "msmeta_corrupt": 0,
        "msmeta_errors": 0,
        "msmeta_total": 0,
        "dlpmeta_updated": 0,
        "dlpmeta_created": 0,
        "dlpmeta_unsigned": 0,
        "dlpmeta_skipped": 0,
        "dlpmeta_corrupt": 0,
        "dlpmeta_errors": 0,
        "dlpmeta_total": 0,
    }

    dlp_files, msm_files = _walk_sidecars(directory)
    stats["dlpmeta_total"] = len(dlp_files)
    stats["msmeta_total"] = len(msm_files)

    def _bump(prefix: str, result: str) -> None:
        if result == "error":
            stats[f"{prefix}_errors"] += 1
            return
        if result == "corrupt":
            stats[f"{prefix}_corrupt"] += 1
            return
        if result == "skipped":
            stats[f"{prefix}_skipped"] += 1
            return
        if result.startswith("unsigned_"):
            stats[f"{prefix}_unsigned"] += 1
            base = result[len("unsigned_"):]
            stats[f"{prefix}_{base}"] += 1
            return
        stats[f"{prefix}_{result}"] += 1

    # Process .dlpmeta first so identity rows exist before .msmeta.json imports.
    for i, sc in enumerate(dlp_files, 1):
        if dry_run:
            raw = read_sidecar_raw(sc)
            _bump("dlpmeta", "error" if raw is None else "updated")
            continue
        result = import_dlpmeta_from_file(
            conn, sc, create_missing=create_missing, key=key
        )
        _bump("dlpmeta", result)
        if i % 500 == 0:
            print(f"  dlpmeta progress: {i}/{stats['dlpmeta_total']}...")

    for i, sc in enumerate(msm_files, 1):
        if dry_run:
            payload = read_metadata_sidecar(sc)
            _bump("msmeta", "error" if payload is None else "updated")
            continue
        result = import_track_from_sidecar(
            conn, sc, create_missing=create_missing, key=key
        )
        _bump("msmeta", result)
        if i % 500 == 0:
            print(f"  msmeta progress: {i}/{stats['msmeta_total']}...")

    return stats


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------

def backup_db(db_path: Path, output_path: Path | None = None) -> Path:
    """Create an exact SQLite backup using the built-in backup API."""
    if output_path is None:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output_path = db_path.with_name(
            f"{db_path.stem}-backup-{timestamp}{db_path.suffix}"
        )

    source = sqlite3.connect(str(db_path))
    dest = sqlite3.connect(str(output_path))
    try:
        source.backup(dest)
    finally:
        dest.close()
        source.close()

    return output_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _format_export_stats(stats: dict[str, int], key_present: bool) -> str:
    mode = "signed" if key_present else "unsigned (.bak)"
    return (
        f"msmeta: {stats['exported_msmeta']} exported "
        f"({stats['unsigned_msmeta']} unsigned), "
        f"dlpmeta: {stats['exported_dlpmeta']} exported "
        f"({stats['unsigned_dlpmeta']} unsigned), "
        f"skipped_missing: {stats['skipped_missing']}, "
        f"errors: {stats['errors']}, "
        f"total_tracks: {stats['total']}, "
        f"mode: {mode}"
    )


def _format_import_stats(stats: dict[str, int]) -> str:
    return (
        f"dlpmeta: updated={stats['dlpmeta_updated']} created={stats['dlpmeta_created']} "
        f"unsigned={stats['dlpmeta_unsigned']} skipped={stats['dlpmeta_skipped']} "
        f"corrupt={stats['dlpmeta_corrupt']} errors={stats['dlpmeta_errors']} "
        f"total={stats['dlpmeta_total']}\n"
        f"msmeta:  updated={stats['msmeta_updated']} created={stats['msmeta_created']} "
        f"unsigned={stats['msmeta_unsigned']} skipped={stats['msmeta_skipped']} "
        f"corrupt={stats['msmeta_corrupt']} errors={stats['msmeta_errors']} "
        f"total={stats['msmeta_total']}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="MuseSleuth metadata sidecar export/import and database backup.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_backup = sub.add_parser("backup-db", help="Create an exact SQLite backup.")
    p_backup.add_argument("--db", required=True, type=Path)
    p_backup.add_argument("--output", type=Path)

    p_export = sub.add_parser(
        "export-sidecars",
        help="Export per-song .msmeta.json and .dlpmeta sidecars.",
    )
    p_export.add_argument("--db", required=True, type=Path)
    p_export.add_argument("--dry-run", action="store_true")
    p_export.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Parallel worker threads for per-track export (default: 1). "
             "Each worker opens its own SQLite connection. 4-16 is a good "
             "range for network/spinning disks.",
    )
    p_export.add_argument(
        "--path-prefix-map",
        action="append",
        default=[],
        metavar="SOURCE=TARGET",
        help="Rewrite stored track paths before looking up audio on disk. "
             "Example: --path-prefix-map I:\\Music=Y: (repeatable).",
    )

    p_import = sub.add_parser(
        "import-sidecars",
        help="Import .msmeta.json and .dlpmeta sidecars into the DB.",
    )
    p_import.add_argument("--db", required=True, type=Path)
    p_import.add_argument("directory", type=Path)
    p_import.add_argument("--dry-run", action="store_true")
    p_import.add_argument(
        "--create-missing",
        action="store_true",
        help="Create track rows for sidecars with no matching DB entry.",
    )

    args = parser.parse_args()

    if args.command == "backup-db":
        if not args.db.exists():
            print(f"Database not found: {args.db}", file=sys.stderr)
            sys.exit(1)
        output = backup_db(args.db, args.output)
        size_mb = output.stat().st_size / (1024 * 1024)
        print(f"Backup created: {output} ({size_mb:.1f} MB)")
        return

    if args.command == "export-sidecars":
        if not args.db.exists():
            print(f"Database not found: {args.db}", file=sys.stderr)
            sys.exit(1)
        key = get_signing_key()
        if key is None:
            print(
                "Warning: MUSESLEUTH_SIDECAR_KEY is not set. "
                "Exports will be written as *.msmeta.json.bak and *.dlpmeta.bak.",
                file=sys.stderr,
            )
        try:
            path_maps = parse_path_prefix_maps(args.path_prefix_map)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            sys.exit(2)
        conn = get_connection(args.db)
        prefix = "[dry-run] " if args.dry_run else ""
        print(f"{prefix}Exporting metadata sidecars...")
        if args.workers > 1:
            print(f"  Using {args.workers} parallel worker threads.")
        if path_maps:
            for source, target in path_maps:
                print(f"  Path map: {source!r} -> {target!r}")
        stats = export_sidecars(
            conn,
            dry_run=args.dry_run,
            key=key,
            workers=args.workers,
            db_path=args.db if args.workers > 1 else None,
            path_maps=path_maps,
        )
        print(f"{prefix}Done: {_format_export_stats(stats, key is not None)}")
        conn.close()
        return

    if args.command == "import-sidecars":
        if not args.db.exists():
            print(f"Database not found: {args.db}", file=sys.stderr)
            sys.exit(1)
        if not args.directory.is_dir():
            print(f"Directory not found: {args.directory}", file=sys.stderr)
            sys.exit(1)
        key = get_signing_key()
        conn = get_connection(args.db)
        prefix = "[dry-run] " if args.dry_run else ""
        print(f"{prefix}Importing metadata sidecars from {args.directory}...")
        stats = import_sidecars(
            conn,
            args.directory,
            dry_run=args.dry_run,
            create_missing=args.create_missing,
            key=key,
        )
        print(f"{prefix}Done:\n{_format_import_stats(stats)}")
        conn.close()
        return


if __name__ == "__main__":
    main()
