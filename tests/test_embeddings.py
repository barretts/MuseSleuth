"""TDD tests for deep audio embeddings (Phase 3) -- written before implementation."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from musesleuth.db import create_schema, generate_metadata_id
from tests.conftest import insert_dummy_track


class TestComputeEmbedding:
    """Tests for core embedding extraction."""

    @pytest.mark.unit
    def test_compute_embedding_shape(self, synthetic_audio: tuple) -> None:
        """Embedding output is a 1-D numpy array of expected dimensionality."""
        from musesleuth.audio_embeddings import compute_embedding
        import numpy as np

        _samples, _sr, wav_path = synthetic_audio
        vec = compute_embedding(str(wav_path), scope="global")

        assert vec is not None
        assert isinstance(vec, np.ndarray)
        assert vec.ndim == 1
        assert len(vec) > 0
        # All values should be finite
        assert np.all(np.isfinite(vec))

    @pytest.mark.unit
    def test_embedding_scopes(self, synthetic_audio: tuple) -> None:
        """Global vs intro vs outro produce vectors (may differ for asymmetric audio)."""
        from musesleuth.audio_embeddings import compute_embedding
        import numpy as np

        _samples, _sr, wav_path = synthetic_audio
        g = compute_embedding(str(wav_path), scope="global")
        i = compute_embedding(str(wav_path), scope="intro")
        o = compute_embedding(str(wav_path), scope="outro")

        assert g is not None
        assert i is not None
        assert o is not None
        # All same dimensionality
        assert len(g) == len(i) == len(o)

    @pytest.mark.unit
    def test_embedding_missing_file(self) -> None:
        """Non-existent file returns None."""
        from musesleuth.audio_embeddings import compute_embedding

        vec = compute_embedding("/nonexistent/path.wav", scope="global")
        assert vec is None

    @pytest.mark.unit
    def test_embedding_deterministic(self, synthetic_audio: tuple) -> None:
        """Same file produces same embedding (deterministic)."""
        from musesleuth.audio_embeddings import compute_embedding
        import numpy as np

        _samples, _sr, wav_path = synthetic_audio
        a = compute_embedding(str(wav_path), scope="global")
        b = compute_embedding(str(wav_path), scope="global")

        assert a is not None and b is not None
        np.testing.assert_array_almost_equal(a, b)


class TestStoreEmbeddings:
    """Tests for DB persistence of embeddings."""

    @pytest.mark.integration
    def test_store_and_load_embeddings(
        self, in_memory_db: sqlite3.Connection, synthetic_audio: tuple
    ) -> None:
        """Embeddings stored as BLOBs round-trip correctly via numpy."""
        from musesleuth.audio_embeddings import run_embedding_for_track
        from musesleuth.timbre import deserialize_array
        import numpy as np

        create_schema(in_memory_db)
        mid = generate_metadata_id()
        insert_dummy_track(in_memory_db, mid)

        _samples, _sr, wav_path = synthetic_audio
        success = run_embedding_for_track(in_memory_db, mid, str(wav_path))
        assert success is True

        rows = in_memory_db.execute(
            "SELECT * FROM embeddings WHERE metadata_id = ? ORDER BY scope",
            (mid,),
        ).fetchall()
        # Should have global, intro, outro
        assert len(rows) == 3
        scopes = {row["scope"] for row in rows}
        assert scopes == {"global", "intro", "outro"}

        for row in rows:
            assert row["model"] is not None
            assert row["dim"] > 0
            assert row["vector"] is not None
            assert row["computed_at"] is not None

            # BLOB round-trip
            vec = deserialize_array(row["vector"])
            assert vec is not None
            assert len(vec) == row["dim"]

    @pytest.mark.integration
    def test_store_replaces_on_rerun(
        self, in_memory_db: sqlite3.Connection, synthetic_audio: tuple
    ) -> None:
        """Running embedding twice replaces existing rows (UNIQUE constraint)."""
        from musesleuth.audio_embeddings import run_embedding_for_track

        create_schema(in_memory_db)
        mid = generate_metadata_id()
        insert_dummy_track(in_memory_db, mid)

        _samples, _sr, wav_path = synthetic_audio
        run_embedding_for_track(in_memory_db, mid, str(wav_path))
        run_embedding_for_track(in_memory_db, mid, str(wav_path))

        count = in_memory_db.execute(
            "SELECT COUNT(*) FROM embeddings WHERE metadata_id = ?", (mid,)
        ).fetchone()[0]
        assert count == 3  # global + intro + outro, not doubled

    @pytest.mark.integration
    def test_store_missing_file(self, in_memory_db: sqlite3.Connection) -> None:
        """Missing file returns False, no rows stored."""
        from musesleuth.audio_embeddings import run_embedding_for_track

        create_schema(in_memory_db)
        mid = generate_metadata_id()
        insert_dummy_track(in_memory_db, mid)

        success = run_embedding_for_track(in_memory_db, mid, "/nonexistent/file.wav")
        assert success is False

    @pytest.mark.unit
    def test_run_embedding_decodes_once_for_all_scopes(
        self,
        in_memory_db: sqlite3.Connection,
        synthetic_audio: tuple,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """run_embedding_for_track loads audio once and reuses it for all scopes."""
        import numpy as np
        import musesleuth.audio_embeddings as ae
        from musesleuth.audio_embeddings import run_embedding_for_track

        create_schema(in_memory_db)
        mid = generate_metadata_id()
        insert_dummy_track(in_memory_db, mid)

        _samples, _sr, wav_path = synthetic_audio

        calls = {"load": 0}

        def _fake_load_audio(file_path: str, sr: int = 22050, duration: float = 120.0):
            calls["load"] += 1
            return np.ones(sr, dtype=np.float32)

        def _fake_compute_from_audio(
            y: np.ndarray,
            sr: int,
            scope: str,
            intro_s: float,
            outro_s: float,
            file_path: str,
        ):
            return np.array([1.0, 2.0, 3.0], dtype=np.float32)

        monkeypatch.setattr(ae, "_load_audio", _fake_load_audio)
        monkeypatch.setattr(ae, "_compute_embedding_from_audio", _fake_compute_from_audio)
        monkeypatch.setattr(ae, "_detect_model", lambda: "mfcc_stat")

        success = run_embedding_for_track(in_memory_db, mid, str(wav_path))

        assert success is True
        assert calls["load"] == 1

        count = in_memory_db.execute(
            "SELECT COUNT(*) FROM embeddings WHERE metadata_id = ?",
            (mid,),
        ).fetchone()[0]
        assert count == 3
