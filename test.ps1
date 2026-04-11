# 1) Only clear stale running -> pending (does NOT touch done)
# 1) Only clear stale running -> pending (does NOT touch done)
$code = @"
import sqlite3
c = sqlite3.connect(r'Y:\music.db')
n = c.execute("UPDATE jobs SET status='pending', worker_id=NULL, claimed_at=NULL, updated_at=datetime('now') WHERE status='running'").rowcount
c.commit()
c.close()
print('reset', n, 'running->pending')
"@
python -c $code
# 2) Seed loudness jobs if missing, then run loudness
musesleuth backfill-jobs --db Y:\music.db --stage loudness
musesleuth run --db Y:\music.db --stage loudness --workers 16

# 3) Continue only upstream unfinished stages (no writeback)
musesleuth run --db Y:\music.db --stage probe --workers 16
musesleuth run --db Y:\music.db --stage analyze --workers 16
musesleuth run --db Y:\music.db --stage fingerprint_match --workers 16
musesleuth run --db Y:\music.db --stage enrich
musesleuth run --db Y:\music.db --stage derive_signals --workers 16

# optional only if you want ml classify completed too
musesleuth run --db Y:\music.db --stage ml_classify --workers 16

# check
python -m musesleuth dashboard --db Y:\music.db