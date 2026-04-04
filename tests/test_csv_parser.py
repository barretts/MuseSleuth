"""TDD tests for CSV parser -- written before implementation."""
from __future__ import annotations

from pathlib import Path

import pytest

from musesleuth.csv_parser import parse_csv, parse_csv_file


class TestParseCsv:
    """Tests for parsing semicolon-delimited mp3tag CSV data."""

    @pytest.mark.unit
    def test_parses_two_rows(self, sample_csv_content: str) -> None:
        rows = parse_csv(sample_csv_content)
        assert len(rows) == 2

    @pytest.mark.unit
    def test_field_names(self, sample_csv_content: str) -> None:
        rows = parse_csv(sample_csv_content)
        expected_fields = {
            "Title", "Artist", "Album", "Track", "Year",
            "Length", "Size", "Last Modified", "Path", "Filename",
        }
        assert set(rows[0].keys()) == expected_fields

    @pytest.mark.unit
    def test_first_row_values(self, sample_csv_content: str) -> None:
        rows = parse_csv(sample_csv_content)
        assert rows[0]["Title"] == "Into The Inner Space (Trance Radio Edit)"
        assert rows[0]["Artist"] == "!Attention!"
        assert rows[0]["Year"] == "2000"

    @pytest.mark.unit
    def test_reconstructs_full_path(self, sample_csv_content: str) -> None:
        rows = parse_csv(sample_csv_content)
        row = rows[0]
        full_path = row["Path"] + row["Filename"]
        assert full_path.endswith(".mp3")

    @pytest.mark.unit
    def test_handles_trailing_semicolon(self) -> None:
        """mp3tag CSV has a trailing semicolon on each line."""
        content = "Title;Artist;\nSong;Bob;\n"
        rows = parse_csv(content)
        assert len(rows) == 1
        assert rows[0]["Title"] == "Song"
        assert rows[0]["Artist"] == "Bob"

    @pytest.mark.unit
    def test_handles_empty_fields(self) -> None:
        content = "Title;Artist;Year;\n;Unknown;;\n"
        rows = parse_csv(content)
        assert rows[0]["Title"] == ""
        assert rows[0]["Artist"] == "Unknown"
        assert rows[0]["Year"] == ""

    @pytest.mark.unit
    def test_skips_blank_lines(self) -> None:
        content = "Title;Artist;\nSong1;A;\n\nSong2;B;\n"
        rows = parse_csv(content)
        assert len(rows) == 2

    @pytest.mark.unit
    def test_handles_utf8_characters(self) -> None:
        content = "Title;Artist;\nCafe\u0301;Bj\u00f6rk;\n"
        rows = parse_csv(content)
        assert rows[0]["Artist"] == "Bj\u00f6rk"

    @pytest.mark.unit
    def test_deduplicates_by_path_and_filename(self) -> None:
        content = (
            "Title;Artist;Path;Filename;\n"
            "Song;A;C:\\music\\;file.mp3;\n"
            "Song;A;C:\\music\\;file.mp3;\n"
            "Song2;B;C:\\music\\;file2.mp3;\n"
        )
        rows = parse_csv(content, deduplicate=True)
        assert len(rows) == 2

    @pytest.mark.unit
    def test_no_dedup_when_disabled(self) -> None:
        content = (
            "Title;Artist;Path;Filename;\n"
            "Song;A;C:\\music\\;file.mp3;\n"
            "Song;A;C:\\music\\;file.mp3;\n"
        )
        rows = parse_csv(content, deduplicate=False)
        assert len(rows) == 2


class TestParseCsvFile:
    """Tests for file-based CSV parsing."""

    @pytest.mark.integration
    def test_reads_from_file(self, sample_csv_file: Path) -> None:
        rows = parse_csv_file(sample_csv_file)
        assert len(rows) == 2
        assert rows[0]["Title"] == "Into The Inner Space (Trance Radio Edit)"

    @pytest.mark.integration
    def test_raises_on_missing_file(self, tmp_dir: Path) -> None:
        with pytest.raises(FileNotFoundError):
            parse_csv_file(tmp_dir / "nonexistent.csv")
