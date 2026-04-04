"""Filesystem scanning and path relocation for MuseSleuth tracks."""
from __future__ import annotations

import logging
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import mutagen

from musesleuth.db import generate_metadata_id
from musesleuth.filename_parser import parse_filename
from musesleuth.hasher import hash_partial
from musesleuth.job_queue import create_jobs_for_track
from musesleuth.probe import AUDIO_EXTENSIONS
from musesleuth.sidecar import SidecarData, read_sidecar, sidecar_path_for, write_sidecar

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ScanResult:
    """Aggregated result of a directory scan."""
    imported: int = 0
    relocated: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class RelocateResult:
    """Aggregated result of a relocate operation."""
    updated: int = 0
    unchanged: int = 0
    orphaned: int = 0
    missing_audio: int = 0
    hash_mismatches: int = 0
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal record types returned by workers
# ---------------------------------------------------------------------------

@dataclass
class _NewTrackRecord:
    """Prepared data for a newly-discovered audio file."""
    full_path: str
    file_path: str
    filename: str
    title: str
    artist: str
    album: str
    year: str
    track_number: str
    length_seconds: str
    file_size: str
    sidecar_data: Optional[SidecarData] = None


@dataclass
class _RelocateRecord:
    """Prepared data for a sidecar-based path update."""
    metadata_id: str
    old_full_path: str
    new_full_path: str
    new_file_path: str
    new_filename: str
    new_sidecar_path: str
    audio_path: Path
    sidecar_data: SidecarData


@dataclass
class _RelocateFileResult:
    """Result from a single relocate worker."""
    record: Optional[_RelocateRecord] = None
    unchanged: bool = False
    orphaned: bool = False
    missing_audio: bool = False
    hash_mismatch: bool = False
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Tag reading helper
# ---------------------------------------------------------------------------

def _read_tags(audio_path: Path) -> dict[str, str]:
    """Read embedded tags from an audio file via mutagen.

    Returns a dict with keys: title, artist, album, year, track_number,
    length_seconds, file_size.  Missing values are empty strings.
    """
    result: dict[str, str] = {
        "title": "",
        "artist": "",
        "album": "",
        "year": "",
        "track_number": "",
        "length_seconds": "",
        "file_size": "",
    }

    try:
        stat = audio_path.stat()
        result["file_size"] = str(stat.st_size)
    except OSError:
        pass

    try:
        audio = mutagen.File(str(audio_path), easy=True)
    except Exception:
        return result

    if audio is None:
        return result

    if hasattr(audio, "info") and audio.info is not None:
        try:
            result["length_seconds"] = str(int(audio.info.length))
        except Exception:
            pass

    tag_map = {
        "title": ["title"],
        "artist": ["artist", "albumartist"],
        "album": ["album"],
        "year": ["date", "year"],
        "track_number": ["tracknumber"],
    }

    for key, tag_names in tag_map.items():
        for tag in tag_names:
            val = audio.get(tag)
            if val:
                result[key] = str(val[0]) if isinstance(val, list) else str(val)
                break

    return result


# ---------------------------------------------------------------------------
# Scan: per-file worker
# ---------------------------------------------------------------------------

def _process_scan_file(audio_path: Path) -> tuple[str, _NewTrackRecord | None]:
    """Worker function: read tags, compute hash, prepare sidecar data.

    Returns (status, record) where status is one of:
    'new', 'error'.  record is None on error.
    """
    try:
        tags = _read_tags(audio_path)
        resolved = audio_path.resolve()
        full_path = str(resolved)
        file_path = str(resolved.parent) + os.sep
        filename = resolved.name

        if not tags["title"]:
            parsed = parse_filename(filename)
            if parsed["title"]:
                tags["title"] = parsed["title"]
            if not tags["artist"] and parsed["artist"]:
                tags["artist"] = parsed["artist"]

        try:
            h = hash_partial(resolved)
        except Exception:
            h = None

        stat = resolved.stat()
        sc_data = SidecarData(
            metadata_id="",  # filled in by caller after ULID generation
            path=full_path,
            size=stat.st_size,
            mtime=stat.st_mtime,
            hash_partial=h,
        )

        record = _NewTrackRecord(
            full_path=full_path,
            file_path=file_path,
            filename=filename,
            title=tags["title"],
            artist=tags["artist"],
            album=tags["album"],
            year=tags["year"],
            track_number=tags["track_number"],
            length_seconds=tags["length_seconds"],
            file_size=tags["file_size"],
            sidecar_data=sc_data,
        )
        return ("new", record)

    except Exception as exc:
        return ("error", None)


