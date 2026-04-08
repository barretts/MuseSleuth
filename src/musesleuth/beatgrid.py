"""Beat grid, downbeat tracking, and phrase boundary detection using librosa (with madmom fallback)."""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


@dataclass
class BeatGridResult:
    """Result of beat/downbeat detection for a single track."""

    beats: list[float] = field(default_factory=list)
    downbeats: list[float] = field(default_factory=list)
    phrase_boundaries: list[float] = field(default_factory=list)
    time_signature: Optional[str] = None
    confidence: float = 0.0


def detect_beats(
    file_path: str,
    sr: int = 22050,
    duration: float = 600.0,
) -> Optional[BeatGridResult]:
    """Detect beats and downbeats in an audio file.

    Tries madmom first (if available), falls back to librosa.
    Returns a BeatGridResult, or None on failure.
    """
    path = Path(file_path)
    if not path.exists():
        log.warning("beatgrid: file not found: %s", file_path)
        return None

    try:
        import librosa
        import numpy as np
    except ImportError:
        log.error("beatgrid: librosa/numpy not installed")
        return None

    try:
        log.debug("beatgrid: loading %s (sr=%d, dur=%.0fs)", file_path, sr, duration)
        y, sr_actual = librosa.load(file_path, sr=sr, mono=True, duration=duration)
    except Exception as exc:
        log.warning("beatgrid: failed to load %s: %s", file_path, exc)
        return None

    if y is None or len(y) == 0:
        log.warning("beatgrid: empty audio for %s", file_path)
        return None

    # Try madmom first for higher-quality beat/downbeat tracking
    result = _madmom_beats(y, sr_actual)
    if result is not None:
        log.debug("beatgrid: %s madmom %d beats, %d downbeats, conf=%.2f",
                  file_path, len(result.beats), len(result.downbeats), result.confidence)
        return result

    # Fallback: librosa beat tracking
    result = _librosa_beats(y, sr_actual)
    if result is not None:
        log.debug("beatgrid: %s librosa %d beats, conf=%.2f",
                  file_path, len(result.beats), result.confidence)
    else:
        log.warning("beatgrid: all backends failed for %s", file_path)
    return result


def derive_phrase_boundaries(
    beats: list[float],
    beats_per_bar: int = 4,
    bars_per_phrase: int = 4,
) -> list[float]:
    """Derive phrase boundaries from beat positions.

    A phrase boundary occurs every `beats_per_bar * bars_per_phrase` beats.
    Returns a list of timestamps (seconds).
    """
    if not beats:
        return []

    step = beats_per_bar * bars_per_phrase
    boundaries = []
    for i in range(0, len(beats), step):
        boundaries.append(beats[i])
    return boundaries


def run_beatgrid_for_track(
    conn: sqlite3.Connection,
    metadata_id: str,
    file_path: str,
) -> bool:
    """Detect beats, derive phrases, and persist to DB.

    Returns True on success, False on failure.
    """
    log.debug("run_beatgrid_for_track: mid=%s path=%s", metadata_id, file_path)
    path = Path(file_path)
    if not path.exists():
        log.warning("run_beatgrid_for_track: file not found mid=%s path=%s", metadata_id, file_path)
        return False

    result = detect_beats(file_path)
    if result is None:
        log.warning("run_beatgrid_for_track: detect_beats returned None mid=%s", metadata_id)
        return False

    # Derive phrase boundaries if not already set
    if not result.phrase_boundaries and result.beats:
        result.phrase_boundaries = derive_phrase_boundaries(result.beats)

    conn.execute(
        """
        INSERT OR REPLACE INTO beat_grids
            (metadata_id, beats_json, downbeats_json, phrase_boundaries_json,
             time_signature, confidence, analyzed_at)
        VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        (
            metadata_id,
            json.dumps(result.beats),
            json.dumps(result.downbeats) if result.downbeats else None,
            json.dumps(result.phrase_boundaries) if result.phrase_boundaries else None,
            result.time_signature,
            result.confidence,
        ),
    )
    conn.commit()
    return True


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _madmom_beats(y, sr: int) -> Optional[BeatGridResult]:
    """Try beat/downbeat tracking via madmom."""
    try:
        import madmom
        import numpy as np

        # madmom expects the file path or audio signal
        # Use RNNBeatProcessor + DBNBeatTrackingProcessor
        proc = madmom.features.beats.RNNBeatProcessor()
        act = proc(madmom.audio.signal.Signal(y, sample_rate=sr))

        tracker = madmom.features.beats.DBNBeatTrackingProcessor(fps=100)
        beats = tracker(act).tolist()

        # Downbeat tracking
        try:
            db_proc = madmom.features.downbeats.RNNDownBeatProcessor()
            db_act = db_proc(madmom.audio.signal.Signal(y, sample_rate=sr))
            db_tracker = madmom.features.downbeats.DBNDownBeatTrackingProcessor(
                beats_per_bar=[3, 4], fps=100
            )
            db_result = db_tracker(db_act)
            downbeats = [float(row[0]) for row in db_result if int(row[1]) == 1]
            # Infer time signature from most common beats_per_bar
            bar_lengths = []
            for i in range(len(db_result) - 1):
                if int(db_result[i][1]) == 1 and int(db_result[i + 1][1]) == 1:
                    # Doesn't happen, next downbeat is next row with beat_num == 1
                    pass
            # Simpler: count beats between downbeats
            time_sig = _infer_time_signature(beats, downbeats)
        except Exception:
            downbeats = []
            time_sig = "4/4"

        phrases = derive_phrase_boundaries(beats)

        return BeatGridResult(
            beats=beats,
            downbeats=downbeats,
            phrase_boundaries=phrases,
            time_signature=time_sig,
            confidence=0.9,
        )
    except Exception:
        return None


def _librosa_beats(y, sr: int) -> Optional[BeatGridResult]:
    """Fallback beat tracking via librosa."""
    try:
        import librosa
        import numpy as np

        tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
        beat_times = librosa.frames_to_time(beat_frames, sr=sr).tolist()

        # Estimate downbeats: assume 4/4, every 4th beat is a downbeat
        downbeats = beat_times[::4] if len(beat_times) >= 4 else beat_times[:1]

        # Phrase boundaries
        phrases = derive_phrase_boundaries(beat_times)

        # Confidence based on tempo stability
        if len(beat_times) >= 2:
            ibis = np.diff(beat_times)
            if len(ibis) > 0 and np.mean(ibis) > 0:
                cv = float(np.std(ibis) / np.mean(ibis))
                confidence = max(0.0, min(1.0, 1.0 - cv))
            else:
                confidence = 0.3
        else:
            confidence = 0.3

        return BeatGridResult(
            beats=beat_times,
            downbeats=downbeats,
            phrase_boundaries=phrases,
            time_signature="4/4",
            confidence=confidence,
        )
    except Exception:
        return None


def _infer_time_signature(
    beats: list[float], downbeats: list[float]
) -> str:
    """Infer time signature from beats per downbeat interval."""
    if len(downbeats) < 2 or len(beats) < 2:
        return "4/4"

    counts = []
    for i in range(len(downbeats) - 1):
        start = downbeats[i]
        end = downbeats[i + 1]
        n = sum(1 for b in beats if start <= b < end)
        if n > 0:
            counts.append(n)

    if not counts:
        return "4/4"

    # Most common count
    from collections import Counter
    most_common = Counter(counts).most_common(1)[0][0]

    if most_common == 3:
        return "3/4"
    elif most_common == 6:
        return "6/8"
    else:
        return "4/4"
