"""Find rows in CSV where columns are shifted."""
import sys
sys.path.insert(0, 'src')

from musesleuth.csv_parser import parse_csv_file

# Parse and find the problematic rows
rows = parse_csv_file('mp3tag.csv')

# Find rows where file_path looks like a date or size
print("Rows with shifted columns (Path contains date/size):")
print("=" * 80)

for i, row in enumerate(rows):
    fp = row.get('Path', '')
    fn = row.get('Filename', '')
    size = row.get('Size', '')
    date = row.get('Last Modified', '')
    
    # Check if Path starts with a date pattern or size pattern
    if fp and len(fp) > 0:
        # Date pattern: starts with digit and contains /
        if fp[0].isdigit() and '/' in fp[:10]:
            print(f'Row {i+1}: Path={repr(fp)}, Filename={repr(fn)}')
            print(f'  Size={repr(size)}, Date={repr(date)}')
            print(f'  Title: {row.get("Title", "")}')
            print()
        # Size pattern: ends with MB
        elif fp.endswith(' MB'):
            print(f'Row {i+1}: Path={repr(fp)}, Filename={repr(fn)}')
            print(f'  Size={repr(size)}, Date={repr(date)}')
            print(f'  Title: {row.get("Title", "")}')
            print()