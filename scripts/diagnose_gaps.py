"""Diagnose data gaps for analyzed tracks."""
from musesleuth.db import get_connection

conn = get_connection("music.db")

# Get all analyzed track IDs
analyzed = conn.execute(
    "SELECT j.metadata_id FROM jobs j WHERE j.stage='analyze' AND j.status='done'"
).fetchall()
mids = [r["metadata_id"] for r in analyzed]
print(f"=== {len(mids)} analyzed tracks ===\n")

for mid in mids:
    t = conn.execute(
        "SELECT title, artist, full_path FROM tracks WHERE metadata_id=?", (mid,)
    ).fetchone()
    print(f"[{mid}] {t['artist']} - {t['title']}")
    print(f"  path: {t['full_path']}")

    # Musical features
    mf = conn.execute(
        "SELECT bpm_final, bpm_aubio, bpm_librosa, bpm_confidence, bpm_disagreement, "
        "key_name, key_mode, energy FROM musical_features WHERE metadata_id=?", (mid,)
    ).fetchone()
    if mf:
        print(f"  BPM: aubio={mf['bpm_aubio']} librosa={mf['bpm_librosa']} final={mf['bpm_final']} conf={mf['bpm_confidence']}")
        print(f"  Key: {mf['key_name']} {mf['key_mode']}")
        print(f"  Energy: {mf['energy']}")
        if mf["bpm_final"] is None:
            print("  *** MISSING BPM ***")
        if mf["energy"] is None:
            print("  *** MISSING ENERGY ***")
    else:
        print("  *** NO musical_features row ***")

    # Sidecar
    sc = conn.execute(
        "SELECT hash_partial, hash_full, fingerprint FROM track_sidecars WHERE metadata_id=?", (mid,)
    ).fetchone()
    if sc:
        print(f"  Hash: partial={'YES' if sc['hash_partial'] else 'NO'} full={'YES' if sc['hash_full'] else 'NO'}")
        print(f"  Fingerprint: {'YES' if sc['fingerprint'] else 'NO'}")
    else:
        print("  *** NO sidecar row ***")

    # Job statuses
    jobs = conn.execute(
        "SELECT stage, status, last_error FROM jobs WHERE metadata_id=? ORDER BY id", (mid,)
    ).fetchall()
    statuses = []
    for j in jobs:
        s = f"{j['stage']}={j['status']}"
        if j["last_error"]:
            s += f" ERR:{j['last_error'][:60]}"
        statuses.append(s)
    print(f"  Jobs: {', '.join(statuses)}")

    # Playlist signals
    ps = conn.execute(
        "SELECT * FROM playlist_signals WHERE metadata_id=?", (mid,)
    ).fetchone()
    print(f"  playlist_signals: {'EXISTS' if ps else 'MISSING'}")

    # External IDs
    ext = conn.execute(
        "SELECT * FROM external_ids WHERE metadata_id=?", (mid,)
    ).fetchone()
    print(f"  external_ids: {'EXISTS' if ext else 'MISSING'}")
    print()

# Summary: check schema for energy column
print("=== Schema check ===")
cols = conn.execute("PRAGMA table_info(musical_features)").fetchall()
col_names = [c["name"] for c in cols]
print(f"musical_features columns: {col_names}")

cols2 = conn.execute("PRAGMA table_info(playlist_signals)").fetchall()
col_names2 = [c["name"] for c in cols2]
print(f"playlist_signals columns: {col_names2}")

conn.close()
