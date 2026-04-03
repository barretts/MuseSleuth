"""Opus migration helpers for conversion + database reconciliation."""
from __future__ import annotations

import csv
import json
import os
import shutil
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from musesleuth.hasher import hash_partial
from musesleuth.sidecar import SIDECAR_EXT, SidecarData, sidecar_path_for, write_sidecar

_AUDIO_EXTS = {
    ".mp3",
    ".flac",
    ".wav",
    ".m4a",
    ".aac",
    ".ogg",
    ".wma",
    ".opus",
}


@dataclass(frozen=True)
class ConversionTask:
    """Single file conversion plan item."""

    metadata_id: str
    source_path: Path
    target_path: Path


@dataclass
class ConversionResult:
    """Outcome for one conversion task."""

    task: ConversionTask
    status: str
    error: str = ""
    source_size: int = 0
    target_size: int = 0
    source_duration_seconds: str = ""
    target_bitrate_kbps: int = 0
    source_codec: str = ""
    source_bitrate_kbps: int = 0


@dataclass
class BatchSummary:
    """Aggregate counts from a conversion batch."""

    converted: int = 0
    skipped_existing: int = 0
    failed: int = 0
    relocated: int = 0
    unchanged: int = 0
    orphaned: int = 0
    missing_audio: int = 0
    hash_mismatches: int = 0


@dataclass
class SidecarRepairSummary:
    """Counts from repairing Opus sidecars from sibling source sidecars."""

    repaired: int = 0
    missing_source_sidecar: int = 0
    read_errors: int = 0
    write_errors: int = 0


