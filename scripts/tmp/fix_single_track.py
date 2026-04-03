"""Fix the single corrupted track caused by unescaped quotes in CSV."""
import sys
sys.path.insert(0, 'src')

from musesleuth.db import get_connection

conn = get_connection('music_new.db')

# The correct values based on the filename
correct_path = 'D:\\Music\\Imported\\'
correct_filename = "Don't Go - Awesome, Awesome.mp3"
correct_title = "Don't Go - Awesome, Awesome"
correct_artist = "Various Artists"

# Find and fix the corrupted track
row = conn.execute("""
    SELECT metadata_id, full_path
    FROM tracks 
    WHERE full_path LIKE '6/15/2013%' AND full_path NOT LIKE 'D:%'
""").fetchone()

if row:
    mid = row['metadata_id']
    print(f"Fixing track {mid}")
    print(f"  Old full_path: {row['full_path']}")
    
    conn.execute("""
        UPDATE tracks 
        SET file_path = ?, filename = ?, full_path = ?, title = ?, artist = ?
        WHERE metadata_id = ?
    """, (correct_path, correct_filename, correct_path + correct_filename, 
          correct_title, correct_artist, mid))
    conn.commit()
    print(f"  New full_path: {correct_path + correct_filename}")
    print("  Fixed!")
else:
    print("No corrupted track found (already fixed or never imported)")

conn.close()