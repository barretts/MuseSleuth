"""Show detailed failure information for debugging."""
import sys
sys.path.insert(0, 'src')

from musesleuth.db import get_connection

conn = get_connection('music.db')

# Get the actual failed jobs with full details
rows = conn.execute('''
SELECT j.metadata_id, j.last_error, j.attempts, j.stage,
       t.title, t.artist, t.file_path, t.filename, t.full_path
FROM jobs j 
JOIN tracks t ON j.metadata_id = t.metadata_id 
WHERE j.status = 'failed'
''').fetchall()

print(f'Total failed jobs: {len(rows)}')
print()
for r in rows:
    print(f'Stage: {r["stage"]}')
    print(f'Title: {r["title"]}')
    print(f'file_path: {repr(r["file_path"])[:100]}')
    print(f'filename: {repr(r["filename"])[:100]}')
    print(f'full_path: {repr(r["full_path"])[:150]}')
    print(f'Error: {r["last_error"]}')
    print(f'Attempts: {r["attempts"]}')
    print('-' * 80)

conn.close()