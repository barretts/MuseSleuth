"""Extract artist and title metadata from audio filenames."""
from __future__ import annotations

import re
from pathlib import PurePosixPath, PureWindowsPath


def parse_filename(filename: str) -> dict[str, str]:
    """Extract artist and title from a filename string.

    Handles common patterns:
      - "Artist - Title.mp3"
      - "Artist - Title (Remix Info).mp3"
      - "[01] Title.mp3"  (track-number prefix, no artist)
      - "Title (Unknown Artist).mp3"

    Returns a dict with 'artist' and 'title' keys (possibly empty strings).
    """
    # Strip directory components -- handle both Windows and POSIX paths
    try:
        stem = PureWindowsPath(filename).stem
    except Exception:
        stem = PurePosixPath(filename).stem

    # Remove leading track-number prefixes: "01 - ", "01. ", "[01] ", "(01) "
    # Require brackets/parens or a non-space separator (dot/dash/underscore) after
    # digits so we don't strip digits that are part of an artist name like "2 Damn Tuff".
    stem = re.sub(r"^(?:\[\d{1,3}\]|\(\d{1,3}\)|\d{1,3}[.\-_])[\s.\-_]*", "", stem)

    artist = ""
    title = ""

    # Pattern 1: "Artist - Title" (most common, ~93% of tagged files)
    if " - " in stem:
        parts = stem.split(" - ", 1)
        left = parts[0].strip()
        right = parts[1].strip()

        if left.isdigit():
            # "01 - Title" or "03 - Artist - Title": leading digits are a track number
            if " - " in right:
                sub = right.split(" - ", 1)
                artist = sub[0].strip()
                title = sub[1].strip()
            else:
                title = right
        else:
            artist = left
            title = right
    else:
        # Pattern 2: "Title (Unknown Artist)" -- treat whole stem as title
        title = stem.strip()

    # Strip "(Unknown Artist)" suffix from title if present
    title = re.sub(r"\s*\(Unknown Artist\)\s*$", "", title, flags=re.IGNORECASE)

    return {"artist": artist, "title": title}


_REMIX_SUFFIX_RE = re.compile(
    r"\s*\([^)]*\b(?:"
    r"remix|re-mix|rmx|mix|edit|dub|rework|bootleg|version|"
    r"instrumental|acapella|a\s*cappella|"
    r"radio\s*edit|extended(?:\s*mix)?|club(?:\s*mix)?|original(?:\s*mix)?|"
    r"vip(?:\s*mix)?|vocal(?:\s*mix)?"
    r")\b[^)]*\)\s*$",
    re.IGNORECASE,
)

_REMIX_DASH_RE = re.compile(
    r"\s+-\s+\S.*\b(?:remix|rmx|re-mix|edit|dub|rework|bootleg|mix)\s*$",
    re.IGNORECASE,
)


def extract_base_title(title: str) -> str:
    """Strip remix/mix/edit parenthetical to get the underlying composition name.

    Examples:
        "Flow (Rave Mix)"                    -> "Flow"
        "Levels (Radio Edit)"                -> "Levels"
        "Play On (Eddie Thoneick Remix)"     -> "Play On"
        "Sandstorm (Extended Mix)"           -> "Sandstorm"
        "Song - DJ Name Remix"               -> "Song"
        "Just A Title"                       -> "Just A Title"

    Titles without recognisable remix info are returned unchanged.
    """
    if not title:
        return title
    stripped = _REMIX_SUFFIX_RE.sub("", title)
    if stripped != title:
        return stripped.strip()
    stripped = _REMIX_DASH_RE.sub("", title)
    return stripped.strip()


def apply_filename_fallback(row: dict[str, str]) -> dict[str, str]:
    """Fill empty Title/Artist fields from filename parsing.

    Only overwrites fields that are empty/whitespace. Returns the
    (potentially mutated) row dict.
    """
    needs_title = not row.get("Title", "").strip()
    needs_artist = not row.get("Artist", "").strip()

    if not (needs_title or needs_artist):
        return row

    filename = row.get("Filename", "")
    if not filename:
        return row

    parsed = parse_filename(filename)

    if needs_title and parsed["title"]:
        row["Title"] = parsed["title"]
    if needs_artist and parsed["artist"]:
        row["Artist"] = parsed["artist"]

    return row
