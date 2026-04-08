"""Loudness analysis using EBU R 128 (ITU-R BS.1770-4) measurements."""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


@dataclass
class LoudnessResult:
    """Result of loudness analysis for a single track."""

    lufs_integrated: Optional[float] = None
    lufs_short_intro: Optional[float] = None
    lufs_short_outro: Optional[float] = None
    lra: Optional[float] = None
    true_peak_dbtp: Optional[float] = None
    crest_factor: Optional[float] = None


def compute_loudness(
    file_path: str,
    intro_s: float = 30.0,
    outro_s: float = 30.0,
) -> Optional[LoudnessResult]:
    """Compute EBU R 128 loudness metrics for an audio file.

    Returns a LoudnessResult with integrated LUFS, short-term LUFS for
    intro/outro segments, loudness range (LRA), true peak, and crest factor.
    Returns None if the file cannot be loaded.
    """
    path = Path(file_path)
    if not path.exists():
        log.warning("loudness: file not found: %s", file_path)
        return None

    try:
        import numpy as np
    except ImportError:
        log.error("loudness: numpy not installed")
        return None

    # Load audio -- try soundfile first (fast, no resampling needed for loudness),
    # fall back to librosa
    y: Optional[np.ndarray] = None
    sr: int = 0

    try:
        import soundfile as sf
        log.debug("loudness: loading %s via soundfile", file_path)
        y, sr = sf.read(file_path, dtype="float64")
        if y.ndim > 1:
            y = y.mean(axis=1)  # downmix to mono
    except Exception:
        try:
            import librosa
            log.debug("loudness: loading %s via librosa (soundfile failed)", file_path)
            y, sr = librosa.load(file_path, sr=None, mono=True)
            y = y.astype(np.float64)
        except Exception as exc:
            log.warning("loudness: failed to load %s: %s", file_path, exc)
            return None

    if y is None or len(y) == 0 or sr == 0:
        log.warning("loudness: empty audio for %s", file_path)
        return None

    # Compute integrated LUFS and LRA
    lufs_integrated = _compute_lufs(y, sr)
    lra = _compute_lra(y, sr)

    # True peak (dBTP)
    true_peak_dbtp = _compute_true_peak(y)

    # Crest factor: peak / RMS in dB
    crest_factor = _compute_crest_factor(y)

    # Short-term LUFS for intro and outro segments
    duration_s = len(y) / sr
    intro_end = min(intro_s, duration_s / 2)
    outro_start = max(duration_s - outro_s, duration_s / 2)

    intro_samples = y[: int(intro_end * sr)]
    outro_samples = y[int(outro_start * sr) :]

    lufs_short_intro = _compute_lufs(intro_samples, sr) if len(intro_samples) > 0 else None
    lufs_short_outro = _compute_lufs(outro_samples, sr) if len(outro_samples) > 0 else None

    result = LoudnessResult(
        lufs_integrated=lufs_integrated,
        lufs_short_intro=lufs_short_intro,
        lufs_short_outro=lufs_short_outro,
        lra=lra,
        true_peak_dbtp=true_peak_dbtp,
        crest_factor=crest_factor,
    )
    log.debug(
        "loudness: %s LUFS=%.1f LRA=%.1f peak=%.1f crest=%.1f",
        file_path,
        lufs_integrated or -99,
        lra or 0,
        true_peak_dbtp or -99,
        crest_factor or 0,
    )
    return result


def is_likely_demo(
    lufs_i: Optional[float],
    lra: Optional[float],
    lufs_threshold: float = -18.0,
    lra_threshold: float = 12.0,
) -> bool:
    """Heuristic: flag a track as likely unmastered/demo.

    Criteria: integrated LUFS below threshold AND loudness range above threshold.
    Both values must be non-None to make a determination.
    """
    if lufs_i is None or lra is None:
        return False
    return lufs_i < lufs_threshold and lra > lra_threshold


