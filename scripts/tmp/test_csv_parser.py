"""Test CSV parser with mp3tag1.csv."""
import sys
sys.path.insert(0, 'src')

from musesleuth.csv_parser import parse_csv_file

rows = parse_csv_file('mp3tag1.csv')
print(f'Total rows parsed: {len(rows)}')
print(f'Columns: {list(rows[0].keys()) if rows else "none"}')
print()
print('First 3 rows:')
for i, row in enumerate(rows[:3]):
    print(f'Row {i+1}:')
    for k, v in row.items():
        if v:
            print(f'  {k}: {v[:80] if len(v) > 80 else v}')
    print()