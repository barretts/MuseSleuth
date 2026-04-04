# quarantine_from_db.py
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from pathlib import Path

LOSSY_EXTS = {".mp3", ".m4a", ".aac", ".ogg", ".wma"}
LOSSLESS_EXTS = {".flac", ".wav"}
AUDIO_EXTS = LOSSY_EXTS | LOSSLESS_EXTS | {".opus"}


def quarantine_dest(quarantine_root: Path, source_file: Path) -> Path:
    # Preserve full path structure (including drive letter) under quarantine root.
    drive = source_file.drive.replace(":", "") if source_file.drive else "no_drive"
    try:
        rel_tail = source_file.resolve().relative_to(Path(source_file.anchor))
    except Exception:
        rel_tail = Path(source_file.name)
    rel = Path(drive) / rel_tail
    return quarantine_root / rel


def move_if_needed(src: Path, quarantine_root: Path, dry_run: bool) -> bool:
    if not src.exists() or not src.is_file():
        return False
    dst = quarantine_dest(quarantine_root, src)
    if dst.resolve() == src.resolve():
        raise RuntimeError(f"Refusing to move onto itself: {src}")
    if dry_run:
        _safe_print(f"WOULD_MOVE: {src} -> {dst}")
        return True
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return True


def _safe_print(line: str) -> None:
    # Avoid cp1252 console crashes on Unicode filenames.
    try:
        print(line)
    except UnicodeEncodeError:
        encoded = line.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(
            sys.stdout.encoding or "utf-8",
            errors="replace",
        )
        print(encoded)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, help="Path to music_new.db")
    parser.add_argument("--quarantine-root", required=True, help="Where old files are moved")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    db_path = Path(args.db).resolve()
    quarantine_root = Path(args.quarantine_root).resolve()

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    rows = conn.execute("SELECT full_path FROM tracks ORDER BY full_path").fetchall()
    conn.close()

    moved = 0
    considered = 0
    missing = 0

    for row in rows:
        p = Path(row["full_path"])
        ext = p.suffix.lower()
        if ext not in AUDIO_EXTS:
            continue

        considered += 1

        # Case A: DB already points to .opus -> quarantine any old sibling formats.
        if ext == ".opus":
            for old_ext in (LOSSY_EXTS | LOSSLESS_EXTS):
                old_file = p.with_suffix(old_ext)
                if old_file.exists():
                    if move_if_needed(old_file, quarantine_root, args.dry_run):
                        moved += 1
            continue

        # Case B: DB still points to non-opus -> only quarantine if .opus sibling exists.
        opus_file = p.with_suffix(".opus")
        if opus_file.exists():
            if move_if_needed(p, quarantine_root, args.dry_run):
                moved += 1
        else:
            missing += 1

    _safe_print(f"Considered DB tracks: {considered}")
    _safe_print(f"Moved (or would move): {moved}")
    _safe_print(f"Skipped (no .opus sibling for non-opus DB paths): {missing}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())