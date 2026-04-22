"""Query Y:\\music.db and Z:\\music_new.db and list all songs that are NOT
album release tracks - i.e. exclude demos, remixes, live, covers, etc.

Keep: official tracks, remasters, vinyl releases
Exclude: demos, remixes, live tracks, covers (incl. Kids Bop), karaoke,
         8-bit, tribute, instrumental versions, acoustic versions
"""
import csv
import re
import sqlite3
from pathlib import Path

# ── Databases ────────────────────────────────────────────────────────────
DB_Y = r"Y:\music.db"
DB_Z = r"Z:\music_new.db"
OUTPUT = Path(r"c:\code\MuseSleuth\scripts\tmp\official_tracks.csv")

# ── Hard artist blocklist (from prefer_official.py) ──────────────────────
HARD_ARTIST_BLOCKLIST = {
    "8-bit arcade", "8 bit arcade", "8 bit universe", "8-bit universe",
    "vitamin string quartet", "rockabye baby!", "rockabye baby",
    "the karaoke channel", "karaoke version", "karaoke - ameritz",
    "ameritz karaoke", "the hit crew", "kidz bop kids", "kidz bop",
    "made famous by", "cover band",
    "twinkle twinkle little rock star",
    "the string quartet tribute", "rock n roll baby",
}

# ── KEEP patterns (remaster/vinyl override — always official) ────────────
KEEP_RES = [
    re.compile(r"\bremaster(?:ed)?\b", re.I),
    re.compile(r"\bvinyl\b", re.I),
    re.compile(r"\boriginal\s+(?:mix|version)\b", re.I),
    re.compile(r"\balbum\s+version\b", re.I),
]

# ── EXCLUDE patterns — each returns a reason string or None ──────────────
def _check_exclude(title: str, artist: str) -> str | None:
    """Return exclusion reason or None if the track passes."""

    combined = f"{artist} {title}"

    # ── Hard keywords that almost always mean non-official ──
    hard_keywords = [
        (r"\bremix\b", "remix"),
        (r"\bdemo\b", "demo"),
        (r"\bkaraoke\b", "karaoke"),
        (r"\b8[- ]?bit\b", "8-bit"),
        (r"\bkids?\s*bop\b", "kidz bop"),
        (r"\bbootleg\b", "bootleg"),
        (r"\bmash[- ]?up\b", "mashup"),
        (r"\brework\b", "rework"),
        (r"\brefix\b", "refix"),
        (r"\bmade famous by\b", "made famous by"),
        (r"\bas made famous by\b", "as made famous by"),
        (r"\bin the style of\b", "in the style of"),
        (r"\binstrumental(?:\s+version)?\b", "instrumental"),
        (r"\btribute\s+to\b", "tribute"),
        (r"\blullaby(?:\s+rendition|\s+version|\s+tribute)?\b", "lullaby"),
    ]
    for pat, label in hard_keywords:
        if re.search(pat, title, re.I):
            return f"excluded: {label}"

    # ── Live tracks: "(Live)", "- Live", "Live at/in/from" ──
    if re.search(r"\blive\s+(?:at|@|in|from|on)\b", title, re.I):
        return "excluded: live"
    if re.search(r"\(\s*live\b", title, re.I):
        return "excluded: live (parens)"

    # ── Acoustic ──
    if re.search(r"\bacoustic(?:\s+(?:version|mix|session|recording))?\b", title, re.I):
        # But keep if it's the actual album title (e.g. "MTV Unplugged")
        return "excluded: acoustic"

    # ── Cover ──
    if re.search(r"\bcover\b", title, re.I):
        return "excluded: cover"

    # ── Mix / Edit (inside parentheses — high confidence) ──
    # e.g. "(Club Mix)", "(Extended Mix)", "(Radio Edit)", "(Dub Mix)"
    paren_content = re.findall(r"\(([^)]+)\)", title)
    for p in paren_content:
        p_lower = p.lower()
        if re.search(r"\b(?:club|extended|dub|vip)\s+mix\b", p_lower):
            return f"excluded: mix ({p.strip()})"
        if re.search(r"\b(?:radio|club)\s+edit\b", p_lower):
            return f"excluded: edit ({p.strip()})"

    # ── Soft cover signals (from prefer_official.py) ──
    soft_pat = re.compile(
        r"\b(8[- ]?bit|karaoke|instrumental version|in the style of"
        r"|lullaby rendition|lullaby version|lullaby tribute"
        r"|tribute to|made famous by|as made famous by)\b", re.I)
    m = soft_pat.search(combined)
    if m:
        return f"soft cover: {m.group()}"

    return None


def is_official_track(title: str | None, artist: str | None,
                      filename: str | None) -> tuple[bool, str]:
    """Return (is_official, reason)."""
    artist_clean = (artist or "").strip()
    title_clean = (title or "").strip()
    artist_lower = artist_clean.lower()

    # Hard artist blocklist
    if artist_lower in HARD_ARTIST_BLOCKLIST:
        return False, f"blocked artist: {artist_clean}"

    # Keep overrides (remaster, vinyl, original mix/version, album version)
    for pat in KEEP_RES:
        if pat.search(title_clean):
            return True, "keep: remaster/vinyl/original"

    # Exclude check
    reason = _check_exclude(title_clean, artist_clean)
    if reason:
        return False, reason

    return True, "official"


