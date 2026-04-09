"""Database package — re-exports for backward compatibility.

Usage unchanged:
    from musesleuth.db import get_connection, create_schema, TABLE_NAMES
"""
from musesleuth.db.schema import SCHEMA_SQL as _SCHEMA_SQL, TABLE_NAMES
from musesleuth.db.connection import generate_metadata_id, get_connection, create_schema
from musesleuth.db.migrate import _migrate, _add_column
from musesleuth.db.search import (
    tokenize,
    index_track,
    rebuild_search_index,
    search_tracks,
)

__all__ = [
    "_SCHEMA_SQL",
    "TABLE_NAMES",
    "generate_metadata_id",
    "get_connection",
    "create_schema",
    "_migrate",
    "_add_column",
    "tokenize",
    "index_track",
    "rebuild_search_index",
    "search_tracks",
]