# ---------------------------------------------------------------------------
# Scan: main entry point
# ---------------------------------------------------------------------------

def scan_directory(
    conn: sqlite3.Connection,
    directory: Path,
    *,
    dry_run: bool = False,
    workers: int = 4,
) -> ScanResult:
    """Walk *directory* for audio files and import new ones into the DB.

    Files with an existing `.dlpmeta` sidecar whose metadata_id is already in
    the DB but at a stale path are silently relocated (hybrid scan+relocate).
    """
    result = ScanResult()
    directory = directory.resolve()

    if not directory.is_dir():
        result.errors.append(f"Not a directory: {directory}")
        return result

    # Phase 1: collect candidate audio files
    candidates: list[Path] = []
    for dirpath, _dirnames, filenames in os.walk(directory):
        for fname in filenames:
            if Path(fname).suffix.lower() in AUDIO_EXTENSIONS:
                candidates.append(Path(dirpath) / fname)

    if not candidates:
        return result

    # Build a set of known full_paths for fast dedup
    rows = conn.execute("SELECT full_path FROM tracks").fetchall()
    known_paths: set[str] = {r["full_path"] for r in rows}

    # Phase 2: triage -- skip known, detect sidecar-linked relocations, collect new
    to_process: list[Path] = []
    for audio_path in candidates:
        resolved = str(audio_path.resolve())

        if resolved in known_paths:
            result.skipped += 1
            continue

        sc = read_sidecar(audio_path)
        if sc is not None and sc.metadata_id:
            existing = conn.execute(
                "SELECT full_path FROM tracks WHERE metadata_id = ?",
                (sc.metadata_id,),
            ).fetchone()
            if existing is not None:
                if existing["full_path"] != resolved:
                    if not dry_run:
                        _relocate_single(conn, sc.metadata_id, existing["full_path"],
                                         audio_path.resolve())
                    result.relocated += 1
                else:
                    result.skipped += 1
                continue

        to_process.append(audio_path)

    if dry_run:
        result.imported = len(to_process)
        return result

    # Phase 3: parallel file I/O (tag reading, hashing)
    prepared: list[_NewTrackRecord] = []
    effective_workers = min(workers, len(to_process)) if to_process else 1

    if effective_workers <= 1:
        for ap in to_process:
            status, rec = _process_scan_file(ap)
            if status == "new" and rec is not None:
                prepared.append(rec)
            else:
                result.errors.append(f"Failed to read: {ap}")
    else:
        with ThreadPoolExecutor(max_workers=effective_workers) as pool:
            futures = {pool.submit(_process_scan_file, ap): ap for ap in to_process}
            for fut in as_completed(futures):
                ap = futures[fut]
                try:
                    status, rec = fut.result()
                    if status == "new" and rec is not None:
                        prepared.append(rec)
                    else:
                        result.errors.append(f"Failed to read: {ap}")
                except Exception as exc:
                    result.errors.append(f"Worker error for {ap}: {exc}")

    # Phase 4: serial DB writes + sidecar writes
    for rec in prepared:
        metadata_id = generate_metadata_id()

        try:
            conn.execute(
                """
                INSERT INTO tracks
                    (metadata_id, title, artist, album, track_number, year,
                     length_seconds, file_size, last_modified, file_path, filename, full_path)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    metadata_id,
                    rec.title,
                    rec.artist,
                    rec.album,
                    rec.track_number,
                    rec.year,
                    rec.length_seconds,
                    rec.file_size,
                    "",
                    rec.file_path,
                    rec.filename,
                    rec.full_path,
                ),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            result.skipped += 1
            continue

        create_jobs_for_track(conn, metadata_id)

        conn.execute(
            """
            UPDATE jobs SET status = 'done', completed_at = datetime('now'),
                   updated_at = datetime('now')
            WHERE metadata_id = ? AND stage = 'import'
            """,
            (metadata_id,),
        )
        conn.commit()

        # Write sidecar
        if rec.sidecar_data is not None:
            rec.sidecar_data.metadata_id = metadata_id
            audio_path = Path(rec.full_path)
            try:
                write_sidecar(audio_path, rec.sidecar_data)
                sc_path = str(sidecar_path_for(audio_path))
                conn.execute(
                    """
                    INSERT OR REPLACE INTO track_sidecars
                        (metadata_id, sidecar_path, hash_partial)
                    VALUES (?, ?, ?)
                    """,
                    (metadata_id, sc_path, rec.sidecar_data.hash_partial),
                )
                conn.commit()
            except OSError as exc:
                logger.warning("Could not write sidecar for %s: %s", rec.full_path, exc)

        result.imported += 1

    return result


# ---------------------------------------------------------------------------
# Relocate: shared helper
# ---------------------------------------------------------------------------

def _relocate_single(
    conn: sqlite3.Connection,
    metadata_id: str,
    old_full_path: str,
    new_resolved: Path,
) -> None:
    """Update DB paths for a single track that has moved."""
    new_full_path = str(new_resolved)
    new_file_path = str(new_resolved.parent) + os.sep
    new_filename = new_resolved.name

    conn.execute(
        """
        UPDATE tracks
        SET file_path = ?, filename = ?, full_path = ?, updated_at = datetime('now')
        WHERE metadata_id = ?
        """,
        (new_file_path, new_filename, new_full_path, metadata_id),
    )

    new_sc_path = str(sidecar_path_for(new_resolved))
    conn.execute(
        """
        INSERT OR REPLACE INTO track_sidecars (metadata_id, sidecar_path)
        VALUES (?, ?)
        """,
        (metadata_id, new_sc_path),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Relocate: per-file worker
# ---------------------------------------------------------------------------

def _process_relocate_file(
    sidecar_file: Path,
    known_ids: dict[str, str],
    verify_hash: bool,
) -> _RelocateFileResult:
    """Worker function: read sidecar, check audio, optionally verify hash.

    *known_ids* maps metadata_id -> current full_path from the DB.
    """
    res = _RelocateFileResult()

    try:
        raw_text = sidecar_file.read_text(encoding="utf-8")
    except OSError as exc:
        res.error = f"Cannot read sidecar {sidecar_file}: {exc}"
        return res

    import json
    try:
        raw = json.loads(raw_text)
    except (json.JSONDecodeError, ValueError) as exc:
        res.error = f"Bad JSON in {sidecar_file}: {exc}"
        return res

    metadata_id = raw.get("id", "")
    if not metadata_id:
        res.error = f"No metadata_id in {sidecar_file}"
        return res

    old_full_path = known_ids.get(metadata_id)
    if old_full_path is None:
        res.orphaned = True
        return res

    # Derive audio path from sidecar path (strip .dlpmeta)
    audio_path = Path(str(sidecar_file).removesuffix(".dlpmeta"))
    if not audio_path.exists():
        res.missing_audio = True
        return res

    new_resolved = audio_path.resolve()
    new_full_path = str(new_resolved)

    if new_full_path == old_full_path:
        res.unchanged = True
        return res

    if verify_hash:
        stored_hash = raw.get("hash_partial")
        if stored_hash:
            try:
                current_hash = hash_partial(new_resolved)
            except Exception:
                current_hash = None
            if current_hash is not None and current_hash != stored_hash:
                res.hash_mismatch = True
                return res

    new_file_path = str(new_resolved.parent) + os.sep
    new_filename = new_resolved.name
    new_sidecar_path = str(sidecar_path_for(new_resolved))

    sc_data = SidecarData(
        metadata_id=metadata_id,
        path=new_full_path,
        size=raw.get("size", 0),
        mtime=raw.get("mtime", 0.0),
        hash_partial=raw.get("hash_partial"),
        hash_full=raw.get("hash_full"),
        fingerprint=raw.get("fp"),
        version=raw.get("v", 1),
        created_at=raw.get("created_at", ""),
    )

    res.record = _RelocateRecord(
        metadata_id=metadata_id,
        old_full_path=old_full_path,
        new_full_path=new_full_path,
        new_file_path=new_file_path,
        new_filename=new_filename,
        new_sidecar_path=new_sidecar_path,
        audio_path=new_resolved,
        sidecar_data=sc_data,
    )
    return res


# ---------------------------------------------------------------------------
# Relocate: main entry point
# ---------------------------------------------------------------------------

def relocate_directory(
    conn: sqlite3.Connection,
    directory: Path,
    *,
    dry_run: bool = False,
    verify_hash: bool = False,
    workers: int = 4,
) -> RelocateResult:
    """Walk *directory* for `.dlpmeta` sidecar files and update DB paths.

    Each sidecar's ``metadata_id`` is matched against the ``tracks`` table.
    When the stored path differs from the file's current location, the DB is
    updated so that playlist exports and pipeline stages use the new path.
    """
    result = RelocateResult()
    directory = directory.resolve()

    if not directory.is_dir():
        result.errors.append(f"Not a directory: {directory}")
        return result

    # Phase 1: collect all .dlpmeta files
    sidecar_files: list[Path] = []
    for dirpath, _dirnames, filenames in os.walk(directory):
        for fname in filenames:
            if fname.endswith(".dlpmeta"):
                sidecar_files.append(Path(dirpath) / fname)

    if not sidecar_files:
        return result

    # Build lookup: metadata_id -> current full_path
    rows = conn.execute("SELECT metadata_id, full_path FROM tracks").fetchall()
    known_ids: dict[str, str] = {r["metadata_id"]: r["full_path"] for r in rows}

    # Phase 2: parallel file I/O
    file_results: list[_RelocateFileResult] = []
    effective_workers = min(workers, len(sidecar_files))

    if effective_workers <= 1:
        for sf in sidecar_files:
            file_results.append(
                _process_relocate_file(sf, known_ids, verify_hash)
            )
    else:
        with ThreadPoolExecutor(max_workers=effective_workers) as pool:
            futures = {
                pool.submit(_process_relocate_file, sf, known_ids, verify_hash): sf
                for sf in sidecar_files
            }
            for fut in as_completed(futures):
                sf = futures[fut]
                try:
                    file_results.append(fut.result())
                except Exception as exc:
                    fr = _RelocateFileResult(error=f"Worker error for {sf}: {exc}")
                    file_results.append(fr)

    # Phase 3: tally results and apply DB updates serially
    for fr in file_results:
        if fr.error:
            result.errors.append(fr.error)
            continue
        if fr.unchanged:
            result.unchanged += 1
            continue
        if fr.orphaned:
            result.orphaned += 1
            continue
        if fr.missing_audio:
            result.missing_audio += 1
            continue
        if fr.hash_mismatch:
            result.hash_mismatches += 1
            continue

        rec = fr.record
        if rec is None:
            continue

        if dry_run:
            result.updated += 1
            continue

        try:
            conn.execute(
                """
                UPDATE tracks
                SET file_path = ?, filename = ?, full_path = ?,
                    updated_at = datetime('now')
                WHERE metadata_id = ?
                """,
                (rec.new_file_path, rec.new_filename, rec.new_full_path,
                 rec.metadata_id),
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO track_sidecars (metadata_id, sidecar_path)
                VALUES (?, ?)
                """,
                (rec.metadata_id, rec.new_sidecar_path),
            )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            result.errors.append(
                f"DB conflict updating {rec.metadata_id}: {exc}"
            )
            continue

        # Rewrite the sidecar file with the updated path
        try:
            write_sidecar(rec.audio_path, rec.sidecar_data, preserve_created=True)
        except OSError as exc:
            logger.warning("Could not rewrite sidecar for %s: %s",
                           rec.new_full_path, exc)

        result.updated += 1

    return result
