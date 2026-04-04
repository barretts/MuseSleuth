"""Debug script to investigate analyze step failures."""
import sys
sys.path.insert(0, 'src')

from musesleuth.db import get_connection

conn = get_connection('music.db')

# Check all failed jobs by stage
print("=" * 80)
print("FAILED JOBS BY STAGE")
print("=" * 80)

stages = conn.execute("""
    SELECT stage, COUNT(*) as cnt, 
           GROUP_CONCAT(DISTINCT substr(last_error, 1, 50)) as sample_errors
    FROM jobs 
    WHERE status = 'failed'
    GROUP BY stage
    ORDER BY cnt DESC
""").fetchall()

for row in stages:
    print(f"  {row['stage']}: {row['cnt']} failures")
    if row['sample_errors']:
        print(f"    Sample errors: {row['sample_errors'][:200]}")
    print()

# Check for tracks with corrupted paths
print("=" * 80)
print("TRACKS WITH CORRUPTED PATHS")
print("=" * 80)

corrupted = conn.execute("""
    SELECT t.metadata_id, t.title, t.artist, t.file_path, t.filename, t.full_path,
           j.stage as failed_stage, j.last_error
    FROM tracks t
    LEFT JOIN jobs j ON t.metadata_id = j.metadata_id AND j.status = 'failed'
    WHERE t.file_path NOT LIKE 'G:\\%' 
      AND t.file_path NOT LIKE 'D:\\%'
      AND t.file_path NOT LIKE 'C:\\%'
      AND t.file_path NOT LIKE '\\\\%'
      AND t.file_path != ''
""").fetchall()

print(f"Found {len(corrupted)} tracks with corrupted file_path")
for row in corrupted:
    print(f"\n  Title: {row['title']}")
    print(f"  Artist: {row['artist']}")
    print(f"  file_path: {repr(row['file_path'])[:100]}")
    print(f"  filename: {repr(row['filename'])[:100]}")
    print(f"  full_path: {repr(row['full_path'])[:150]}")
    if row['failed_stage']:
        print(f"  Failed at stage: {row['failed_stage']}")
        print(f"  Error: {row['last_error'][:100] if row['last_error'] else 'N/A'}")

# Check tracks that exist on disk
print("\n" + "=" * 80)
print("CHECKING IF FILES EXIST ON DISK")
print("=" * 80)

from pathlib import Path

sample_tracks = conn.execute("""
    SELECT metadata_id, full_path, title
    FROM tracks 
    WHERE full_path LIKE 'G:\\dlp\\%' 
    LIMIT 10
""").fetchall()

exists_count = 0
missing_count = 0
for track in sample_tracks:
    path = Path(track['full_path'])
    if path.exists():
        exists_count += 1
    else:
        missing_count += 1
        print(f"  MISSING: {track['title'][:50]}")
        print(f"    Path: {track['full_path'][:100]}")

print(f"\nSample check: {exists_count} exist, {missing_count} missing (out of {len(sample_tracks)} sampled)")

conn.close()