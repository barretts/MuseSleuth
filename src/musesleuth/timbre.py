"""Timbre & classical spectral feature extraction using librosa."""
from __future__ import annotations

import io
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)


@dataclass
class TimbreResult:
    """Result of timbre feature extraction for a single track."""

    mfcc_mean: Optional[list[float]] = None
    mfcc_var: Optional[list[float]] = None
    centroid_mean: Optional[float] = None
    rolloff_mean: Optional[float] = None
    bandwidth_mean: Optional[float] = None
    flatness_mean: Optional[float] = None
    zcr_mean: Optional[float] = None


def extract_timbre(
    file_path: str,
    sr: int = 22050,
    duration: float = 60.0,
    n_mfcc: int = 20,
) -> Optional[TimbreResult]:
    """Extract timbre and spectral features from an audio file.

    Returns a TimbreResult with MFCC mean/variance (20 coefficients),
    spectral centroid, rolloff, bandwidth, flatness, and ZCR.
    Returns None if the file cannot be loaded.
    """
    path = Path(file_path)
    if not path.exists():
        log.warning("timbre: file not found: %s", file_path)
        return None

    try:
        import librosa
    except ImportError:
        log.error("timbre: librosa not installed")
        return None

    try:
        log.debug("timbre: loading %s (sr=%d, dur=%.0fs)", file_path, sr, duration)
        y, sr_actual = librosa.load(file_path, sr=sr, mono=True, duration=duration)
    except Exception as exc:
        log.warning("timbre: failed to load %s: %s", file_path, exc)
        return None

    if y is None or len(y) == 0:
        log.warning("timbre: empty audio for %s", file_path)
        return None

    try:
        # MFCCs
        mfcc = librosa.feature.mfcc(y=y, sr=sr_actual, n_mfcc=n_mfcc)
        mfcc_mean = np.mean(mfcc, axis=1).tolist()
        mfcc_var = np.var(mfcc, axis=1).tolist()

        # Spectral centroid
        centroid = librosa.feature.spectral_centroid(y=y, sr=sr_actual)
        centroid_mean = float(np.mean(centroid))

        # Spectral rolloff
        rolloff = librosa.feature.spectral_rolloff(y=y, sr=sr_actual)
        rolloff_mean = float(np.mean(rolloff))

        # Spectral bandwidth
        bandwidth = librosa.feature.spectral_bandwidth(y=y, sr=sr_actual)
        bandwidth_mean = float(np.mean(bandwidth))

        # Spectral flatness
        flatness = librosa.feature.spectral_flatness(y=y)
        flatness_mean = float(np.mean(flatness))

        # Zero crossing rate
        zcr = librosa.feature.zero_crossing_rate(y=y)
        zcr_mean = float(np.mean(zcr))

        result = TimbreResult(
            mfcc_mean=mfcc_mean,
            mfcc_var=mfcc_var,
            centroid_mean=centroid_mean,
            rolloff_mean=rolloff_mean,
            bandwidth_mean=bandwidth_mean,
            flatness_mean=flatness_mean,
            zcr_mean=zcr_mean,
        )
        log.debug(
            "timbre: %s centroid=%.1f rolloff=%.1f bw=%.1f flat=%.4f zcr=%.4f",
            file_path, centroid_mean, rolloff_mean, bandwidth_mean, flatness_mean, zcr_mean,
        )
        return result
    except Exception as exc:
        log.warning("timbre: feature extraction failed for %s: %s", file_path, exc)
        return None


def timbre_cosine_distance(
    a: Optional[TimbreResult],
    b: Optional[TimbreResult],
) -> Optional[float]:
    """Compute cosine distance between two timbre feature vectors.

    Uses concatenated MFCC mean + variance as the feature vector.
    Returns a float in [0, 2] (0 = identical, 2 = opposite).
    Returns None if either result is None.
    """
    if a is None or b is None:
        return None
    if a.mfcc_mean is None or b.mfcc_mean is None:
        return None

    vec_a = np.array(a.mfcc_mean + (a.mfcc_var or [0.0] * len(a.mfcc_mean)), dtype=np.float64)
    vec_b = np.array(b.mfcc_mean + (b.mfcc_var or [0.0] * len(b.mfcc_mean)), dtype=np.float64)

    norm_a = np.linalg.norm(vec_a)
    norm_b = np.linalg.norm(vec_b)

    if norm_a == 0 or norm_b == 0:
        return 0.0

    cosine_sim = float(np.dot(vec_a, vec_b) / (norm_a * norm_b))
    # Clamp to [-1, 1] to avoid floating point issues
    cosine_sim = max(-1.0, min(1.0, cosine_sim))
    return 1.0 - cosine_sim


# ---------------------------------------------------------------------------
# BLOB serialization helpers
# ---------------------------------------------------------------------------

def serialize_array(arr: list[float]) -> bytes:
    """Serialize a list of floats to a numpy BLOB for SQLite storage."""
    buf = io.BytesIO()
    np.save(buf, np.array(arr, dtype=np.float32))
    return buf.getvalue()


def deserialize_array(blob: Optional[bytes]) -> Optional[list[float]]:
    """Deserialize a numpy BLOB back to a list of floats."""
    if blob is None:
        return None
    buf = io.BytesIO(blob)
    arr = np.load(buf, allow_pickle=False)
    return arr.tolist()


# ---------------------------------------------------------------------------
# DB runner
# ---------------------------------------------------------------------------

def run_timbre_for_track(
    conn: sqlite3.Connection,
    metadata_id: str,
    file_path: str,
) -> bool:
    """Run timbre feature extraction for a single track and persist to DB.

    Returns True on success, False on failure.
    """
    log.debug("run_timbre_for_track: mid=%s path=%s", metadata_id, file_path)
    result = extract_timbre(file_path)
    if result is None:
        log.warning("run_timbre_for_track: extract_timbre returned None for mid=%s path=%s", metadata_id, file_path)
        return False

    mfcc_mean_blob = serialize_array(result.mfcc_mean) if result.mfcc_mean else None
    mfcc_var_blob = serialize_array(result.mfcc_var) if result.mfcc_var else None

    conn.execute(
        """
        INSERT OR REPLACE INTO timbre_features
            (metadata_id, mfcc_mean, mfcc_var, centroid_mean, rolloff_mean,
             bandwidth_mean, flatness_mean, zcr_mean, analyzed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        (
            metadata_id,
            mfcc_mean_blob,
            mfcc_var_blob,
            result.centroid_mean,
            result.rolloff_mean,
            result.bandwidth_mean,
            result.flatness_mean,
            result.zcr_mean,
        ),
    )
    conn.commit()
    return True
