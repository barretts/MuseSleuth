"""Structural segmentation and intro/outro detection using librosa novelty."""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


@dataclass
class Segment:
    """A detected structural segment within a track."""

    start_s: float = 0.0
    end_s: float = 0.0
    kind: str = "body"
    confidence: float = 0.0


def detect_boundaries(
    file_path: str,
    sr: int = 22050,
    duration: float = 600.0,
) -> Optional[list[Segment]]:
    """Detect structural boundaries using spectral novelty.

    Returns a list of Segments covering the full track,
    or None if the file cannot be loaded.
    """
    path = Path(file_path)
    if not path.exists():
        log.warning("structure: file not found: %s", file_path)
        return None

    try:
        import librosa
        import numpy as np
    except ImportError:
        log.error("structure: librosa/numpy not installed")
        return None

    try:
        log.debug("structure: loading %s (sr=%d, dur=%.0fs)", file_path, sr, duration)
        y, sr_actual = librosa.load(file_path, sr=sr, mono=True, duration=duration)
    except Exception as exc:
        log.warning("structure: failed to load %s: %s", file_path, exc)
        return None

    if y is None or len(y) == 0:
        log.warning("structure: empty audio for %s", file_path)
        return None

    track_duration = len(y) / sr_actual

    try:
        # Compute mel spectrogram for self-similarity
        S = librosa.feature.melspectrogram(
            y=y, sr=sr_actual, n_fft=2048, hop_length=512, n_mels=128
        )
        S_db = librosa.power_to_db(S, ref=np.max)

        # Compute novelty function from spectral flux
        novelty = np.diff(S_db, axis=1)
        novelty_curve = np.mean(np.abs(novelty), axis=0)

        # Smooth the novelty curve
        kernel_size = max(1, int(sr_actual / 512 * 0.5))  # ~0.5s smoothing
        if kernel_size > 1:
            kernel = np.ones(kernel_size) / kernel_size
            novelty_curve = np.convolve(novelty_curve, kernel, mode="same")

        # Find peaks in novelty curve
        peak_threshold = np.mean(novelty_curve) + 1.0 * np.std(novelty_curve)
        hop_time = 512 / sr_actual

        # Simple peak picking with minimum distance
        min_distance_frames = max(1, int(2.0 / hop_time))  # at least 2s between peaks
        boundaries_frames = _pick_peaks(novelty_curve, peak_threshold, min_distance_frames)

        # Convert frame indices to time
        boundary_times = [float(f * hop_time) for f in boundaries_frames]

        # Build segments from boundaries
        segments = _boundaries_to_segments(boundary_times, track_duration)

        if len(segments) == 0:
            # No boundaries found -- single segment covering full track
            segments = [Segment(start_s=0.0, end_s=track_duration, kind="body", confidence=0.5)]

        log.debug("structure: %s %d segments (%.1fs)", file_path, len(segments), track_duration)
        return segments

    except Exception as exc:
        log.warning("structure: segmentation failed for %s: %s", file_path, exc)
        return None


def label_intro_outro(
    segments: list[Segment],
    duration_s: float,
    intro_max_s: float = 60.0,
    outro_max_s: float = 60.0,
) -> list[Segment]:
    """Label first and last segments as intro/outro if they're short enough.

    Modifies kind in-place and returns the list.
    """
    if not segments:
        return segments

    result = []
    for seg in segments:
        result.append(Segment(
            start_s=seg.start_s,
            end_s=seg.end_s,
            kind=seg.kind,
            confidence=seg.confidence,
        ))

    # Label intro
    if result[0].end_s - result[0].start_s <= intro_max_s:
        result[0].kind = "intro"

    # Label outro
    if len(result) > 1 and (result[-1].end_s - result[-1].start_s) <= outro_max_s:
        result[-1].kind = "outro"

    return result


def is_radio_edit(segments: list[Segment]) -> bool:
    """Heuristic: a track is likely a radio edit if intro < 15s.

    Returns False for empty segment lists.
    """
    if not segments:
        return False

    # Find the intro segment
    for seg in segments:
        if seg.kind == "intro":
            return (seg.end_s - seg.start_s) < 15.0

    # If no labeled intro, check the first segment
    first = segments[0]
    return (first.end_s - first.start_s) < 15.0


def run_structure_for_track(
    conn: sqlite3.Connection,
    metadata_id: str,
    file_path: str,
) -> bool:
    """Detect structure, label intro/outro, and persist to DB.

    Returns True on success, False on failure.
    """
    log.debug("run_structure_for_track: mid=%s path=%s", metadata_id, file_path)
    path = Path(file_path)
    if not path.exists():
        log.warning("run_structure_for_track: file not found mid=%s path=%s", metadata_id, file_path)
        return False

    segments = detect_boundaries(file_path)
    if segments is None:
        log.warning("run_structure_for_track: detect_boundaries returned None mid=%s", metadata_id)
        return False

    # detect_boundaries already loads audio and returns segments that span track duration.
    duration_s = segments[-1].end_s if segments else 0.0

    segments = label_intro_outro(segments, duration_s)

    # Delete existing segments for this track
    conn.execute(
        "DELETE FROM structure_segments WHERE metadata_id = ?",
        (metadata_id,),
    )

    for idx, seg in enumerate(segments):
        conn.execute(
            """
            INSERT INTO structure_segments
                (metadata_id, segment_idx, start_s, end_s, kind, confidence, analyzed_at)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (metadata_id, idx, seg.start_s, seg.end_s, seg.kind, seg.confidence),
        )

    conn.commit()
    return True


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _pick_peaks(
    curve,
    threshold: float,
    min_distance: int,
) -> list[int]:
    """Simple peak picking with threshold and minimum distance."""
    import numpy as np

    peaks = []
    last_peak = -min_distance

    for i in range(1, len(curve) - 1):
        if curve[i] > threshold and curve[i] > curve[i - 1] and curve[i] >= curve[i + 1]:
            if i - last_peak >= min_distance:
                peaks.append(i)
                last_peak = i

    return peaks


def _boundaries_to_segments(
    boundary_times: list[float],
    track_duration: float,
) -> list[Segment]:
    """Convert boundary timestamps to a list of Segments covering the full track."""
    # Always include 0 and track_duration
    all_times = [0.0] + sorted(boundary_times) + [track_duration]

    # Remove duplicates / near-duplicates
    cleaned = [all_times[0]]
    for t in all_times[1:]:
        if t - cleaned[-1] > 0.5:  # min 0.5s gap
            cleaned.append(t)
    if cleaned[-1] < track_duration - 0.5:
        cleaned.append(track_duration)

    segments = []
    for i in range(len(cleaned) - 1):
        segments.append(Segment(
            start_s=cleaned[i],
            end_s=cleaned[i + 1],
            kind="body",
            confidence=0.5,
        ))

    return segments
