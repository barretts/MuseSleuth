"""Investigate the problematic track with extra quote."""
import sys
sys.path.insert(0, 'src')

from musesleuth.db import get_connection

conn = get_connection('music_new.db')

# Find the problematic track
row = conn.execute("""
    SELECT metadata_id, title, artist, file_path, filename, full_path
    FROM tracks 
    WHERE title LIKE '%Awesome%' OR full_path LIKE '6/15/2013%'
""").fetchone()

if row:
    print("Problematic track:")
    print(f"  Title: {repr(row['title'])}")
    print(f"  Artist: {repr(row['artist'])}")
    print(f"  file_path: {repr(row['file_path'])}")
    print(f"  filename: {repr(row['filename'])}")
    print(f"  full_path: {repr(row['full_path'])}")

# Now let's find this row in the CSV
print("\n" + "=" * 80)
print("Looking for this row in the CSV...")

with open('mp3tag1.csv', 'r', encoding='utf-16') as f:
    lines = f.readlines()

for i, line in enumerate(lines):
    if 'Awesome' in line or '6/15/2013' in line:
        print(f"\nLine {i+1}:")
        print(repr(line[:400]))
        print()

conn.close()