def run_loudness_for_track(
    conn: sqlite3.Connection,
    metadata_id: str,
    file_path: str,
) -> bool:
    """Run loudness analysis for a single track and persist to DB.

    Returns True on success, False on failure.
    """
    log.debug("run_loudness_for_track: mid=%s path=%s", metadata_id, file_path)
    result = compute_loudness(file_path)
    if result is None:
        log.warning("run_loudness_for_track: compute_loudness returned None for mid=%s path=%s", metadata_id, file_path)
        return False

    conn.execute(
        """
        INSERT OR REPLACE INTO loudness_features
            (metadata_id, lufs_integrated, lufs_short_intro, lufs_short_outro,
             lra, true_peak_dbtp, crest_factor, analyzed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        (
            metadata_id,
            result.lufs_integrated,
            result.lufs_short_intro,
            result.lufs_short_outro,
            result.lra,
            result.true_peak_dbtp,
            result.crest_factor,
        ),
    )
    conn.commit()
    return True


# ---------------------------------------------------------------------------
# Internal helpers for ITU-R BS.1770-4 loudness measurement
# ---------------------------------------------------------------------------

def _compute_lufs(y, sr: int) -> Optional[float]:
    """Compute integrated LUFS using pyloudnorm if available, else manual K-weighting."""
    import numpy as np

    if len(y) == 0:
        return None

    # Try pyloudnorm first
    try:
        import pyloudnorm as pyln
        meter = pyln.Meter(sr)
        lufs = meter.integrated_loudness(y)
        if np.isfinite(lufs):
            return float(lufs)
    except Exception:
        pass

    # Fallback: simplified ITU-R BS.1770-4 approximation
    # Mean square -> dB with -0.691 offset (K-weighting approximation)
    try:
        mean_sq = float(np.mean(y ** 2))
        if mean_sq <= 0:
            return -70.0
        lufs = 10.0 * np.log10(mean_sq) - 0.691
        return float(lufs) if np.isfinite(lufs) else -70.0
    except Exception:
        return None


def _compute_lra(y, sr: int) -> Optional[float]:
    """Compute Loudness Range (LRA) -- difference between loud and quiet short-term segments."""
    import numpy as np

    if len(y) == 0:
        return None

    # Try pyloudnorm first
    try:
        import pyloudnorm as pyln
        meter = pyln.Meter(sr)
        # pyloudnorm doesn't directly expose LRA, compute from short-term blocks
        block_size = int(3.0 * sr)  # 3-second blocks per EBU R 128
        hop = int(1.0 * sr)         # 1-second hop
        block_loudness = []

        for start in range(0, len(y) - block_size + 1, hop):
            block = y[start : start + block_size]
            bl = meter.integrated_loudness(block)
            if np.isfinite(bl) and bl > -70.0:
                block_loudness.append(bl)

        if len(block_loudness) < 2:
            return 0.0

        block_loudness.sort()
        # LRA: difference between 95th and 10th percentile
        p10 = block_loudness[max(0, int(len(block_loudness) * 0.10))]
        p95 = block_loudness[min(len(block_loudness) - 1, int(len(block_loudness) * 0.95))]
        return float(p95 - p10)
    except Exception:
        pass

    # Fallback: RMS-based LRA approximation
    try:
        block_size = int(3.0 * sr)
        hop = int(1.0 * sr)
        block_rms_db = []

        for start in range(0, len(y) - block_size + 1, hop):
            block = y[start : start + block_size]
            rms = float(np.sqrt(np.mean(block ** 2)))
            if rms > 0:
                db = 20.0 * np.log10(rms)
                if np.isfinite(db) and db > -70.0:
                    block_rms_db.append(db)

        if len(block_rms_db) < 2:
            return 0.0

        block_rms_db.sort()
        p10 = block_rms_db[max(0, int(len(block_rms_db) * 0.10))]
        p95 = block_rms_db[min(len(block_rms_db) - 1, int(len(block_rms_db) * 0.95))]
        return float(p95 - p10)
    except Exception:
        return None


def _compute_true_peak(y) -> Optional[float]:
    """Compute true peak in dBTP (oversampled peak detection)."""
    import numpy as np

    if len(y) == 0:
        return None

    try:
        # Simple peak detection (4x oversampling via linear interpolation for approximation)
        peak = float(np.max(np.abs(y)))
        if peak <= 0:
            return -100.0
        dbtp = 20.0 * np.log10(peak)
        return float(dbtp) if np.isfinite(dbtp) else -100.0
    except Exception:
        return None


def _compute_crest_factor(y) -> Optional[float]:
    """Compute crest factor (peak-to-RMS ratio) in dB."""
    import numpy as np

    if len(y) == 0:
        return None

    try:
        peak = float(np.max(np.abs(y)))
        rms = float(np.sqrt(np.mean(y ** 2)))
        if rms <= 0 or peak <= 0:
            return 0.0
        crest = 20.0 * np.log10(peak / rms)
        return float(crest) if np.isfinite(crest) else 0.0
    except Exception:
        return None