@dataclass
class ReconcileSummary:
    """Counts from final DB path reconciliation to Opus files."""

    updated: int = 0
    already_opus: int = 0
    missing_opus: int = 0


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def backup_database(db_path: Path, backup_dir: Path) -> Path:
    """Create a SQLite backup via sqlite3 backup API."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    out_path = backup_dir / f"{db_path.stem}.backup-{_utc_stamp()}{db_path.suffix}"
    src_conn = sqlite3.connect(str(db_path))
    dst_conn = sqlite3.connect(str(out_path))
    try:
        src_conn.backup(dst_conn)
    finally:
        dst_conn.close()
        src_conn.close()
    return out_path


def export_track_inventory(conn: sqlite3.Connection, output_csv: Path) -> int:
    """Export a baseline inventory manifest from DB tables."""
    rows = conn.execute(
        """
        SELECT t.metadata_id, t.full_path, t.file_path, t.filename, t.file_size,
               t.length_seconds, t.updated_at, s.sidecar_path
        FROM tracks t
        LEFT JOIN track_sidecars s ON s.metadata_id = t.metadata_id
        ORDER BY t.full_path
        """
    ).fetchall()
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "metadata_id",
                "full_path",
                "file_path",
                "filename",
                "file_size",
                "length_seconds",
                "updated_at",
                "sidecar_path",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(dict(row))
    return len(rows)


def plan_tasks(
    conn: sqlite3.Connection,
    source_root: Path | None,
    target_root: Path | None,
    *,
    limit: int | None = None,
) -> list[ConversionTask]:
    """Build conversion tasks based on current DB path inventory."""
    source_root_resolved = source_root.resolve() if source_root is not None else None
    target_root_resolved = target_root.resolve() if target_root is not None else None
    rows = conn.execute(
        """
        SELECT metadata_id, full_path
        FROM tracks
        ORDER BY full_path
        """
    ).fetchall()

    tasks: list[ConversionTask] = []
    for row in rows:
        src = Path(row["full_path"])
        if src.suffix.lower() == ".opus":
            continue
        if not src.exists():
            continue
        src_resolved = src.resolve()
        if source_root_resolved is not None:
            try:
                rel = src_resolved.relative_to(source_root_resolved)
            except ValueError:
                continue
            if target_root_resolved is None:
                target = (source_root_resolved / rel).with_suffix(".opus")
            else:
                target = (target_root_resolved / rel).with_suffix(".opus")
        else:
            if target_root_resolved is None:
                # In-place sibling output when roots are omitted.
                target = src_resolved.with_suffix(".opus")
            else:
                target = _path_under_target(src_resolved, target_root_resolved).with_suffix(".opus")
        tasks.append(
            ConversionTask(
                metadata_id=row["metadata_id"],
                source_path=src_resolved,
                target_path=target,
            )
        )
        if limit is not None and len(tasks) >= limit:
            break
    return tasks


def run_conversion_batch(
    conn: sqlite3.Connection,
    tasks: Iterable[ConversionTask],
    *,
    bitrate_kbps: int,
    workers: int,
    dry_run: bool,
    manifest_path: Path,
    scaling: bool = True,
    run_relocate: bool = True,
    relocate_workers: int = 8,
) -> BatchSummary:
    """Convert tasks, write sidecars, optionally relocate DB paths."""
    task_list = list(tasks)
    summary = BatchSummary()
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    if dry_run:
        with manifest_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "metadata_id",
                    "source_path",
                    "target_path",
                    "status",
                    "source_size",
                    "target_size",
                    "target_bitrate_kbps",
                    "source_codec",
                    "source_bitrate_kbps",
                    "error",
                ],
            )
            writer.writeheader()
            for task in task_list:
                source_codec, source_bitrate = _probe_source_profile(task.source_path)
                target_bitrate = (
                    _choose_target_bitrate(
                        source_codec=source_codec,
                        source_bitrate_kbps=source_bitrate,
                        max_bitrate_kbps=bitrate_kbps,
                        scaling=scaling,
                    )
                )
                writer.writerow(
                    {
                        "metadata_id": task.metadata_id,
                        "source_path": str(task.source_path),
                        "target_path": str(task.target_path),
                        "status": "dry_run",
                        "source_size": task.source_path.stat().st_size,
                        "target_size": "",
                        "target_bitrate_kbps": target_bitrate,
                        "source_codec": source_codec,
                        "source_bitrate_kbps": source_bitrate,
                        "error": "",
                    }
                )
        return summary

    results: list[ConversionResult] = []
    max_workers = max(1, min(workers, len(task_list))) if task_list else 1
    if max_workers == 1:
        for task in task_list:
            results.append(
                _convert_one(
                    task,
                    bitrate_kbps=bitrate_kbps,
                    scaling=scaling,
                )
            )
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(
                    _convert_one,
                    task,
                    bitrate_kbps=bitrate_kbps,
                    scaling=scaling,
                ): task
                for task in task_list
            }
            for future in as_completed(futures):
                results.append(future.result())

    for result in results:
        if result.status == "converted":
            summary.converted += 1
        elif result.status == "skipped_existing":
            summary.skipped_existing += 1
        else:
            summary.failed += 1

    with manifest_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "metadata_id",
                "source_path",
                "target_path",
                "status",
                "source_size",
                "target_size",
                "target_bitrate_kbps",
                "source_codec",
                "source_bitrate_kbps",
                "error",
            ],
        )
        writer.writeheader()
        for result in results:
            writer.writerow(
                {
                    "metadata_id": result.task.metadata_id,
                    "source_path": str(result.task.source_path),
                    "target_path": str(result.task.target_path),
                    "status": result.status,
                    "source_size": result.source_size,
                    "target_size": result.target_size,
                    "target_bitrate_kbps": result.target_bitrate_kbps,
                    "source_codec": result.source_codec,
                    "source_bitrate_kbps": result.source_bitrate_kbps,
                    "error": result.error,
                }
            )

    # Sync DB paths directly to target opus paths for this batch.
    if run_relocate and task_list:
        updated = _sync_db_paths_for_tasks(conn, task_list)
        summary.relocated += updated
    return summary


def _convert_one(
    task: ConversionTask,
    *,
    bitrate_kbps: int,
    scaling: bool,
) -> ConversionResult:
    source_size = 0
    try:
        source_size = task.source_path.stat().st_size
    except OSError:
        return ConversionResult(task=task, status="failed", error="missing_source")

    source_codec, source_bitrate = _probe_source_profile(task.source_path)
    target_bitrate = _choose_target_bitrate(
        source_codec=source_codec,
        source_bitrate_kbps=source_bitrate,
        max_bitrate_kbps=bitrate_kbps,
        scaling=scaling,
    )

    task.target_path.parent.mkdir(parents=True, exist_ok=True)
    if task.target_path.exists():
        return ConversionResult(
            task=task,
            status="skipped_existing",
            source_size=source_size,
            target_size=task.target_path.stat().st_size,
            target_bitrate_kbps=target_bitrate,
            source_codec=source_codec,
            source_bitrate_kbps=source_bitrate,
        )

    temp_target = task.target_path.with_name(
        f"{task.target_path.stem}.tmp{task.target_path.suffix}"
    )
    if temp_target.exists():
        try:
            temp_target.unlink()
        except OSError:
            return ConversionResult(
                task=task,
                status="failed",
                error="stale_temp_unremovable",
                source_size=source_size,
                target_bitrate_kbps=target_bitrate,
                source_codec=source_codec,
                source_bitrate_kbps=source_bitrate,
            )

    cmd = [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(task.source_path),
        "-map_metadata",
        "0",
        "-vn",
        "-c:a",
        "libopus",
        "-b:a",
        f"{target_bitrate}k",
        "-vbr",
        "on",
        str(temp_target),
    ]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        try:
            if temp_target.exists():
                temp_target.unlink()
        except OSError:
            pass
        return ConversionResult(
            task=task,
            status="failed",
            error=(proc.stderr or proc.stdout).strip()[:500],
            source_size=source_size,
            target_bitrate_kbps=target_bitrate,
            source_codec=source_codec,
            source_bitrate_kbps=source_bitrate,
        )

    if not temp_target.exists():
        return ConversionResult(
            task=task,
            status="failed",
            error="missing_temp_output",
            source_size=source_size,
            target_bitrate_kbps=target_bitrate,
            source_codec=source_codec,
            source_bitrate_kbps=source_bitrate,
        )

    if temp_target.stat().st_size <= 0:
        try:
            temp_target.unlink()
        except OSError:
            pass
        return ConversionResult(
            task=task,
            status="failed",
            error="empty_temp_output",
            source_size=source_size,
            target_bitrate_kbps=target_bitrate,
            source_codec=source_codec,
            source_bitrate_kbps=source_bitrate,
        )

    try:
        os.replace(temp_target, task.target_path)
    except OSError as exc:
        try:
            if temp_target.exists():
                temp_target.unlink()
        except OSError:
            pass
        return ConversionResult(
            task=task,
            status="failed",
            error=f"finalize_failed:{exc}",
            source_size=source_size,
            target_bitrate_kbps=target_bitrate,
            source_codec=source_codec,
            source_bitrate_kbps=source_bitrate,
        )
    stat = task.target_path.stat()
    sc_data = SidecarData(
        metadata_id=task.metadata_id,
        path=str(task.target_path),
        size=stat.st_size,
        mtime=stat.st_mtime,
        hash_partial=hash_partial(task.target_path),
    )
    write_sidecar(task.target_path, sc_data)
    _copy_companion_files(task.source_path, task.target_path)
    return ConversionResult(
        task=task,
        status="converted",
        source_size=source_size,
        target_size=stat.st_size,
        target_bitrate_kbps=target_bitrate,
        source_codec=source_codec,
        source_bitrate_kbps=source_bitrate,
    )


def _probe_source_profile(audio_path: Path) -> tuple[str, int]:
    """Return (codec_name, bitrate_kbps) from ffprobe when available."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=codec_name,bit_rate",
        "-of",
        "default=noprint_wrappers=1:nokey=0",
        str(audio_path),
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        return ("unknown", 0)

    if proc.returncode != 0:
        return ("unknown", 0)

    codec_name = "unknown"
    bitrate_kbps = 0
    for line in proc.stdout.splitlines():
        if line.startswith("codec_name="):
            codec_name = line.partition("=")[2].strip().lower() or "unknown"
        elif line.startswith("bit_rate="):
            raw = line.partition("=")[2].strip()
            try:
                bitrate_kbps = int(round(int(raw) / 1000))
            except (TypeError, ValueError):
                bitrate_kbps = 0
    return (codec_name, max(0, bitrate_kbps))


