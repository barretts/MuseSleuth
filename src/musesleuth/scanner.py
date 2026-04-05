"""Filesystem scanning and path relocation for MuseSleuth tracks."""
from __future__ import annotations

import json
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
    lyrics_found: int = 0
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
    genre: str = ""
    album_artist: str = ""
    disc_number: str = ""
    total_tracks: str = ""
    original_year: str = ""
    label: str = ""
    raw_tags_json: str = "{}"
    embedded_ids: dict[str, list[str]] = field(default_factory=dict)
    lyrics_path: Optional[str] = None
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

def _read_tags(audio_path: Path) -> dict:
    """Read embedded tags from an audio file via mutagen (easy=False).

    Returns a dict with basic fields (title, artist, album, year,
    track_number, length_seconds, file_size), rich fields (genre,
    album_artist, disc_number, total_tracks, original_year, label),
    embedded_ids dict mapping source names to lists of IDs, and
    raw_tags_json with the full serialised tag dict.
    """
    result: dict = {
        "title": "",
        "artist": "",
        "album": "",
        "year": "",
        "track_number": "",
        "length_seconds": "",
        "file_size": "",
        "genre": "",
        "album_artist": "",
        "disc_number": "",
        "total_tracks": "",
        "original_year": "",
        "label": "",
        "embedded_ids": {},
        "raw_tags_json": "{}",
    }

    try:
        stat = audio_path.stat()
        result["file_size"] = str(stat.st_size)
    except OSError:
        pass

    try:
        audio = mutagen.File(str(audio_path), easy=False)
    except Exception:
        return result

    if audio is None:
        return result

    if hasattr(audio, "info") and audio.info is not None:
        try:
            result["length_seconds"] = str(int(audio.info.length))
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Build a flat normalized tag dict from whatever format mutagen found
    # ------------------------------------------------------------------
    raw_tags: dict[str, str] = {}
    norm: dict[str, str] = {}

    tags_obj = audio.tags
    if tags_obj is None:
        result["raw_tags_json"] = json.dumps({})
        return result

    if hasattr(tags_obj, "getall"):
        # ID3-style (MP3, AIFF, …)
        for frame_id in tags_obj:
            frame = tags_obj[frame_id]
            text_val = _extract_id3_text(frame)
            if text_val is not None:
                raw_tags[frame_id] = text_val
                key = frame_id.split(":")[0] if ":" in frame_id else frame_id
                norm[key.lower()] = text_val
                # Also store TXXX descriptors with their description as key
                if frame_id.startswith("TXXX:"):
                    desc = frame_id[5:].lower().replace(" ", "_")
                    norm[desc] = text_val
    elif hasattr(tags_obj, "items"):
        # Vorbis comments (FLAC, OGG) or MP4 tags
        for key, value in tags_obj.items():
            if isinstance(value, list):
                # MP4 tuples like trkn = [(1, 10)]
                if value and isinstance(value[0], tuple):
                    text_val = str(value[0][0])
                    # Store second element as total
                    if len(value[0]) > 1 and value[0][1]:
                        raw_tags[key + "_total"] = str(value[0][1])
                        norm[key.lower() + "_total"] = str(value[0][1])
                else:
                    text_val = str(value[0]) if value else ""
            elif hasattr(value, "decode"):
                text_val = value.decode("utf-8", errors="replace")
            else:
                text_val = str(value)
            raw_tags[key] = text_val
            norm[key.lower()] = text_val

    result["raw_tags_json"] = json.dumps(raw_tags, ensure_ascii=False)

    # ------------------------------------------------------------------
    # Map normalized keys → result fields
    # ------------------------------------------------------------------
    _FIELD_MAP = {
        "title":         ["tit2", "title", "\xa9nam"],
        "artist":        ["tpe1", "artist", "\xa9art"],
        "album":         ["talb", "album", "\xa9alb"],
        "year":          ["tdrc", "tyer", "date", "year", "\xa9day"],
        "track_number":  ["trck", "tracknumber", "trkn"],
        "genre":         ["tcon", "genre", "\xa9gen"],
        "album_artist":  ["tpe2", "albumartist", "aart"],
        "disc_number":   ["tpos", "discnumber", "disk"],
        "total_tracks":  ["tracktotal", "totaltracks", "trck_total", "trkn_total"],
        "original_year": ["tdor", "originaldate", "originalyear"],
        "label":         ["tpub", "label", "organization"],
    }

    for field, candidates in _FIELD_MAP.items():
        for key in candidates:
            val = norm.get(key, "")
            if val:
                result[field] = val
                break

    # Handle "x/y" track number format (e.g. "2/32")
    if "/" in result["track_number"] and not result["total_tracks"]:
        parts = result["track_number"].split("/", 1)
        result["track_number"] = parts[0]
        result["total_tracks"] = parts[1]

    # Handle "x/y" disc number format
    if "/" in result["disc_number"]:
        parts = result["disc_number"].split("/", 1)
        result["disc_number"] = parts[0]

    # ------------------------------------------------------------------
    # Extract embedded MusicBrainz / external IDs
    # ------------------------------------------------------------------
    _ID_MAP = {
        "musicbrainz":              ["musicbrainz_trackid", "musicbrainz_recording_id"],
        "musicbrainz_artist":       ["musicbrainz_artistid"],
        "musicbrainz_album":        ["musicbrainz_albumid"],
        "musicbrainz_releasegroup": ["musicbrainz_releasegroupid"],
        "musicbrainz_releasetrack": ["musicbrainz_releasetrackid"],
        "musicbrainz_albumartist":  ["musicbrainz_albumartistid"],
        "embedded_isrc":            ["isrc", "tsrc"],
    }

    embedded_ids: dict[str, list[str]] = {}
    for source, tag_keys in _ID_MAP.items():
        for tk in tag_keys:
            val = norm.get(tk, "")
            if val:
                ids = [v.strip() for v in val.split(",") if v.strip()]
                if not ids:
                    ids = [val]
                embedded_ids.setdefault(source, []).extend(ids)
                break

    # Also check ISRC which may have multiple values in raw_tags
    isrc_raw = raw_tags.get("isrc", "")
    if not isrc_raw:
        isrc_raw = raw_tags.get("TSRC", "")
    if isrc_raw and "embedded_isrc" not in embedded_ids:
        ids = [v.strip() for v in isrc_raw.split(",") if v.strip()]
        if ids:
            embedded_ids["embedded_isrc"] = ids

    result["embedded_ids"] = embedded_ids
    return result


