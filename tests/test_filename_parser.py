"""Tests for filename-based metadata extraction."""
from __future__ import annotations

import pytest

from musesleuth.filename_parser import apply_filename_fallback, extract_base_title, parse_filename


class TestParseFilename:
    """Tests for parse_filename extraction logic."""

    @pytest.mark.unit
    def test_artist_dash_title(self) -> None:
        result = parse_filename("2 Damn Tuff - Ruff Muff.mp3")
        assert result["artist"] == "2 Damn Tuff"
        assert result["title"] == "Ruff Muff"

    @pytest.mark.unit
    def test_artist_dash_title_with_remix(self) -> None:
        result = parse_filename("Vinylgroover - Flow (Rave Mix).mp3")
        assert result["artist"] == "Vinylgroover"
        assert result["title"] == "Flow (Rave Mix)"

    @pytest.mark.unit
    def test_artist_dash_title_featuring(self) -> None:
        result = parse_filename("Todd Terry feat. Tara Macdonald - Play On (Eddie Thoneick Remix).mp3")
        assert result["artist"] == "Todd Terry feat. Tara Macdonald"
        assert result["title"] == "Play On (Eddie Thoneick Remix)"

    @pytest.mark.unit
    def test_unknown_artist_suffix(self) -> None:
        result = parse_filename("A Dreams Suprise (Unknown Artist).mp3")
        assert result["artist"] == ""
        assert result["title"] == "A Dreams Suprise"

    @pytest.mark.unit
    def test_track_number_prefix_bracket(self) -> None:
        result = parse_filename("[01] infinite beat.mp3")
        assert result["artist"] == ""
        assert result["title"] == "infinite beat"

    @pytest.mark.unit
    def test_track_number_prefix_plain(self) -> None:
        result = parse_filename("01 - Some Track.mp3")
        assert result["artist"] == ""
        assert result["title"] == "Some Track"

    @pytest.mark.unit
    def test_track_number_with_artist_title(self) -> None:
        result = parse_filename("03 - Artist Name - Song Title.mp3")
        assert result["artist"] == "Artist Name"
        assert result["title"] == "Song Title"

    @pytest.mark.unit
    def test_no_separator_plain_title(self) -> None:
        result = parse_filename("Age Of Stomp.mp3")
        assert result["artist"] == ""
        assert result["title"] == "Age Of Stomp"

    @pytest.mark.unit
    def test_strips_extension(self) -> None:
        result = parse_filename("Artist - Title.flac")
        assert result["title"] == "Title"

    @pytest.mark.unit
    def test_full_windows_path_in_filename(self) -> None:
        result = parse_filename("G:\\dlp\\music\\Artist - Song.mp3")
        assert result["artist"] == "Artist"
        assert result["title"] == "Song"

    @pytest.mark.unit
    def test_empty_filename_returns_empty(self) -> None:
        result = parse_filename("")
        assert result["artist"] == ""
        assert result["title"] == ""

    @pytest.mark.unit
    def test_multiple_dashes_splits_on_first(self) -> None:
        result = parse_filename("Tom Wax Joins Jamx & De Leon - Laut & Leise (Dj Jamx & De Leon Mix)(German Vs).mp3")
        assert result["artist"] == "Tom Wax Joins Jamx & De Leon"
        assert result["title"] == "Laut & Leise (Dj Jamx & De Leon Mix)(German Vs)"

    @pytest.mark.unit
    def test_parenthesized_track_number(self) -> None:
        result = parse_filename("(02) bliss.mp3")
        assert result["title"] == "bliss"