def _choose_target_bitrate(
    *,
    source_codec: str,
    source_bitrate_kbps: int,
    max_bitrate_kbps: int,
    scaling: bool,
) -> int:
    """Choose Opus bitrate from source profile with max cap.

    Scaling heuristic:
    - lossy source (mp3/aac/m4a/ogg/wma): Opus target ~= 75% of source bitrate
    - unknown or lossless source: use max bitrate
    """
    capped_max = max(32, int(max_bitrate_kbps))
    if not scaling:
        return capped_max

    lossy_codecs = {"mp3", "aac", "vorbis", "wmav2", "wmav1", "opus"}
    m4a_codecs = {"alac", "aac", "mp4a"}
    codec = source_codec.lower()
    if source_bitrate_kbps <= 0:
        return capped_max

    if codec in lossy_codecs or codec in m4a_codecs:
        scaled = int(round(source_bitrate_kbps * 0.75))
    else:
        # Lossless or unknown quality class gets the configured max.
        scaled = capped_max

    # Keep practical Opus floor and 8 kbps steps for cleaner output.
    scaled = max(64, min(capped_max, scaled))
    return int(round(scaled / 8.0) * 8)


def _copy_companion_files(source_audio: Path, target_audio: Path) -> None:
    """Copy common sidecars that match stem, excluding audio and dlpmeta."""
    stem = source_audio.stem
    src_dir = source_audio.parent
    for candidate in src_dir.glob(f"{stem}.*"):
        if candidate.resolve() == source_audio.resolve():
            continue
        suffix = candidate.suffix.lower()
        if suffix in _AUDIO_EXTS or suffix == ".dlpmeta":
            continue
        dst = target_audio.with_suffix(candidate.suffix)
        if not dst.exists():
            shutil.copy2(candidate, dst)