def _extract_id3_text(frame: object) -> Optional[str]:
    """Extract text content from a mutagen ID3 frame."""
    if hasattr(frame, "text") and frame.text:
        return str(frame.text[0])
    if hasattr(frame, "url"):
        return str(frame.url)
    return None


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

        # Discover lyric sidecar (.lrc file with same stem)
        lyrics_path: Optional[str] = None
        lrc_candidate = resolved.with_suffix(".lrc")
        if lrc_candidate.exists():
            lyrics_path = str(lrc_candidate)

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
            genre=tags["genre"],
            album_artist=tags["album_artist"],
            disc_number=tags["disc_number"],
            total_tracks=tags["total_tracks"],
            original_year=tags["original_year"],
            label=tags["label"],
            raw_tags_json=tags["raw_tags_json"],
            embedded_ids=tags["embedded_ids"],
            lyrics_path=lyrics_path,
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
                     length_seconds, file_size, last_modified, file_path, filename, full_path,
                     genre, album_artist, disc_number, total_tracks, original_year, label)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    rec.genre,
                    rec.album_artist,
                    rec.disc_number,
                    rec.total_tracks,
                    rec.original_year,
                    rec.label,
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

        # Store raw tag snapshot
        if rec.raw_tags_json and rec.raw_tags_json != "{}":
            conn.execute(
                """
                INSERT OR REPLACE INTO tag_snapshot_raw (metadata_id, tags_json)
                VALUES (?, ?)
                """,
                (metadata_id, rec.raw_tags_json),
            )

        # Store embedded external IDs (MusicBrainz, ISRC, etc.)
        for source, id_list in rec.embedded_ids.items():
            for ext_id in id_list:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO external_ids
                        (metadata_id, source, external_id, confidence)
                    VALUES (?, ?, ?, ?)
                    """,
                    (metadata_id, source, ext_id, 1.0),
                )

        # Store embedded genre tag
        if rec.genre:
            conn.execute(
                """
                INSERT OR IGNORE INTO genres_tags
                    (metadata_id, tag_type, tag_value, source)
                VALUES (?, ?, ?, ?)
                """,
                (metadata_id, "genre", rec.genre, "embedded"),
            )

        # Store lyric sidecar
        if rec.lyrics_path:
            try:
                lrc_size = Path(rec.lyrics_path).stat().st_size
            except OSError:
                lrc_size = None
            lrc_type = Path(rec.lyrics_path).suffix.lstrip(".") or "lrc"
            conn.execute(
                """
                INSERT OR REPLACE INTO track_lyrics
                    (metadata_id, lyrics_path, lyrics_type, file_size)
                VALUES (?, ?, ?, ?)
                """,
                (metadata_id, rec.lyrics_path, lrc_type, lrc_size),
            )
            result.lyrics_found += 1

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

    # Update lyric sidecar path if one is tracked
    lrc_row = conn.execute(
        "SELECT lyrics_path FROM track_lyrics WHERE metadata_id = ?",
        (metadata_id,),
    ).fetchone()
    if lrc_row:
        new_lrc = new_resolved.with_suffix(".lrc")
        if new_lrc.exists():
            conn.execute(
                "UPDATE track_lyrics SET lyrics_path = ? WHERE metadata_id = ?",
                (str(new_lrc), metadata_id),
            )
        elif Path(lrc_row["lyrics_path"]).exists():
            # Lyric file didn't move — keep old path
            pass
        else:
            # Both old and new are gone — remove stale row
            conn.execute(
                "DELETE FROM track_lyrics WHERE metadata_id = ?",
                (metadata_id,),
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

        # Update lyric sidecar path if one is tracked
        lrc_row = conn.execute(
            "SELECT lyrics_path FROM track_lyrics WHERE metadata_id = ?",
            (rec.metadata_id,),
        ).fetchone()
        if lrc_row:
            new_lrc = rec.audio_path.with_suffix(".lrc")
            if new_lrc.exists():
                conn.execute(
                    "UPDATE track_lyrics SET lyrics_path = ? WHERE metadata_id = ?",
                    (str(new_lrc), rec.metadata_id),
                )
                conn.commit()

        result.updated += 1

    return result
