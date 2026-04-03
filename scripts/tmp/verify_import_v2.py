"""Verify the new import has clean data - more precise checks."""
import sys
sys.path.insert(0, 'src')

from musesleuth.db import get_connection

conn = get_connection('music_new.db')

# Check for actual corrupted full_path values (file_path is a date or size, not a path)
print("Checking for corrupted full_path values (file_path looks like date/size)...")
print("=" * 80)

# file_path should start with a drive letter or UNC path, not a date or size
problematic = conn.execute("""
    SELECT metadata_id, title, artist, file_path, filename, full_path
    FROM tracks 
    WHERE file_path NOT LIKE 'G:\\%' 
      AND file_path NOT LIKE 'D:\\%'
      AND file_path NOT LIKE 'C:\\%'
      AND file_path NOT LIKE '\\\\%'
      AND file_path != ''
    LIMIT 20
""").fetchall()

print(f"Found {len(problematic)} tracks with corrupted file_path (doesn't start with drive letter)")
for row in problematic:
    print(f"  Title: {row['title']}")
    print(f"  file_path: {repr(row['file_path'])[:100]}")
    print(f"  filename: {repr(row['filename'])[:100]}")
    print(f"  full_path: {repr(row['full_path'])[:150]}")
    print()

# Check total tracks
total = conn.execute("SELECT COUNT(*) as cnt FROM tracks").fetchone()['cnt']
print(f"Total tracks in database: {total}")

# Check tracks with .mp3 extension in full_path (sanity check)
mp3_count = conn.execute("SELECT COUNT(*) as cnt FROM tracks WHERE full_path LIKE '%.mp3'").fetchone()['cnt']
print(f"Tracks with .mp3 extension: {mp3_count}")

# Check tracks with drive letter
drive_count = conn.execute("SELECT COUNT(*) as cnt FROM tracks WHERE full_path LIKE 'G:\\%'").fetchone()['cnt']
print(f"Tracks with G: drive: {drive_count}")

conn.close()