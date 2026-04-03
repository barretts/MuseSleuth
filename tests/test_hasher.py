"""TDD tests for BLAKE3 file hashing -- written before implementation."""
from __future__ import annotations

from pathlib import Path

import pytest

from musesleuth.hasher import hash_partial, hash_full, CHUNK_SIZE


class TestHashPartial:
    """Tests for partial file hashing (first + last 64KB)."""

    @pytest.mark.unit
    def test_returns_hex_string(self, tmp_path: Path) -> None:
        f = tmp_path / "test.bin"
        f.write_bytes(b"\x00" * 200_000)
        result = hash_partial(f)
        assert isinstance(result, str)
        assert len(result) == 64  # BLAKE3 hex digest

    @pytest.mark.unit
    def test_deterministic(self, tmp_path: Path) -> None:
        f = tmp_path / "test.bin"
        f.write_bytes(b"hello world" * 10000)
        h1 = hash_partial(f)
        h2 = hash_partial(f)
        assert h1 == h2

    @pytest.mark.unit
    def test_different_content_different_hash(self, tmp_path: Path) -> None:
        f1 = tmp_path / "a.bin"
        f2 = tmp_path / "b.bin"
        f1.write_bytes(b"\x00" * 200_000)
        f2.write_bytes(b"\xff" * 200_000)
        assert hash_partial(f1) != hash_partial(f2)

    @pytest.mark.unit
    def test_small_file_still_works(self, tmp_path: Path) -> None:
        """Files smaller than 2*CHUNK_SIZE should still hash correctly."""
        f = tmp_path / "tiny.bin"
        f.write_bytes(b"tiny file content")
        result = hash_partial(f)
        assert isinstance(result, str)
        assert len(result) == 64

    @pytest.mark.unit
    def test_raises_on_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            hash_partial(tmp_path / "nope.bin")


class TestHashFull:
    """Tests for full file hashing."""

    @pytest.mark.unit
    def test_returns_hex_string(self, tmp_path: Path) -> None:
        f = tmp_path / "test.bin"
        f.write_bytes(b"hello world")
        result = hash_full(f)
        assert isinstance(result, str)
        assert len(result) == 64

    @pytest.mark.unit
    def test_deterministic(self, tmp_path: Path) -> None:
        f = tmp_path / "test.bin"
        f.write_bytes(b"deterministic content")
        assert hash_full(f) == hash_full(f)

    @pytest.mark.unit
    def test_matches_known_value(self, tmp_path: Path) -> None:
        """Verify against a known BLAKE3 hash of empty bytes."""
        import blake3
        f = tmp_path / "empty.bin"
        f.write_bytes(b"")
        expected = blake3.blake3(b"").hexdigest()
        assert hash_full(f) == expected

    @pytest.mark.unit
    def test_full_and_partial_differ_for_large_files(self, tmp_path: Path) -> None:
        """For files larger than 2*CHUNK_SIZE, partial and full should differ."""
        f = tmp_path / "large.bin"
        data = bytes(range(256)) * 1000  # 256KB
        f.write_bytes(data)
        assert hash_partial(f) != hash_full(f)
