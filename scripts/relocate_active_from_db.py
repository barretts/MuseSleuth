from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from pathlib import Path


def _safe_print(line: str) -> None:
    try:
        print(line)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "utf-8"
        print(line.encode(enc, errors="replace").decode(enc, errors="replace"))


def _dest_for(source_file: Path, destination_root: Path) -> Path:
    """Preserve drive + path under destination root."""
    drive = source_file.drive.replace(":", "") if source_file.drive else "no_drive"
    try:
        rel_tail = source_file.resolve().relative_to(Path(source_file.anchor))
    except Exception:
        rel_tail = Path(source_file.name)
    return destination_root / drive / rel_tail


def _move_one(src: Path, dst: Path, dry_run: bool) -> bool:
    if not src.exists() or not src.is_file():
        return False
    if dst.resolve() == src.resolve():
        raise RuntimeError(f"Refusing to move onto itself: {src}")
    if dry_run:
        _safe_print(f"WOULD_MOVE: {src} -> {dst}")
        return True
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Move DB-referenced active files into one destination tree."
    )
    parser.add_argument("--db", required=True, help="Path to music_new.db")
    parser.add_argument(
        "--destination-root",
        required=True,
        help="Root folder where active files are moved",
    )
    parser.add_argument(
        "--include-sidecars",
        action="store_true",
        help="Also move .dlpmeta sidecars for moved files",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    db_path = Path(args.db).resolve()
    destination_root = Path(args.destination_root).resolve()

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT metadata_id, full_path
        FROM tracks
        ORDER BY full_path
        """
    ).fetchall()
    conn.close()

    considered = 0
    moved_files = 0
    moved_sidecars = 0
    missing_files = 0

    for row in rows:
        considered += 1
        src = Path(row["full_path"])
        dst = _dest_for(src, destination_root)
        if _move_one(src, dst, args.dry_run):
            moved_files += 1
        else:
            missing_files += 1

        if args.include_sidecars:
            sidecar_src = Path(str(src) + ".dlpmeta")
            sidecar_dst = Path(str(dst) + ".dlpmeta")
            if _move_one(sidecar_src, sidecar_dst, args.dry_run):
                moved_sidecars += 1

    _safe_print(f"Considered DB tracks: {considered}")
    _safe_print(f"Moved tracks (or would move): {moved_files}")
    _safe_print(f"Moved sidecars (or would move): {moved_sidecars}")
    _safe_print(f"Missing track files on disk: {missing_files}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
