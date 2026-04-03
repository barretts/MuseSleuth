"""Parser for mp3tag CSV exports (comma or semicolon delimited)."""
from __future__ import annotations

import csv
import io
from pathlib import Path


def parse_csv(
    content: str,
    *,
    deduplicate: bool = False,
) -> list[dict[str, str]]:
    """Parse CSV content into a list of row dicts.

    Auto-detects delimiter (comma vs semicolon). Handles quoted values
    properly using Python's csv module.
    """
    lines = [line for line in content.splitlines() if line.strip()]
    if not lines:
        return []

    # Auto-detect delimiter by checking first line
    header_line = lines[0]
    if '","' in header_line or header_line.startswith('"'):
        # Comma-delimited with quotes
        delimiter = ","
    elif ";" in header_line:
        # Semicolon-delimited
        delimiter = ";"
    else:
        # Default to comma
        delimiter = ","

    reader = csv.DictReader(
        io.StringIO(content),
        delimiter=delimiter,
        skipinitialspace=True,
    )

    rows: list[dict[str, str]] = []
    seen: set[str] = set()

    for row in reader:
        # Strip whitespace from keys and values (handle list values from duplicate columns)
        cleaned_row = {}
        for k, v in row.items():
            key = k.strip() if k else k
            if isinstance(v, list):
                # Join duplicate column values with separator
                val = "; ".join(str(item) for item in v).strip()
            elif v:
                val = v.strip()
            else:
                val = v
            cleaned_row[key] = val

        if deduplicate:
            dedup_key = cleaned_row.get("Path", "") + cleaned_row.get("Filename", "")
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

        rows.append(cleaned_row)

    return rows


def parse_csv_file(
    path: Path,
    *,
    deduplicate: bool = False,
    encoding: str = "utf-8",
) -> list[dict[str, str]]:
    """Read and parse a CSV file from disk.

    Raises FileNotFoundError if the file does not exist.
    Falls back to utf-8-sig and latin-1 on decode errors.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"CSV file not found: {path}")

    for enc in [encoding, "utf-8-sig", "utf-16", "latin-1"]:
        try:
            content = path.read_text(encoding=enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise UnicodeDecodeError(
            encoding, b"", 0, 1, f"Failed to decode {path} with any encoding"
        )

    return parse_csv(content, deduplicate=deduplicate)


def _split_row(line: str) -> list[str]:
    """Split a semicolon-delimited row, stripping trailing empty field from trailing semicolon."""
    parts = line.split(";")
    # mp3tag CSV lines end with ; producing an empty trailing element
    if parts and parts[-1].strip() == "":
        parts = parts[:-1]
    return parts