def _common_parent(paths: list[Path]) -> Path:
    if not paths:
        raise ValueError("paths cannot be empty")
    common = Path(os.path.commonpath([str(p.parent) for p in paths]))
    return common


def _path_under_target(source_path: Path, target_root: Path) -> Path:
    """Map an absolute source path under target_root, preserving drive path."""
    drive = source_path.drive.replace(":", "")
    tail = source_path.as_posix().lstrip("/")
    if drive and tail.lower().startswith(drive.lower() + "/"):
        return target_root / tail
    if drive:
        return target_root / drive / tail
    return target_root / tail


def _sync_db_paths_for_tasks(
    conn: sqlite3.Connection,
    tasks: list[ConversionTask],
) -> int:
    """Update DB paths to Opus targets for tasks whose targets exist."""
    updated = 0
    for task in tasks:
        if not task.target_path.exists():
            continue
        target_full = str(task.target_path.resolve())
        row = conn.execute(
            "SELECT full_path FROM tracks WHERE metadata_id = ?",
            (task.metadata_id,),
        ).fetchone()
        if row is None:
            continue
        if row["full_path"] == target_full:
            continue

        conn.execute(
            """
            UPDATE tracks
            SET file_path = ?, filename = ?, full_path = ?, updated_at = datetime('now')
            WHERE metadata_id = ?
            """,
            (
                str(task.target_path.parent.resolve()) + os.sep,
                task.target_path.name,
                target_full,
                task.metadata_id,
            ),
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO track_sidecars (metadata_id, sidecar_path, synced_at)
            VALUES (?, ?, datetime('now'))
            """,
            (task.metadata_id, str(sidecar_path_for(task.target_path))),
        )
        updated += 1

    conn.commit()
    return updated


def regenerate_opus_sidecars(
    conn: sqlite3.Connection,
    *,
    limit: int = 800,
    newest_first: bool = True,
    dry_run: bool = False,
) -> SidecarRepairSummary:
    """Rebuild Opus sidecars from sibling sidecars sharing the same basename."""
    summary = SidecarRepairSummary()
    order = "DESC" if newest_first else "ASC"
    rows = conn.execute(
        f"""
        SELECT metadata_id, full_path
        FROM tracks
        WHERE filename LIKE '%.opus'
        ORDER BY updated_at {order}
        LIMIT ?
        """,
        (max(1, limit),),
    ).fetchall()

    for row in rows:
        metadata_id = row["metadata_id"]
        opus_path = Path(row["full_path"]).resolve()
        if not opus_path.exists():
            summary.read_errors += 1
            continue

        source_sidecar = _pick_sibling_source_sidecar(opus_path)
        if source_sidecar is None:
            summary.missing_source_sidecar += 1
            continue

        try:
            source_payload = json.loads(source_sidecar.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            summary.read_errors += 1
            continue

        try:
            stat = opus_path.stat()
            merged = dict(source_payload)
            merged["id"] = metadata_id
            merged["path"] = str(opus_path)
            merged["size"] = stat.st_size
            merged["mtime"] = stat.st_mtime
            merged["hash_partial"] = hash_partial(opus_path)
            merged["v"] = int(merged.get("v", 1) or 1)
            if not merged.get("created_at"):
                merged["created_at"] = _utc_iso()
            merged["updated_at"] = _utc_iso()
            if dry_run:
                summary.repaired += 1
                continue

            _write_sidecar_payload_atomic(sidecar_path_for(opus_path), merged)
            conn.execute(
                """
                INSERT OR REPLACE INTO track_sidecars
                    (metadata_id, sidecar_path, hash_partial, hash_full, fingerprint, sidecar_version, synced_at)
                VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                (
                    metadata_id,
                    str(sidecar_path_for(opus_path)),
                    merged.get("hash_partial"),
                    merged.get("hash_full"),
                    merged.get("fp"),
                    int(merged.get("v", 1) or 1),
                ),
            )
            conn.commit()
            summary.repaired += 1
        except OSError:
            summary.write_errors += 1

    return summary


