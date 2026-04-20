"""Remove cover / tribute / 8-bit tracks from Subsonic playlists in place.

Uses the same hard artist blocklist and soft regex as
:mod:`musesleuth.prefer_official`, applied directly to what Subsonic
reports for each playlist entry. Does *not* touch local MuseSleuth
playlists - it only mutates Subsonic via ``updatePlaylist.view``.

Typical flow::

    python scripts/scrub_subsonic_covers.py --dry-run --verbose
    python scripts/scrub_subsonic_covers.py --exclude-name "Vibes seed"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from musesleuth.prefer_official import (  # noqa: E402
    has_soft_cover_signal,
    is_hard_blocked,
)
from musesleuth.subsonic import (  # noqa: E402
    SubsonicClient,
    load_subsonic_settings_from_env,
)


def _entry_is_cover(entry: dict) -> tuple[bool, str]:
    """Return ``(drop, reason)`` for a Subsonic playlist entry."""
    artist = str(entry.get("artist") or "").strip()
    title = str(entry.get("title") or "").strip()
    if is_hard_blocked(artist):
        return True, f"hard-blocklist artist '{artist}'"
    if has_soft_cover_signal(artist, title):
        return True, "soft pattern match"
    return False, ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--exclude-name",
        action="append",
        default=[],
        help="Playlist name to skip (case-insensitive, repeatable).",
    )
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--verbose",
        action="store_true",
        help="Print artist - title for each dropped entry.",
    )
    args = ap.parse_args()

    excluded = {n.strip().lower() for n in args.exclude_name}

    try:
        client = SubsonicClient.from_settings(load_subsonic_settings_from_env())
    except RuntimeError as exc:
        print(f"Subsonic settings error: {exc}", file=sys.stderr)
        return 1

    playlists = client.list_playlists()
    print(f"Discovered {len(playlists)} Subsonic playlist(s)")

    grand_scanned = 0
    grand_dropped = 0
    touched = 0

    for pl in playlists:
        pl_id = str(pl.get("id"))
        pl_name = str(pl.get("name") or "")
        if pl_name.strip().lower() in excluded:
            print(f"SKIP  sub={pl_id:>4}  {pl_name}  (excluded)")
            continue

        entries = client.get_playlist_entries(pl_id)
        scanned = len(entries)
        grand_scanned += scanned
        if scanned == 0:
            print(f"EMPTY sub={pl_id:>4}  {pl_name}")
            continue

        to_remove: list[tuple[int, str, str, str]] = []
        for idx, entry in enumerate(entries):
            drop, reason = _entry_is_cover(entry)
            if drop:
                to_remove.append(
                    (
                        idx,
                        str(entry.get("artist") or ""),
                        str(entry.get("title") or ""),
                        reason,
                    )
                )

        dropped = len(to_remove)
        grand_dropped += dropped

        status = "KEEP " if dropped == 0 else "TRIM "
        print(
            f"{status} sub={pl_id:>4}  {pl_name[:40]:40s}  "
            f"{scanned:>4} -> {scanned - dropped:>4}  (-{dropped})"
        )
        if args.verbose:
            for _, artist, title, reason in to_remove:
                print(f"        - {artist} - {title}   [{reason}]")

        if dropped == 0 or args.dry_run:
            continue

        client.update_playlist_remove_indices(
            pl_id, [idx for idx, *_ in to_remove]
        )
        touched += 1

    suffix = " (dry-run, no writes)" if args.dry_run else ""
    print()
    print(
        f"Summary: {len(playlists)} playlists scanned, "
        f"{touched} modified, "
        f"{grand_dropped} / {grand_scanned} entries dropped{suffix}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
