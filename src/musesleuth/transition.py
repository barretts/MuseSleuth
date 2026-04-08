"""Transition cost function for DJ-style playlist sequencing."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Optional



@dataclass
class TrackFeatures:
    """Lightweight feature vector for transition scoring."""

    bpm: Optional[float] = None
    camelot_key: Optional[str] = None
    energy: Optional[float] = None
    lufs: Optional[float] = None
    timbre_vec: Optional[list[float]] = None
    embedding_vec: Optional[list[float]] = None
    embedding_dist: Optional[float] = None


@dataclass
class TransitionWeights:
    """Per-factor weights for the transition cost function."""

    bpm: float = 1.0
    key: float = 1.5
    energy: float = 1.0
    loudness: float = 0.5
    timbre: float = 0.3
    embedding: float = 0.5


_DEFAULT_WEIGHTS = TransitionWeights()


def transition_cost(
    a: TrackFeatures,
    b: TrackFeatures,
    weights: Optional[TransitionWeights] = None,
) -> float:
    """Compute a weighted transition cost between two tracks.

    Lower cost = smoother transition. Each factor is normalized to roughly
    [0, 1] before weighting so that weights are comparable.

    Factors:
      - BPM:      |bpm_a - bpm_b| / 30  (30 BPM diff = cost 1.0)
      - Key:      0 if Camelot-compatible, 1 otherwise
      - Energy:   |energy_a - energy_b|  (already 0-1 range)
      - Loudness: |lufs_a - lufs_b| / 12 (12 LU diff = cost 1.0)
      - Timbre:   cosine distance of timbre vectors (0-2 range, /2)
      - Embedding: pre-computed embedding distance (0-2 range, /2)

    Returns a non-negative float.
    """
    w = weights or _DEFAULT_WEIGHTS
    total = 0.0

    # BPM factor
    if a.bpm is not None and b.bpm is not None:
        bpm_diff = abs(a.bpm - b.bpm) / 30.0
        total += w.bpm * bpm_diff

    # Key compatibility factor
    if a.camelot_key is not None and b.camelot_key is not None:
        from musesleuth.playlist_generator import camelot_compatible
        key_penalty = 0.0 if camelot_compatible(a.camelot_key, b.camelot_key) else 1.0
        total += w.key * key_penalty

    # Energy factor
    if a.energy is not None and b.energy is not None:
        total += w.energy * abs(a.energy - b.energy)

    # Loudness factor
    if a.lufs is not None and b.lufs is not None:
        lufs_diff = abs(a.lufs - b.lufs) / 12.0
        total += w.loudness * lufs_diff

    # Timbre cosine distance factor
    if a.timbre_vec is not None and b.timbre_vec is not None:
        try:
            import numpy as np
            va = np.array(a.timbre_vec, dtype=np.float64)
            vb = np.array(b.timbre_vec, dtype=np.float64)
            na, nb = np.linalg.norm(va), np.linalg.norm(vb)
            if na > 0 and nb > 0:
                cos_dist = 1.0 - float(np.dot(va, vb) / (na * nb))
                total += w.timbre * (cos_dist / 2.0)
        except Exception:
            pass

    # Embedding distance factor
    if a.embedding_vec is not None and b.embedding_vec is not None:
        try:
            import numpy as np
            va = np.array(a.embedding_vec, dtype=np.float64)
            vb = np.array(b.embedding_vec, dtype=np.float64)
            na, nb = np.linalg.norm(va), np.linalg.norm(vb)
            if na > 0 and nb > 0:
                cos_dist = 1.0 - float(np.dot(va, vb) / (na * nb))
                total += w.embedding * (cos_dist / 2.0)
        except Exception:
            pass
    elif a.embedding_dist is not None:
        total += w.embedding * (a.embedding_dist / 2.0)
    elif b.embedding_dist is not None:
        total += w.embedding * (b.embedding_dist / 2.0)

    return max(0.0, total)


def load_track_features(
    conn: sqlite3.Connection,
    metadata_id: str,
) -> Optional[TrackFeatures]:
    """Load TrackFeatures for a single track from the DB.

    Returns None if the track doesn't exist.
    """
    track = conn.execute(
        "SELECT metadata_id FROM tracks WHERE metadata_id = ?",
        (metadata_id,),
    ).fetchone()
    if track is None:
        return None

    bpm: Optional[float] = None
    energy: Optional[float] = None
    mf = conn.execute(
        "SELECT bpm_final, energy FROM musical_features WHERE metadata_id = ?",
        (metadata_id,),
    ).fetchone()
    if mf:
        bpm = mf["bpm_final"]
        energy = mf["energy"]

    camelot_key: Optional[str] = None
    ps = conn.execute(
        "SELECT camelot_key FROM playlist_signals WHERE metadata_id = ?",
        (metadata_id,),
    ).fetchone()
    if ps:
        camelot_key = ps["camelot_key"]

    lufs: Optional[float] = None
    lf = conn.execute(
        "SELECT lufs_integrated FROM loudness_features WHERE metadata_id = ?",
        (metadata_id,),
    ).fetchone()
    if lf:
        lufs = lf["lufs_integrated"]

    timbre_vec: Optional[list[float]] = None
    tf = conn.execute(
        "SELECT mfcc_mean FROM timbre_features WHERE metadata_id = ?",
        (metadata_id,),
    ).fetchone()
    if tf and tf["mfcc_mean"] is not None:
        try:
            from musesleuth.timbre import deserialize_array
            timbre_vec = deserialize_array(tf["mfcc_mean"])
        except Exception:
            timbre_vec = None

    embedding_vec: Optional[list[float]] = None
    emb = conn.execute(
        """
        SELECT vector
        FROM embeddings
        WHERE metadata_id = ? AND scope = 'global'
        ORDER BY computed_at DESC, id DESC
        LIMIT 1
        """,
        (metadata_id,),
    ).fetchone()
    if emb and emb["vector"] is not None:
        try:
            from musesleuth.timbre import deserialize_array
            embedding_vec = deserialize_array(emb["vector"])
        except Exception:
            embedding_vec = None

    return TrackFeatures(
        bpm=bpm,
        camelot_key=camelot_key,
        energy=energy,
        lufs=lufs,
        timbre_vec=timbre_vec,
        embedding_vec=embedding_vec,
    )
