"""Check analyze stage results for recently analyzed tracks."""
from musesleuth.db import get_connection

conn = get_connection("music.db")

# Get tracks with analyze done
rows = conn.execute("""
    SELECT t.title, t.artist,
           mf.bpm_aubio, mf.bpm_librosa, mf.bpm_final, mf.bpm_confidence,
           mf.bpm_disagreement, mf.key_name, mf.key_mode,
           ts.hash_partial, ts.hash_full, ts.fingerprint
    FROM tracks t
    JOIN musical_features mf ON t.metadata_id = mf.metadata_id
    LEFT JOIN track_sidecars ts ON t.metadata_id = ts.metadata_id
    LIMIT 5
""").fetchall()

for r in rows:
    print(f"{r['artist']} - {r['title']}")
    print(f"  BPM: aubio={r['bpm_aubio']}, librosa={r['bpm_librosa']}, final={r['bpm_final']}")
    print(f"  Confidence: {r['bpm_confidence']}, disagreement={r['bpm_disagreement']}")
    print(f"  Key: {r['key_name']} {r['key_mode']}")
    print(f"  Hash: partial={r['hash_partial'][:16] if r['hash_partial'] else 'N/A'}...")
    print(f"  Fingerprint: {'yes' if r['fingerprint'] else 'no'}")
    print()

# Counts
mf_count = conn.execute("SELECT COUNT(*) as c FROM musical_features").fetchone()["c"]
fp_count = conn.execute("SELECT COUNT(*) as c FROM track_sidecars WHERE fingerprint IS NOT NULL").fetchone()["c"]
print(f"Musical features rows: {mf_count}")
print(f"Tracks with fingerprints: {fp_count}")

conn.close()
