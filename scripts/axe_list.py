import sqlite3, re, csv, collections
from pathlib import Path

DB = "music_new.db"
OUT = "axe_list.csv"

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
rows = conn.execute("""
SELECT
  t.metadata_id, t.title, t.artist, t.full_path, t.filename, t.length_seconds, t.file_size,
  tf.bitrate, tf.integrity_ok, tf.lowpass_cutoff_hz, tf.silence_ratio, tf.clipping_ratio
FROM tracks t
LEFT JOIN technical_features tf ON tf.metadata_id = t.metadata_id
""").fetchall()

ws = re.compile(r"\s+")
remix_tag = re.compile(r"[\(\[][^\)\]]*(remix|mix|edit|version|dub|vip|bootleg|rework|live)[^\)\]]*[\)\]]", re.I)

def norm(s):
    s = (s or "").casefold().strip()
    return ws.sub(" ", s)

def core_title(t):
    t = norm(t)
    t = remix_tag.sub("", t)
    return ws.sub(" ", t).strip(" -_")

def as_int(x):
    try:
        return int(float(x))
    except Exception:
        return 0

def as_float(x):
    try:
        return float(x)
    except Exception:
        return None

axe = []

# Tier 1: exact dupes by artist+title; keep best, axe rest
by_exact = collections.defaultdict(list)
for r in rows:
    k = (norm(r["artist"]), norm(r["title"]))
    if k[0] and k[1]:
        by_exact[k].append(r)

for k, grp in by_exact.items():
    if len(grp) <= 1:
        continue
    ranked = sorted(
        grp,
        key=lambda r: (
            1 if r["integrity_ok"] == 1 else 0,
            as_int(r["bitrate"]),
            as_int(r["file_size"]),
        ),
        reverse=True,
    )
    keep = ranked[0]["metadata_id"]
    for drop in ranked[1:]:
        axe.append({
            "metadata_id": drop["metadata_id"],
            "tier": "safe_auto",
            "reason": "exact_duplicate_artist_title",
            "keep_metadata_id": keep,
            "artist": drop["artist"] or "",
            "title": drop["title"] or "",
            "filename": drop["filename"] or "",
            "bitrate": as_int(drop["bitrate"]),
            "integrity_ok": drop["integrity_ok"] if drop["integrity_ok"] is not None else "",
            "full_path": drop["full_path"] or "",
        })

# Tier 2: likely low-value/problem files
for r in rows:
    secs = as_float(r["length_seconds"])
    bitrate = as_int(r["bitrate"])
    bad_integrity = (r["integrity_ok"] == 0)
    lowpass_cutoff = as_float(r["lowpass_cutoff_hz"])
    silence_ratio = as_float(r["silence_ratio"])
    clipping_ratio = as_float(r["clipping_ratio"])

    reasons = []
    if secs is not None and secs < 48:
        reasons.append("very_short_lt48s")
    if bad_integrity and bitrate and bitrate < 160000:
        reasons.append("integrity_bad_and_low_bitrate")
    if lowpass_cutoff is not None and lowpass_cutoff < 16000 and bitrate and bitrate <= 192000:
        reasons.append("suspected_lowpass_transcode")
    if silence_ratio is not None and silence_ratio >= 0.35:
        reasons.append("silence_heavy")
    if clipping_ratio is not None and clipping_ratio >= 0.01:
        reasons.append("clipping_detected")

    if reasons:
        axe.append({
            "metadata_id": r["metadata_id"],
            "tier": "review_fast",
            "reason": "|".join(reasons),
            "keep_metadata_id": "",
            "artist": r["artist"] or "",
            "title": r["title"] or "",
            "filename": r["filename"] or "",
            "bitrate": bitrate,
            "integrity_ok": r["integrity_ok"] if r["integrity_ok"] is not None else "",
            "full_path": r["full_path"] or "",
        })

# Tier 3: remix overflow by artist+core_title (keep best 2)
by_core = collections.defaultdict(list)
for r in rows:
    k = (norm(r["artist"]), core_title(r["title"]))
    if k[0] and k[1]:
        by_core[k].append(r)

for k, grp in by_core.items():
    if len(grp) <= 2:
        continue
    ranked = sorted(
        grp,
        key=lambda r: (
            1 if r["integrity_ok"] == 1 else 0,
            as_int(r["bitrate"]),
            as_int(r["file_size"]),
        ),
        reverse=True,
    )
    keep_ids = [ranked[0]["metadata_id"], ranked[1]["metadata_id"]]
    for drop in ranked[2:]:
        axe.append({
            "metadata_id": drop["metadata_id"],
            "tier": "review_curate",
            "reason": "remix_family_overflow_keep2",
            "keep_metadata_id": ",".join(keep_ids),
            "artist": drop["artist"] or "",
            "title": drop["title"] or "",
            "filename": drop["filename"] or "",
            "bitrate": as_int(drop["bitrate"]),
            "integrity_ok": drop["integrity_ok"] if drop["integrity_ok"] is not None else "",
            "full_path": drop["full_path"] or "",
        })

# dedupe by metadata_id with tier priority
priority = {"safe_auto": 0, "review_fast": 1, "review_curate": 2}
best = {}
for row in axe:
    mid = row["metadata_id"]
    if mid not in best or priority[row["tier"]] < priority[best[mid]["tier"]]:
        best[mid] = row

final = list(best.values())
final.sort(key=lambda r: (r["tier"], (r["artist"] or "").casefold(), (r["title"] or "").casefold()))

with open(OUT, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=[
        "metadata_id","tier","reason","keep_metadata_id",
        "artist","title","filename","bitrate","integrity_ok","full_path"
    ])
    w.writeheader()
    w.writerows(final)

print(f"Wrote {OUT} with {len(final)} candidates")