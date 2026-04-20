"""Tests for ``scripts/metadata_sidecar.py`` and the signing helpers in ``musesleuth.sidecar``."""
from __future__ import annotations

import base64
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

from musesleuth.db import create_schema, get_connection
from musesleuth.sidecar import (
    SIDECAR_BAK_EXT,
    SIDECAR_EXT,
    SIDECAR_VERSION,
    SIGNATURE_FIELD,
    SidecarData,
    build_dlpmeta_payload,
    sign_payload,
    verify_signature,
)
from tests.conftest import insert_dummy_track

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from metadata_sidecar import (  # noqa: E402
    BLOB_MARKER,
    MSMETA_BAK_EXT,
    MSMETA_EXT,
    MULTI_ROW_TABLES,
    OUTGOING_EDGE_TABLE,
    SCHEMA_VERSION,
    SINGLE_ROW_TABLES,
    TABLE_DISPOSITION,
    apply_path_map,
    backup_db,
    dlpmeta_bak_path_for,
    export_sidecars,
    export_track_metadata,
    import_dlpmeta_from_file,
    import_sidecars,
    import_track_from_sidecar,
    msmeta_path_for,
    parse_path_prefix_maps,
    read_metadata_sidecar,
    schema_coverage_diff,
    write_dlpmeta_sidecar,
    write_metadata_sidecar,
)