class TestExtractBaseTitle:
    """Tests for remix/mix parenthetical stripping."""

    @pytest.mark.unit
    def test_original_mix(self) -> None:
        assert extract_base_title("Sandstorm (Original Mix)") == "Sandstorm"

    @pytest.mark.unit
    def test_extended_mix(self) -> None:
        assert extract_base_title("Sandstorm (Extended Mix)") == "Sandstorm"

    @pytest.mark.unit
    def test_club_mix(self) -> None:
        assert extract_base_title("Flow (Club Mix)") == "Flow"

    @pytest.mark.unit
    def test_radio_edit(self) -> None:
        assert extract_base_title("Levels (Radio Edit)") == "Levels"

    @pytest.mark.unit
    def test_named_remix(self) -> None:
        assert extract_base_title("Play On (Eddie Thoneick Remix)") == "Play On"

    @pytest.mark.unit
    def test_named_extended_remix(self) -> None:
        assert extract_base_title("Song (DJ X Extended Remix)") == "Song"

    @pytest.mark.unit
    def test_vip_mix(self) -> None:
        assert extract_base_title("Anthem (VIP Mix)") == "Anthem"

    @pytest.mark.unit
    def test_dub(self) -> None:
        assert extract_base_title("Track (Dub)") == "Track"

    @pytest.mark.unit
    def test_rework(self) -> None:
        assert extract_base_title("Classic (2024 Rework)") == "Classic"

    @pytest.mark.unit
    def test_bootleg(self) -> None:
        assert extract_base_title("Hit Song (DJ Z Bootleg)") == "Hit Song"

    @pytest.mark.unit
    def test_instrumental(self) -> None:
        assert extract_base_title("Vocal Track (Instrumental)") == "Vocal Track"

    @pytest.mark.unit
    def test_vocal_mix(self) -> None:
        assert extract_base_title("Deep Blue (Vocal Mix)") == "Deep Blue"

    @pytest.mark.unit
    def test_rave_mix(self) -> None:
        assert extract_base_title("Flow (Rave Mix)") == "Flow"

    @pytest.mark.unit
    def test_dash_remix_suffix(self) -> None:
        assert extract_base_title("Song - DJ Name Remix") == "Song"

    @pytest.mark.unit
    def test_no_remix_info_unchanged(self) -> None:
        assert extract_base_title("Just A Title") == "Just A Title"

    @pytest.mark.unit
    def test_non_remix_parens_preserved(self) -> None:
        assert extract_base_title("Song (feat. Artist)") == "Song (feat. Artist)"

    @pytest.mark.unit
    def test_empty_string(self) -> None:
        assert extract_base_title("") == ""

    @pytest.mark.unit
    def test_acapella(self) -> None:
        assert extract_base_title("Song (Acapella)") == "Song"

    @pytest.mark.unit
    def test_version(self) -> None:
        assert extract_base_title("Track (Album Version)") == "Track"


class TestApplyFilenameFallback:
    """Tests for apply_filename_fallback row mutation."""

    @pytest.mark.unit
    def test_fills_empty_title_and_artist(self) -> None:
        row = {"Title": "", "Artist": "", "Filename": "Vinylgroover - Hotstepper.mp3"}
        apply_filename_fallback(row)
        assert row["Title"] == "Hotstepper"
        assert row["Artist"] == "Vinylgroover"

    @pytest.mark.unit
    def test_does_not_overwrite_existing_title(self) -> None:
        row = {"Title": "Real Title", "Artist": "", "Filename": "Wrong - Also Wrong.mp3"}
        apply_filename_fallback(row)
        assert row["Title"] == "Real Title"
        assert row["Artist"] == "Wrong"

    @pytest.mark.unit
    def test_does_not_overwrite_existing_artist(self) -> None:
        row = {"Title": "", "Artist": "Real Artist", "Filename": "Wrong - Also Wrong.mp3"}
        apply_filename_fallback(row)
        assert row["Title"] == "Also Wrong"
        assert row["Artist"] == "Real Artist"

    @pytest.mark.unit
    def test_no_op_when_both_present(self) -> None:
        row = {"Title": "T", "Artist": "A", "Filename": "X - Y.mp3"}
        apply_filename_fallback(row)
        assert row["Title"] == "T"
        assert row["Artist"] == "A"

    @pytest.mark.unit
    def test_whitespace_only_treated_as_empty(self) -> None:
        row = {"Title": "  ", "Artist": " ", "Filename": "Artist - Title.mp3"}
        apply_filename_fallback(row)
        assert row["Title"] == "Title"
        assert row["Artist"] == "Artist"

    @pytest.mark.unit
    def test_no_filename_returns_unchanged(self) -> None:
        row = {"Title": "", "Artist": "", "Filename": ""}
        apply_filename_fallback(row)
        assert row["Title"] == ""
        assert row["Artist"] == ""

    @pytest.mark.unit
    def test_unknown_artist_not_assigned(self) -> None:
        row = {"Title": "", "Artist": "", "Filename": "My Song (Unknown Artist).mp3"}
        apply_filename_fallback(row)
        assert row["Title"] == "My Song"
        assert row["Artist"] == ""

    @pytest.mark.unit
    def test_returns_row_reference(self) -> None:
        row = {"Title": "", "Artist": "", "Filename": "A - B.mp3"}
        result = apply_filename_fallback(row)
        assert result is row
