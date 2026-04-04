"""BLAKE3-based file hashing for track identity."""
from __future__ import annotations

from pathlib import Path

import blake3 as _blake3


CHUNK_SIZE = 65_536  # 64 KB


def hash_partial(path: Path) -> str:
    """Compute a BLAKE3 hash of the first and last CHUNK_SIZE bytes of a file.

    For files smaller than 2*CHUNK_SIZE, hashes the entire file.
    Returns the hex digest string.
    Raises FileNotFoundError if the file does not exist.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    file_size = path.stat().st_size
    hasher = _blake3.blake3()

    with open(path, "rb") as f:
        if file_size <= 2 * CHUNK_SIZE:
            hasher.update(f.read())
        else:
            # First chunk
            hasher.update(f.read(CHUNK_SIZE))
            # Last chunk
            f.seek(-CHUNK_SIZE, 2)
            hasher.update(f.read(CHUNK_SIZE))

    return hasher.hexdigest()


def hash_full(path: Path) -> str:
    """Compute a BLAKE3 hash of the entire file contents.

    Returns the hex digest string.
    Raises FileNotFoundError if the file does not exist.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    hasher = _blake3.blake3()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                break
            hasher.update(chunk)

    return hasher.hexdigest()