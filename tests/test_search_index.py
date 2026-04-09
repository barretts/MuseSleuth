from __future__ import annotations

import sqlite3

from musesleuth.db import create_schema
from musesleuth.db.search import index_track, search_tracks, tokenize


def test_tokenize_handles_apostrophes() -> None:
    assert tokenize("I'll fly with you") == {"ll", "fly", "with", "you"}


def test_search_tracks_finds_title_with_common_words(in_memory_db: sqlite3.Connection) -> None:
    create_schema(in_memory_db)

    for i in range(5100):
        metadata_id = f"0000{i:04d}"
        in_memory_db.execute(
            """
            INSERT INTO tracks (metadata_id, title, artist, album, file_path, filename, full_path)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                metadata_id,
                f"With You {i}",
                "Noise Artist",
                "Noise Album",
                "C:\\test\\",
                f"noise-{i}.mp3",
                f"C:\\test\\noise-{i}.mp3",
            ),
        )
        index_track(in_memory_db, metadata_id)

    target_id = "zzzz_target"
    in_memory_db.execute(
        """
        INSERT INTO tracks (metadata_id, title, artist, album, file_path, filename, full_path)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            target_id,
            "I'll Fly With You",
            "Gigi D'Agostino",
            "L'Amour Toujours",
            "C:\\test\\",
            "ill-fly-with-you.mp3",
            "C:\\test\\ill-fly-with-you.mp3",
        ),
    )
    index_track(in_memory_db, target_id)
    in_memory_db.commit()

    matches = search_tracks(in_memory_db, "I'll fly with you")

    assert target_id in matches


def test_search_tracks_falls_back_to_near_match_when_exact_tokens_miss(
    in_memory_db: sqlite3.Connection,
) -> None:
    create_schema(in_memory_db)

    metadata_id = "gigi_track"
    in_memory_db.execute(
        """
        INSERT INTO tracks (metadata_id, title, artist, album, file_path, filename, full_path)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            metadata_id,
            "Toujours L'Amour (I'll Fly For You)",
            "Gigi D'Agostino",
            "Clubland 9 CD1",
            "C:\\test\\",
            "gigi.mp3",
            "C:\\test\\gigi.mp3",
        ),
    )
    index_track(in_memory_db, metadata_id)
    in_memory_db.commit()

    matches = search_tracks(in_memory_db, "I'll fly with you")

    assert metadata_id in matches
