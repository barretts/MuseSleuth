"""Verify the new import has clean data."""
import sys
sys.path.insert(0, 'src')

from musesleuth.db import get_connection

conn = get_connection('music_new.db')

# Check for corrupted full_path values
print("Checking for corrupted full_path values...")
print("=" * 80)

# Look for paths that look like dates or sizes
problematic = conn.execute("""
    SELECT metadata_id, title, artist, file_path, filename, full_path
    FROM tracks 
    WHERE full_path LIKE '%MB%' 
       OR full_path LIKE '3/29/2026%' 
       OR full_path LIKE '4/23/2013%'
       OR full_path LIKE '5/27/2013%'
       OR full_path LIKE '6/19/2013%'
    LIMIT 20
""").fetchall()

print(f"Found {len(problematic)} potentially corrupted tracks")
for row in problematic:
    print(f"  Title: {row['title']}")
    print(f"  Path: {repr(row['file_path'])[:100]}")
    print(f"  Filename: {repr(row['filename'])[:100]}")
    print(f"  Full Path: {repr(row['full_path'])[:150]}")
    print()

# Check total tracks
total = conn.execute("SELECT COUNT(*) as cnt FROM tracks").fetchone()['cnt']
print(f"Total tracks in database: {total}")

# Check a few random clean rows
print("\n" + "=" * 80)
print("Sample of clean rows:")
print("=" * 80)
clean = conn.execute("""
    SELECT metadata_id, title, artist, full_path
    FROM tracks 
    WHERE full_path LIKE 'G:\\dlp\\%' AND full_path NOT LIKE '%MB%'
    LIMIT 5
""").fetchall()

for row in clean:
    print(f"  {row['title']} - {row['artist']}")
    print(f"    Path: {row['full_path'][:100]}")
    print()

conn.close()