SIGNING_KEY = b"super-secret-test-key-0123456789"
OTHER_KEY = b"some-other-key-xxxxxxxxxxxxxxxx"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db_conn(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    create_schema(conn)
    return conn


@pytest.fixture
def audio_file(tmp_path: Path) -> Path:
    audio = tmp_path / "artist" / "song.mp3"
    audio.parent.mkdir(parents=True, exist_ok=True)
    audio.write_bytes(b"\xff\xfb\x90\x00" * 100)
    return audio


def _insert_full_track(
    conn: sqlite3.Connection,
    metadata_id: str,
    audio_path: Path,
) -> None:
    conn.execute(
        """
        INSERT INTO tracks
            (metadata_id, title, artist, album, track_number, year,
             length_seconds, file_size, file_path, filename, full_path,
             genre, album_artist, label)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            metadata_id,
            "Test Song",
            "Test Artist",
            "Test Album",
            "3",
            "2024",
            "240",
            "5000000",
            str(audio_path.parent) + os.sep,
            audio_path.name,
            str(audio_path),
            "Progressive House",
            "Test Artist",
            "Test Label",
        ),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Schema coverage audit
# ---------------------------------------------------------------------------

class TestSchemaCoverage:
    @pytest.mark.unit
    def test_every_schema_table_has_disposition(self) -> None:
        missing, stale = schema_coverage_diff()
        assert not missing, (
            f"Schema has CREATE TABLE for {sorted(missing)} but they are not "
            "listed in TABLE_DISPOSITION. Add them (as 'included-*' or "
            "'skipped-*') so exports stay complete."
        )
        assert not stale, (
            f"TABLE_DISPOSITION references {sorted(stale)} which no longer "
            "exist in schema.py."
        )

    @pytest.mark.unit
    def test_include_lists_match_disposition(self) -> None:
        single_disposed = {
            t for t, d in TABLE_DISPOSITION.items() if d == "included-single"
        }
        multi_disposed = {
            t for t, d in TABLE_DISPOSITION.items() if d == "included-multi"
        }
        edge_disposed = {
            t for t, d in TABLE_DISPOSITION.items() if d == "included-edges"
        }
        assert set(SINGLE_ROW_TABLES) == single_disposed
        assert set(MULTI_ROW_TABLES) == multi_disposed
        assert edge_disposed == {OUTGOING_EDGE_TABLE}


# ---------------------------------------------------------------------------
# Path derivation
# ---------------------------------------------------------------------------

class TestSidecarPaths:
    @pytest.mark.unit
    def test_msmeta_path(self) -> None:
        p = msmeta_path_for(Path("E:/ms/t/a/song.mp3"))
        assert str(p).endswith(MSMETA_EXT)

    @pytest.mark.unit
    def test_msmeta_bak_path(self) -> None:
        p = msmeta_path_for(Path("E:/ms/t/a/song.mp3"), bak=True)
        assert str(p).endswith(MSMETA_BAK_EXT)

    @pytest.mark.unit
    def test_dlpmeta_bak_path(self) -> None:
        p = dlpmeta_bak_path_for(Path("E:/ms/t/a/song.mp3"))
        assert str(p).endswith(SIDECAR_BAK_EXT)


# ---------------------------------------------------------------------------
# Signing helpers
# ---------------------------------------------------------------------------

class TestSigning:
    @pytest.mark.unit
    def test_sign_and_verify_valid(self) -> None:
        payload = {"schema_version": 2, "metadata_id": "01TEST", "tables": {}}
        payload[SIGNATURE_FIELD] = sign_payload(payload, SIGNING_KEY)
        assert verify_signature(payload, SIGNING_KEY) == "valid"

    @pytest.mark.unit
    def test_unsigned_payload(self) -> None:
        payload = {"schema_version": 2, "metadata_id": "01TEST", "tables": {}}
        payload[SIGNATURE_FIELD] = sign_payload(payload, None)
        assert verify_signature(payload, SIGNING_KEY) == "unsigned"

    @pytest.mark.unit
    def test_tamper_detected_via_sha256(self) -> None:
        payload = {"schema_version": 2, "metadata_id": "01TEST", "tables": {"x": 1}}
        payload[SIGNATURE_FIELD] = sign_payload(payload, SIGNING_KEY)
        payload["tables"]["x"] = 2
        assert verify_signature(payload, SIGNING_KEY) == "corrupt"

    @pytest.mark.unit
    def test_key_mismatch_is_unsigned(self) -> None:
        payload = {"schema_version": 2, "metadata_id": "01TEST", "tables": {}}
        payload[SIGNATURE_FIELD] = sign_payload(payload, SIGNING_KEY)
        assert verify_signature(payload, OTHER_KEY) == "unsigned"

    @pytest.mark.unit
    def test_timestamp_change_does_not_invalidate(self) -> None:
        """``exported_at`` / ``updated_at`` are excluded from canonical bytes."""
        payload = {
            "schema_version": 2,
            "metadata_id": "01TEST",
            "exported_at": "2026-01-01T00:00:00Z",
            "tables": {},
        }
        payload[SIGNATURE_FIELD] = sign_payload(payload, SIGNING_KEY)
        payload["exported_at"] = "2099-12-31T23:59:59Z"
        assert verify_signature(payload, SIGNING_KEY) == "valid"


# ---------------------------------------------------------------------------
# .msmeta.json round-trip
# ---------------------------------------------------------------------------

class TestMsmetaRoundTrip:
    @pytest.mark.integration
    def test_basic_round_trip_signed(
        self, db_conn: sqlite3.Connection, audio_file: Path
    ) -> None:
        mid = "01TEST00000000000000000001"
        _insert_full_track(db_conn, mid, audio_file)

        payload = export_track_metadata(db_conn, mid)
        assert payload is not None
        assert payload["schema_version"] == SCHEMA_VERSION

        written, signed = write_metadata_sidecar(audio_file, payload, key=SIGNING_KEY)
        assert signed
        assert str(written).endswith(MSMETA_EXT)
        assert written.exists()

        db_conn.execute(
            "UPDATE tracks SET title = 'MODIFIED' WHERE metadata_id = ?", (mid,)
        )
        db_conn.commit()

        result = import_track_from_sidecar(db_conn, written, key=SIGNING_KEY)
        assert result == "updated"

        row = db_conn.execute(
            "SELECT title FROM tracks WHERE metadata_id = ?", (mid,)
        ).fetchone()
        assert row["title"] == "Test Song"

    @pytest.mark.integration
    def test_unsigned_export_writes_bak(
        self, db_conn: sqlite3.Connection, audio_file: Path
    ) -> None:
        mid = "01TEST00000000000000000002"
        _insert_full_track(db_conn, mid, audio_file)

        payload = export_track_metadata(db_conn, mid)
        written, signed = write_metadata_sidecar(audio_file, payload, key=None)

        assert not signed
        assert str(written).endswith(MSMETA_BAK_EXT)
        assert written.exists()
        assert not msmeta_path_for(audio_file).exists()

    @pytest.mark.integration
    def test_corrupt_payload_refused(
        self, db_conn: sqlite3.Connection, audio_file: Path, tmp_path: Path
    ) -> None:
        mid = "01TEST00000000000000000003"
        _insert_full_track(db_conn, mid, audio_file)

        payload = export_track_metadata(db_conn, mid)
        written, _ = write_metadata_sidecar(audio_file, payload, key=SIGNING_KEY)

        # Tamper with the file directly.
        raw = json.loads(written.read_text(encoding="utf-8"))
        raw["tables"]["tracks"]["title"] = "EVIL"
        written.write_text(json.dumps(raw), encoding="utf-8")

        result = import_track_from_sidecar(db_conn, written, key=SIGNING_KEY)
        assert result == "corrupt"

    @pytest.mark.integration
    def test_unsigned_import_demotes_to_bak(
        self, db_conn: sqlite3.Connection, audio_file: Path
    ) -> None:
        mid = "01TEST00000000000000000004"
        _insert_full_track(db_conn, mid, audio_file)

        payload = export_track_metadata(db_conn, mid)
        # Sign with the wrong key so verification treats the file as unsigned.
        written, _ = write_metadata_sidecar(audio_file, payload, key=OTHER_KEY)
        assert written.exists()
        assert str(written).endswith(MSMETA_EXT)

        db_conn.execute(
            "UPDATE tracks SET title = 'CHANGED' WHERE metadata_id = ?", (mid,)
        )
        db_conn.commit()

        result = import_track_from_sidecar(db_conn, written, key=SIGNING_KEY)
        assert result == "unsigned_updated"

        # Live .msmeta.json got renamed to .bak.
        assert not msmeta_path_for(audio_file).exists()
        assert msmeta_path_for(audio_file, bak=True).exists()

        row = db_conn.execute(
            "SELECT title FROM tracks WHERE metadata_id = ?", (mid,)
        ).fetchone()
        assert row["title"] == "Test Song"

    @pytest.mark.integration
    def test_musical_features_round_trip(
        self, db_conn: sqlite3.Connection, audio_file: Path
    ) -> None:
        mid = "01TEST00000000000000000005"
        _insert_full_track(db_conn, mid, audio_file)
        db_conn.execute(
            "INSERT INTO musical_features (metadata_id, bpm_final, key_name, key_mode, energy) "
            "VALUES (?, ?, ?, ?, ?)",
            (mid, 128.0, "A", "minor", 0.85),
        )
        db_conn.commit()

        payload = export_track_metadata(db_conn, mid)
        assert payload["tables"]["musical_features"]["bpm_final"] == 128.0

        db_conn.execute("DELETE FROM musical_features WHERE metadata_id = ?", (mid,))
        db_conn.commit()

        written, _ = write_metadata_sidecar(audio_file, payload, key=SIGNING_KEY)
        assert import_track_from_sidecar(db_conn, written, key=SIGNING_KEY) == "updated"

        row = db_conn.execute(
            "SELECT bpm_final, key_name FROM musical_features WHERE metadata_id = ?",
            (mid,),
        ).fetchone()
        assert row["bpm_final"] == 128.0
        assert row["key_name"] == "A"


# ---------------------------------------------------------------------------
# BLOB round-trip
# ---------------------------------------------------------------------------

class TestBlobRoundTrip:
    @pytest.mark.integration
    def test_timbre_blob(
        self, db_conn: sqlite3.Connection, audio_file: Path
    ) -> None:
        mid = "01TEST00000000000000000010"
        _insert_full_track(db_conn, mid, audio_file)

        fake = b"\x93NUMPY" + b"\x00" * 50
        db_conn.execute(
            "INSERT INTO timbre_features (metadata_id, mfcc_mean, mfcc_var, centroid_mean) "
            "VALUES (?, ?, ?, ?)",
            (mid, fake, fake, 1500.0),
        )
        db_conn.commit()

        payload = export_track_metadata(db_conn, mid)
        timbre = payload["tables"]["timbre_features"]
        assert BLOB_MARKER in timbre["mfcc_mean"]
        assert base64.b64decode(timbre["mfcc_mean"][BLOB_MARKER]) == fake

        db_conn.execute("DELETE FROM timbre_features WHERE metadata_id = ?", (mid,))
        db_conn.commit()

        written, _ = write_metadata_sidecar(audio_file, payload, key=SIGNING_KEY)
        assert import_track_from_sidecar(db_conn, written, key=SIGNING_KEY) == "updated"

        row = db_conn.execute(
            "SELECT mfcc_mean, centroid_mean FROM timbre_features WHERE metadata_id = ?",
            (mid,),
        ).fetchone()
        assert bytes(row["mfcc_mean"]) == fake
        assert row["centroid_mean"] == 1500.0

    @pytest.mark.integration
    def test_embeddings_blob(
        self, db_conn: sqlite3.Connection, audio_file: Path
    ) -> None:
        mid = "01TEST00000000000000000011"
        _insert_full_track(db_conn, mid, audio_file)

        vec = b"\x00\x01\x02\x03" * 128
        db_conn.execute(
            "INSERT INTO embeddings (metadata_id, model, scope, dim, vector) "
            "VALUES (?, ?, ?, ?, ?)",
            (mid, "clap-2024", "track", 512, vec),
        )
        db_conn.commit()

        payload = export_track_metadata(db_conn, mid)
        emb = payload["tables"]["embeddings"]
        assert len(emb) == 1

        db_conn.execute("DELETE FROM embeddings WHERE metadata_id = ?", (mid,))
        db_conn.commit()

        written, _ = write_metadata_sidecar(audio_file, payload, key=SIGNING_KEY)
        assert import_track_from_sidecar(db_conn, written, key=SIGNING_KEY) == "updated"

        row = db_conn.execute(
            "SELECT model, vector FROM embeddings WHERE metadata_id = ?", (mid,)
        ).fetchone()
        assert row["model"] == "clap-2024"
        assert bytes(row["vector"]) == vec


# ---------------------------------------------------------------------------
# Multi-row round-trip (including new tables)
# ---------------------------------------------------------------------------

class TestMultiRowRoundTrip:
    @pytest.mark.integration
    def test_external_ids(self, db_conn: sqlite3.Connection, audio_file: Path) -> None:
        mid = "01TEST00000000000000000020"
        _insert_full_track(db_conn, mid, audio_file)
        db_conn.execute(
            "INSERT INTO external_ids (metadata_id, source, external_id, confidence) "
            "VALUES (?, ?, ?, ?)",
            (mid, "isrc", "USRC12345", 0.95),
        )
        db_conn.commit()

        payload = export_track_metadata(db_conn, mid)
        db_conn.execute("DELETE FROM external_ids WHERE metadata_id = ?", (mid,))
        db_conn.commit()

        written, _ = write_metadata_sidecar(audio_file, payload, key=SIGNING_KEY)
        assert import_track_from_sidecar(db_conn, written, key=SIGNING_KEY) == "updated"

        rows = db_conn.execute(
            "SELECT source FROM external_ids WHERE metadata_id = ?", (mid,)
        ).fetchall()
        assert [r["source"] for r in rows] == ["isrc"]

    @pytest.mark.integration
    def test_tag_writeback_log(
        self, db_conn: sqlite3.Connection, audio_file: Path
    ) -> None:
        mid = "01TEST00000000000000000021"
        _insert_full_track(db_conn, mid, audio_file)
        db_conn.execute(
            "INSERT INTO tag_writeback_log (metadata_id, field_name, old_value, new_value) "
            "VALUES (?, ?, ?, ?)",
            (mid, "genre", "Trance", "Progressive Trance"),
        )
        db_conn.commit()

        payload = export_track_metadata(db_conn, mid)
        assert "tag_writeback_log" in payload["tables"]
        assert payload["tables"]["tag_writeback_log"][0]["field_name"] == "genre"

        db_conn.execute("DELETE FROM tag_writeback_log WHERE metadata_id = ?", (mid,))
        db_conn.commit()

        written, _ = write_metadata_sidecar(audio_file, payload, key=SIGNING_KEY)
        assert import_track_from_sidecar(db_conn, written, key=SIGNING_KEY) == "updated"

        rows = db_conn.execute(
            "SELECT field_name, new_value FROM tag_writeback_log WHERE metadata_id = ?",
            (mid,),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["new_value"] == "Progressive Trance"


# ---------------------------------------------------------------------------
# similarity_edges (outgoing only, unknown dst filtered on import)
# ---------------------------------------------------------------------------

class TestSimilarityEdges:
    @pytest.mark.integration
    def test_outgoing_edges_round_trip_with_unknown_dst(
        self, db_conn: sqlite3.Connection, audio_file: Path, tmp_path: Path
    ) -> None:
        src = "01EDGESRC00000000000000001"
        known_dst = "01EDGEDST00000000000000002"
        unknown_dst = "01EDGEMISSING00000000000003"

        _insert_full_track(db_conn, src, audio_file)

        other_audio = tmp_path / "b" / "other.mp3"
        other_audio.parent.mkdir(parents=True, exist_ok=True)
        other_audio.write_bytes(b"\xff\xfb\x90\x00" * 50)
        _insert_full_track(db_conn, known_dst, other_audio)

        third_audio = tmp_path / "c" / "third.mp3"
        third_audio.parent.mkdir(parents=True, exist_ok=True)
        third_audio.write_bytes(b"\xff\xfb\x90\x00" * 50)
        _insert_full_track(db_conn, unknown_dst, third_audio)

        db_conn.executemany(
            "INSERT INTO similarity_edges (src_id, dst_id, metric, value) "
            "VALUES (?, ?, ?, ?)",
            [
                (src, known_dst, "cosine", 0.9),
                (src, unknown_dst, "cosine", 0.8),
            ],
        )
        db_conn.commit()

        payload = export_track_metadata(db_conn, src)
        edges = payload["tables"][OUTGOING_EDGE_TABLE]
        assert {e["dst_id"] for e in edges} == {known_dst, unknown_dst}

        # Simulate the target DB not knowing about unknown_dst at import time.
        db_conn.execute("DELETE FROM similarity_edges WHERE src_id = ?", (src,))
        db_conn.execute("DELETE FROM tracks WHERE metadata_id = ?", (unknown_dst,))
        db_conn.commit()

        written, _ = write_metadata_sidecar(audio_file, payload, key=SIGNING_KEY)
        assert import_track_from_sidecar(db_conn, written, key=SIGNING_KEY) == "updated"

        rows = db_conn.execute(
            "SELECT dst_id FROM similarity_edges WHERE src_id = ?", (src,)
        ).fetchall()
        # Unknown destination was filtered out.
        assert {r["dst_id"] for r in rows} == {known_dst}


# ---------------------------------------------------------------------------
# .dlpmeta export/import
# ---------------------------------------------------------------------------

class TestDlpmetaRoundTrip:
    @pytest.mark.integration
    def test_signed_dlpmeta_round_trip(
        self, db_conn: sqlite3.Connection, audio_file: Path
    ) -> None:
        mid = "01DLP000000000000000000001"
        _insert_full_track(db_conn, mid, audio_file)

        data = SidecarData(
            metadata_id=mid,
            path=str(audio_file),
            size=audio_file.stat().st_size,
            mtime=audio_file.stat().st_mtime,
            hash_partial="p",
            hash_full="f",
            fingerprint="fp",
            version=SIDECAR_VERSION,
        )
        written, signed = write_dlpmeta_sidecar(audio_file, data, key=SIGNING_KEY)
        assert signed
        assert str(written).endswith(SIDECAR_EXT)
        assert not str(written).endswith(SIDECAR_BAK_EXT)

        # Blow away track_sidecars so we can see import restore it.
        db_conn.execute("DELETE FROM track_sidecars")
        db_conn.commit()

        result = import_dlpmeta_from_file(db_conn, written, key=SIGNING_KEY)
        assert result == "updated"

        row = db_conn.execute(
            "SELECT hash_full, fingerprint FROM track_sidecars WHERE metadata_id = ?",
            (mid,),
        ).fetchone()
        assert row["hash_full"] == "f"
        assert row["fingerprint"] == "fp"

    @pytest.mark.integration
    def test_unsigned_dlpmeta_writes_bak(
        self, db_conn: sqlite3.Connection, audio_file: Path
    ) -> None:
        mid = "01DLP000000000000000000002"
        _insert_full_track(db_conn, mid, audio_file)
        data = SidecarData(
            metadata_id=mid,
            path=str(audio_file),
            size=100,
            mtime=0.0,
            version=SIDECAR_VERSION,
        )
        written, signed = write_dlpmeta_sidecar(audio_file, data, key=None)
        assert not signed
        assert str(written).endswith(SIDECAR_BAK_EXT)
        assert written.exists()

    @pytest.mark.integration
    def test_dlpmeta_create_missing(
        self, db_conn: sqlite3.Connection, tmp_path: Path
    ) -> None:
        audio = tmp_path / "new" / "song.mp3"
        audio.parent.mkdir(parents=True, exist_ok=True)
        audio.write_bytes(b"\xff\xfb\x90\x00" * 20)

        mid = "01DLPNEW00000000000000001"
        data = SidecarData(
            metadata_id=mid,
            path=str(audio),
            size=audio.stat().st_size,
            mtime=audio.stat().st_mtime,
            hash_full="hhh",
            version=SIDECAR_VERSION,
        )
        written, _ = write_dlpmeta_sidecar(audio, data, key=SIGNING_KEY)

        # No track row yet; without create_missing this is skipped.
        assert (
            import_dlpmeta_from_file(db_conn, written, key=SIGNING_KEY) == "skipped"
        )
        assert (
            import_dlpmeta_from_file(
                db_conn, written, key=SIGNING_KEY, create_missing=True
            )
            == "created"
        )
        row = db_conn.execute(
            "SELECT full_path FROM tracks WHERE metadata_id = ?", (mid,)
        ).fetchone()
        assert row is not None
        assert row["full_path"] == str(audio)


# ---------------------------------------------------------------------------
# Bulk export / import
# ---------------------------------------------------------------------------

class TestBulkExportImport:
    @pytest.mark.integration
    def test_signed_export_writes_both_sidecars(self, tmp_path: Path) -> None:
        db_path = tmp_path / "bulk.db"
        conn = get_connection(db_path)
        create_schema(conn)

        audios: list[Path] = []
        for i in range(3):
            audio = tmp_path / "music" / f"track_{i}.mp3"
            audio.parent.mkdir(parents=True, exist_ok=True)
            audio.write_bytes(b"\xff\xfb\x90\x00" * 50)
            audios.append(audio)
            _insert_full_track(conn, f"01BULK00000000000000000{i:02d}", audio)

        stats = export_sidecars(conn, key=SIGNING_KEY)
        assert stats["exported_msmeta"] == 3
        assert stats["exported_dlpmeta"] == 3
        assert stats["unsigned_msmeta"] == 0
        assert stats["unsigned_dlpmeta"] == 0

        for audio in audios:
            assert msmeta_path_for(audio).exists()
            assert not msmeta_path_for(audio, bak=True).exists()
            assert Path(str(audio) + SIDECAR_EXT).exists()
            assert not Path(str(audio) + SIDECAR_BAK_EXT).exists()

        conn.close()

    @pytest.mark.integration
    def test_unsigned_export_writes_both_baks(self, tmp_path: Path) -> None:
        db_path = tmp_path / "bulk.db"
        conn = get_connection(db_path)
        create_schema(conn)

        audio = tmp_path / "music" / "unsigned.mp3"
        audio.parent.mkdir(parents=True, exist_ok=True)
        audio.write_bytes(b"\xff\xfb\x90\x00" * 50)
        _insert_full_track(conn, "01UNSIGN000000000000000001", audio)

        stats = export_sidecars(conn, key=None)
        assert stats["unsigned_msmeta"] == 1
        assert stats["unsigned_dlpmeta"] == 1
        assert msmeta_path_for(audio, bak=True).exists()
        assert Path(str(audio) + SIDECAR_BAK_EXT).exists()
        assert not msmeta_path_for(audio).exists()
        assert not Path(str(audio) + SIDECAR_EXT).exists()

        conn.close()

    @pytest.mark.integration
    def test_bulk_export_then_import_round_trip(self, tmp_path: Path) -> None:
        db_path = tmp_path / "bulk.db"
        conn = get_connection(db_path)
        create_schema(conn)

        mids: list[str] = []
        for i in range(2):
            audio = tmp_path / "music" / f"track_{i}.mp3"
            audio.parent.mkdir(parents=True, exist_ok=True)
            audio.write_bytes(b"\xff\xfb\x90\x00" * 50)
            mid = f"01ROUNDTRIP000000000000{i:02d}"
            mids.append(mid)
            _insert_full_track(conn, mid, audio)

        export_sidecars(conn, key=SIGNING_KEY)

        for mid in mids:
            conn.execute(
                "UPDATE tracks SET title = 'CHANGED' WHERE metadata_id = ?", (mid,)
            )
        conn.commit()

        stats = import_sidecars(conn, tmp_path / "music", key=SIGNING_KEY)
        assert stats["msmeta_updated"] == 2
        assert stats["dlpmeta_updated"] == 2
        assert stats["msmeta_corrupt"] == 0
        assert stats["dlpmeta_corrupt"] == 0

        for mid in mids:
            row = conn.execute(
                "SELECT title FROM tracks WHERE metadata_id = ?", (mid,)
            ).fetchone()
            assert row["title"] == "Test Song"

        conn.close()

    @pytest.mark.integration
    def test_signed_reexport_leaves_preexisting_bak_intact(
        self, tmp_path: Path
    ) -> None:
        """A signed re-export must not overwrite or delete a pre-existing ``.bak``."""
        db_path = tmp_path / "bulk.db"
        conn = get_connection(db_path)
        create_schema(conn)

        audio = tmp_path / "music" / "track.mp3"
        audio.parent.mkdir(parents=True, exist_ok=True)
        audio.write_bytes(b"\xff\xfb\x90\x00" * 50)
        _insert_full_track(conn, "01BAKKEEP000000000000001", audio)

        # First export with no key → both files land as .bak.
        export_sidecars(conn, key=None)
        msmeta_bak = msmeta_path_for(audio, bak=True)
        dlpmeta_bak = Path(str(audio) + SIDECAR_BAK_EXT)
        assert msmeta_bak.exists()
        assert dlpmeta_bak.exists()

        bak_msmeta_bytes = msmeta_bak.read_bytes()
        bak_dlpmeta_bytes = dlpmeta_bak.read_bytes()

        # Signed re-export writes the live files without touching the .bak files.
        export_sidecars(conn, key=SIGNING_KEY)
        assert msmeta_path_for(audio).exists()
        assert Path(str(audio) + SIDECAR_EXT).exists()
        assert msmeta_bak.exists()
        assert dlpmeta_bak.exists()
        assert msmeta_bak.read_bytes() == bak_msmeta_bytes
        assert dlpmeta_bak.read_bytes() == bak_dlpmeta_bytes

        conn.close()

    @pytest.mark.integration
    def test_path_prefix_map_rewrites_lookup_but_not_db(
        self, tmp_path: Path
    ) -> None:
        """--path-prefix-map rewrites the on-disk path while leaving the DB row alone."""
        db_path = tmp_path / "map.db"
        conn = get_connection(db_path)
        create_schema(conn)

        # The audio lives under tmp_path/"real" on disk; the DB row references
        # a fake SOURCE prefix that doesn't exist.
        real_audio = tmp_path / "real" / "song.mp3"
        real_audio.parent.mkdir(parents=True, exist_ok=True)
        real_audio.write_bytes(b"\xff\xfb\x90\x00" * 50)

        fake_audio = Path("X:\\nope") / "real" / "song.mp3"
        conn.execute(
            "INSERT INTO tracks (metadata_id, file_path, filename, full_path) "
            "VALUES (?, ?, ?, ?)",
            (
                "01MAP00000000000000000001",
                str(fake_audio.parent) + os.sep,
                fake_audio.name,
                str(fake_audio),
            ),
        )
        conn.commit()

        # Without a map, the export skips the track as missing.
        stats = export_sidecars(conn, key=SIGNING_KEY)
        assert stats["skipped_missing"] == 1
        assert stats["exported_msmeta"] == 0

        # With a map, the sidecar lands next to the real audio file.
        maps = ((r"X:\nope", str(tmp_path)),)
        stats = export_sidecars(conn, key=SIGNING_KEY, path_maps=maps)
        assert stats["exported_msmeta"] == 1
        assert stats["exported_dlpmeta"] == 1
        assert msmeta_path_for(real_audio).exists()
        assert Path(str(real_audio) + SIDECAR_EXT).exists()

        # DB row is unchanged.
        row = conn.execute(
            "SELECT full_path FROM tracks WHERE metadata_id = ?",
            ("01MAP00000000000000000001",),
        ).fetchone()
        assert row["full_path"] == str(fake_audio)

        conn.close()

    @pytest.mark.unit
    def test_parse_path_prefix_maps_rejects_malformed(self) -> None:
        with pytest.raises(ValueError):
            parse_path_prefix_maps(["no-equals-sign"])
        with pytest.raises(ValueError):
            parse_path_prefix_maps(["=notarget"])
        with pytest.raises(ValueError):
            parse_path_prefix_maps(["nosource="])

    @pytest.mark.unit
    def test_apply_path_map_first_match_wins(self) -> None:
        maps = ((r"I:\Music", "Y:"), (r"I:", "Z:"))
        assert apply_path_map(r"I:\Music\foo.mp3", maps) == r"Y:\foo.mp3"
        assert apply_path_map(r"I:\Other\bar.mp3", maps) == r"Z:\Other\bar.mp3"
        assert apply_path_map(r"C:\elsewhere.mp3", maps) == r"C:\elsewhere.mp3"

    @pytest.mark.integration
    def test_parallel_export_matches_serial(self, tmp_path: Path) -> None:
        """``workers > 1`` must produce the same on-disk result as ``workers == 1``."""
        db_path = tmp_path / "parallel.db"
        conn = get_connection(db_path)
        create_schema(conn)

        audios: list[Path] = []
        for i in range(10):
            audio = tmp_path / "music" / f"track_{i:02d}.mp3"
            audio.parent.mkdir(parents=True, exist_ok=True)
            audio.write_bytes(b"\xff\xfb\x90\x00" * 50)
            audios.append(audio)
            _insert_full_track(conn, f"01PAR00000000000000000{i:03d}", audio)

        stats = export_sidecars(conn, key=SIGNING_KEY, workers=4, db_path=db_path)
        assert stats["exported_msmeta"] == 10
        assert stats["exported_dlpmeta"] == 10
        assert stats["errors"] == 0

        # Every track got both signed sidecars.
        for audio in audios:
            ms = msmeta_path_for(audio)
            dlp = Path(str(audio) + SIDECAR_EXT)
            assert ms.exists() and dlp.exists()
            ms_raw = json.loads(ms.read_text(encoding="utf-8"))
            dlp_raw = json.loads(dlp.read_text(encoding="utf-8"))
            assert ms_raw["signature"]["hmac"] is not None
            assert dlp_raw["signature"]["hmac"] is not None

        conn.close()

    @pytest.mark.integration
    def test_parallel_export_requires_db_path(self, tmp_path: Path) -> None:
        db_path = tmp_path / "parallel.db"
        conn = get_connection(db_path)
        create_schema(conn)
        with pytest.raises(ValueError, match="db_path"):
            export_sidecars(conn, key=SIGNING_KEY, workers=2)
        conn.close()

    @pytest.mark.integration
    def test_import_also_discovers_bak_files(self, tmp_path: Path) -> None:
        db_path = tmp_path / "bulk.db"
        conn = get_connection(db_path)
        create_schema(conn)

        audio = tmp_path / "music" / "track.mp3"
        audio.parent.mkdir(parents=True, exist_ok=True)
        audio.write_bytes(b"\xff\xfb\x90\x00" * 50)
        _insert_full_track(conn, "01BAKDISCOVER0000000000001", audio)

        # Unsigned export produces only .bak files.
        export_sidecars(conn, key=None)
        assert msmeta_path_for(audio, bak=True).exists()

        conn.execute("UPDATE tracks SET title = 'CHANGED'")
        conn.commit()

        stats = import_sidecars(conn, tmp_path / "music", key=SIGNING_KEY)
        # No signing key in the sidecars so these count as unsigned, but the
        # import walker still found them and restored the data.
        assert stats["msmeta_unsigned"] == 1
        assert stats["dlpmeta_unsigned"] == 1
        row = conn.execute("SELECT title FROM tracks").fetchone()
        assert row["title"] == "Test Song"

        conn.close()


# ---------------------------------------------------------------------------
# Legacy v1 reads
# ---------------------------------------------------------------------------

class TestLegacyV1:
    @pytest.mark.integration
    def test_v1_msmeta_reads_as_unsigned(
        self, db_conn: sqlite3.Connection, audio_file: Path
    ) -> None:
        mid = "01LEGACY00000000000000001"
        _insert_full_track(db_conn, mid, audio_file)

        legacy = {
            "schema_version": 1,
            "metadata_id": mid,
            "full_path": str(audio_file),
            "filename": audio_file.name,
            "exported_at": "2025-01-01T00:00:00Z",
            "tables": {"tracks": {"title": "Legacy Song"}},
        }
        sc_path = msmeta_path_for(audio_file)
        sc_path.write_text(json.dumps(legacy), encoding="utf-8")

        result = import_track_from_sidecar(db_conn, sc_path, key=SIGNING_KEY)
        assert result == "unsigned_updated"
        assert msmeta_path_for(audio_file, bak=True).exists()
        row = db_conn.execute(
            "SELECT title FROM tracks WHERE metadata_id = ?", (mid,)
        ).fetchone()
        assert row["title"] == "Legacy Song"

    @pytest.mark.integration
    def test_v1_dlpmeta_reads_as_unsigned(
        self, db_conn: sqlite3.Connection, audio_file: Path
    ) -> None:
        mid = "01LEGACYDLP000000000000001"
        _insert_full_track(db_conn, mid, audio_file)

        legacy = {
            "v": 1,
            "id": mid,
            "path": str(audio_file),
            "size": 100,
            "mtime": 0.0,
            "hash_partial": "p",
            "hash_full": "f",
            "fp": "fp",
            "created_at": "2025-01-01T00:00:00Z",
            "updated_at": "2025-01-01T00:00:00Z",
        }
        sc_path = Path(str(audio_file) + SIDECAR_EXT)
        sc_path.write_text(json.dumps(legacy), encoding="utf-8")

        result = import_dlpmeta_from_file(db_conn, sc_path, key=SIGNING_KEY)
        assert result == "unsigned_updated"
        assert Path(str(audio_file) + SIDECAR_BAK_EXT).exists()


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------

class TestBackupDb:
    @pytest.mark.integration
    def test_creates_valid_db(self, tmp_path: Path) -> None:
        db_path = tmp_path / "source.db"
        conn = get_connection(db_path)
        create_schema(conn)
        insert_dummy_track(conn, "01TESTBACKUP000000000001")
        conn.close()

        backup = backup_db(db_path)
        assert backup.exists()

        bconn = sqlite3.connect(str(backup))
        bconn.row_factory = sqlite3.Row
        row = bconn.execute(
            "SELECT metadata_id FROM tracks WHERE metadata_id = ?",
            ("01TESTBACKUP000000000001",),
        ).fetchone()
        bconn.close()
        assert row is not None

    @pytest.mark.integration
    def test_custom_output(self, tmp_path: Path) -> None:
        db_path = tmp_path / "src.db"
        conn = get_connection(db_path)
        create_schema(conn)
        conn.close()
        custom = tmp_path / "target.db"
        assert backup_db(db_path, custom) == custom
        assert custom.exists()


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    @pytest.mark.integration
    def test_import_skips_unknown_metadata_id(
        self, db_conn: sqlite3.Connection, tmp_path: Path
    ) -> None:
        sc_path = tmp_path / "ghost.mp3.msmeta.json"
        payload = {
            "schema_version": SCHEMA_VERSION,
            "metadata_id": "01NONEXISTENT0000000000",
            "full_path": "C:\\fake\\ghost.mp3",
            "filename": "ghost.mp3",
            "exported_at": "2026-04-11T00:00:00Z",
            "tables": {},
        }
        payload[SIGNATURE_FIELD] = sign_payload(payload, SIGNING_KEY)
        sc_path.write_text(json.dumps(payload), encoding="utf-8")

        assert (
            import_track_from_sidecar(db_conn, sc_path, key=SIGNING_KEY) == "skipped"
        )

    @pytest.mark.integration
    def test_malformed_sidecar_returns_error(
        self, db_conn: sqlite3.Connection, tmp_path: Path
    ) -> None:
        sc_path = tmp_path / "bad.mp3.msmeta.json"
        sc_path.write_text("{invalid json", encoding="utf-8")
        assert (
            import_track_from_sidecar(db_conn, sc_path, key=SIGNING_KEY) == "error"
        )

    @pytest.mark.integration
    def test_export_nonexistent_track_returns_none(
        self, db_conn: sqlite3.Connection
    ) -> None:
        assert export_track_metadata(db_conn, "01DOESNOTEXIST00000000") is None

    @pytest.mark.integration
    def test_export_skips_missing_audio(self, tmp_path: Path) -> None:
        db_path = tmp_path / "skip.db"
        conn = get_connection(db_path)
        create_schema(conn)
        conn.execute(
            "INSERT INTO tracks (metadata_id, file_path, filename, full_path) "
            "VALUES (?, ?, ?, ?)",
            (
                "01NOAUDIO00000000000001",
                "C:\\fake\\",
                "missing.mp3",
                "C:\\fake\\missing.mp3",
            ),
        )
        conn.commit()
        stats = export_sidecars(conn, key=SIGNING_KEY)
        assert stats["skipped_missing"] == 1
        assert stats["exported_msmeta"] == 0
        conn.close()