def reconcile_db_to_opus(
    conn: sqlite3.Connection,
    *,
    target_root: Path | None = None,
) -> ReconcileSummary:
    """Set DB full_path to matching .opus files where they exist."""
    summary = ReconcileSummary()
    root = target_root.resolve() if target_root is not None else None
    rows = conn.execute(
        "SELECT metadata_id, full_path FROM tracks ORDER BY full_path"
    ).fetchall()

    for row in rows:
        metadata_id = row["metadata_id"]
        current = Path(row["full_path"]).resolve()
        if current.suffix.lower() == ".opus":
            summary.already_opus += 1
            continue

        candidate = current.with_suffix(".opus")
        if not candidate.exists():
            summary.missing_opus += 1
            continue
        if root is not None:
            try:
                candidate.resolve().relative_to(root)
            except ValueError:
                summary.missing_opus += 1
                continue

        conn.execute(
            """
            UPDATE tracks
            SET file_path = ?, filename = ?, full_path = ?, updated_at = datetime('now')
            WHERE metadata_id = ?
            """,
            (
                str(candidate.parent.resolve()) + os.sep,
                candidate.name,
                str(candidate.resolve()),
                metadata_id,
            ),
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO track_sidecars (metadata_id, sidecar_path, synced_at)
            VALUES (?, ?, datetime('now'))
            """,
            (metadata_id, str(sidecar_path_for(candidate))),
        )
        summary.updated += 1

    conn.commit()
    return summary


def _pick_sibling_source_sidecar(opus_path: Path) -> Path | None:
    stem = opus_path.stem
    sidecar_name = f"{opus_path.name}{SIDECAR_EXT}"
    candidates = [
        p for p in opus_path.parent.glob(f"{stem}.*{SIDECAR_EXT}")
        if p.name != sidecar_name
    ]
    if not candidates:
        return None

    priority = {
        ".flac": 0,
        ".wav": 1,
        ".m4a": 2,
        ".aac": 3,
        ".mp3": 4,
        ".ogg": 5,
        ".wma": 6,
    }

    def key(path: Path) -> tuple[int, str]:
        # "track.mp3.dlpmeta" -> ".mp3"
        orig_suffix = Path(path.name.removesuffix(SIDECAR_EXT)).suffix.lower()
        return (priority.get(orig_suffix, 99), path.name.lower())

    return sorted(candidates, key=key)[0]


def _write_sidecar_payload_atomic(sidecar_path: Path, payload: dict) -> None:
    tmp = sidecar_path.with_name(sidecar_path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, sidecar_path)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
