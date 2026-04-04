"""Debug script to investigate CSV parsing issues causing corrupted full_path values."""
import sys
sys.path.insert(0, 'src')

from musesleuth.csv_parser import parse_csv_file
from musesleuth.db import get_connection

# First, let's look at the raw CSV content to understand the format
print("=" * 80)
print("RAW CSV ANALYSIS")
print("=" * 80)

# Try multiple encodings for BOM
for enc in ['utf-8-sig', 'utf-8', 'utf-16', 'latin-1']:
    try:
        with open('mp3tag.csv', 'r', encoding=enc) as f:
            lines = f.readlines()
        print(f"Successfully read CSV with encoding: {enc}")
        break
    except UnicodeDecodeError:
        continue
else:
    print("ERROR: Could not decode CSV with any encoding")
    sys.exit(1)

print(f"Total lines in CSV: {len(lines)}")
print(f"\nFirst 3 lines (raw):")
for i, line in enumerate(lines[:3]):
    print(f"  Line {i+1}: {repr(line[:200])}...")

# Parse with the CSV parser
print("\n" + "=" * 80)
print("PARSED CSV ANALYSIS")
print("=" * 80)

rows = parse_csv_file('mp3tag.csv')
print(f"Total rows parsed: {len(rows)}")

# Check what columns we have
if rows:
    print(f"\nColumns: {list(rows[0].keys())}")
    
# Look for rows with problematic full_path
print("\n" + "=" * 80)
print("PROBLEMATIC ROWS")
print("=" * 80)

conn = get_connection('music.db')
problematic = conn.execute("""
    SELECT metadata_id, title, artist, album, file_path, filename, full_path
    FROM tracks 
    WHERE full_path LIKE '%MB%' OR full_path LIKE '%/20/%' OR full_path LIKE '%\201%'
    LIMIT 10
""").fetchall()

print(f"Found {len(problematic)} tracks with corrupted full_path")
for row in problematic:
    print(f"\n  Title: {row['title']}")
    print(f"  Artist: {row['artist']}")
    print(f"  file_path: {row['file_path'][:100] if row['file_path'] else 'None'}")
    print(f"  filename: {row['filename'][:100] if row['filename'] else 'None'}")
    print(f"  full_path: {row['full_path'][:150]}")

# Now let's check what the CSV parser gives us for these same tracks
print("\n" + "=" * 80)
print("CSV PARSER OUTPUT FOR FIRST 5 ROWS")
print("=" * 80)

for i, row in enumerate(rows[:5]):
    print(f"\nRow {i+1}:")
    for key, value in row.items():
        if value:  # Only show non-empty values
            print(f"  {key}: {value[:80] if len(value) > 80 else value}")

conn.close()