def query_db(db_path: str) -> list[dict]:
    """Query all tracks from a database."""
    print(f"  Connecting to {db_path} ...")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    cols = [r[1] for r in conn.execute("PRAGMA table_info(tracks)").fetchall()]
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]

    select_parts = [
        "t.metadata_id", "t.title", "t.artist", "t.album",
        "t.track_number", "t.year", "t.length_seconds", "t.file_size",
        "t.full_path", "t.filename",
    ]
    for col in ("genre", "album_artist", "label", "original_year"):
        if col in cols:
            select_parts.append(f"t.{col}")

    if "track_stats" in tables:
        select_parts.append("COALESCE(ts.listener_count, 0) AS listener_count")
        query = (
            f"SELECT {', '.join(select_parts)} "
            f"FROM tracks t "
            f"LEFT JOIN track_stats ts "
            f"  ON ts.metadata_id = t.metadata_id AND ts.source = 'lastfm'"
        )
    else:
        select_parts.append("0 AS listener_count")
        query = f"SELECT {', '.join(select_parts)} FROM tracks t"

    print(f"  Executing query ...")
    rows = conn.execute(query).fetchall()
    conn.close()
    print(f"  Got {len(rows)} tracks")
    return [dict(r) for r in rows]


def main():
    print("=== Finding official album tracks ===\n")

    all_tracks: list[dict] = []

    print("[1/2] Querying Y:\\music.db ...")
    if Path(DB_Y).exists():
        all_tracks.extend(query_db(DB_Y))
    else:
        print(f"  WARNING: {DB_Y} not found, skipping")

    print(f"\n[2/2] Querying Z:\\music_new.db ...")
    if Path(DB_Z).exists():
        all_tracks.extend(query_db(DB_Z))
    else:
        print(f"  WARNING: {DB_Z} not found, skipping")

    print(f"\n  Total tracks across both DBs: {len(all_tracks)}")

    # ── Filter ───────────────────────────────────────────────────────────
    official: list[dict] = []
    excluded: list[dict] = []
    for t in all_tracks:
        is_off, reason = is_official_track(
            t.get("title"), t.get("artist"), t.get("filename"))
        t["_is_official"] = is_off
        t["_reason"] = reason
        (official if is_off else excluded).append(t)

    print(f"  Official tracks: {len(official)}")
    print(f"  Excluded tracks: {len(excluded)}")

    # ── Exclusion breakdown ──────────────────────────────────────────────
    reason_counts: dict[str, int] = {}
    for t in excluded:
        r = t["_reason"]
        # Collapse to category
        cat = r.split(":")[0] if ":" in r else r
        reason_counts[cat] = reason_counts.get(cat, 0) + 1
    print(f"\n  Exclusion breakdown:")
    for reason, count in sorted(reason_counts.items(), key=lambda x: -x[1]):
        print(f"    {count:>6}  {reason}")

    # ── De-duplicate by full_path ────────────────────────────────────────
    seen_paths: set[str] = set()
    deduped_official: list[dict] = []
    for t in official:
        fp = t.get("full_path", "")
        if fp not in seen_paths:
            seen_paths.add(fp)
            deduped_official.append(t)

    print(f"\n  After dedup by path: {len(deduped_official)} official tracks")

    # ── Write official CSV ───────────────────────────────────────────────
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "metadata_id", "title", "artist", "album", "track_number",
        "year", "length_seconds", "file_size", "full_path", "filename",
        "genre", "album_artist", "label", "original_year", "listener_count",
        "source_db",
    ]
    with open(OUTPUT, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for t in deduped_official:
            fp = t.get("full_path", "")
            t["source_db"] = (
                "music_new (Z:)" if fp.startswith(("E:\\", "Z:\\"))
                else "music (Y:)"
            )
            writer.writerow(t)

    print(f"\n  Official CSV written to: {OUTPUT}")
    print(f"  ({len(deduped_official)} rows)")

    # ── Write excluded CSV for review ────────────────────────────────────
    excluded_output = OUTPUT.with_name("excluded_tracks.csv")
    excl_fieldnames = [
        "metadata_id", "title", "artist", "album", "track_number",
        "year", "length_seconds", "full_path", "filename",
        "genre", "album_artist", "label", "original_year", "listener_count",
        "source_db", "_reason",
    ]
    with open(excluded_output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=excl_fieldnames, extrasaction="ignore")
        writer.writeheader()
        for t in excluded:
            fp = t.get("full_path", "")
            t["source_db"] = (
                "music_new (Z:)" if fp.startswith(("E:\\", "Z:\\"))
                else "music (Y:)"
            )
            writer.writerow(t)

    print(f"  Excluded CSV written to: {excluded_output}")
    print(f"  ({len(excluded)} rows)")


if __name__ == "__main__":
    main()