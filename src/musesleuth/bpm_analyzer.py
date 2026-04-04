"""BPM and key analysis using aubio and librosa with cross-check."""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Optional


# Thread-local audio cache: avoids loading the same file twice for BPM + key
_audio_cache = threading.local()


BPM_DISAGREEMENT_THRESHOLD = 10.0  # BPM difference to flag as disagreement


@dataclass
class BpmResult:
    """Result from a single BPM estimator."""

    bpm: Optional[float] = None
    confidence: float = 0.0
    source: str = ""
    error: Optional[str] = None


@dataclass
class KeyResult:
    """Result from key estimation."""

    key: Optional[str] = None
    mode: Optional[str] = None
    confidence: float = 0.0
    error: Optional[str] = None


@dataclass
class AnalysisResult:
    """Cross-checked BPM result combining aubio and librosa."""

    bpm_aubio: Optional[float] = None
    bpm_librosa: Optional[float] = None
    bpm_final: Optional[float] = None
    confidence: float = 0.0
    disagreement: bool = False


def analyze_bpm_aubio(file_path: str) -> BpmResult:
    """Estimate BPM using aubio."""
    try:
        bpm = _aubio_tempo(file_path)
        return BpmResult(bpm=bpm, confidence=0.85, source="aubio")
    except Exception as exc:
        return BpmResult(source="aubio", error=str(exc))


def analyze_bpm_librosa(file_path: str) -> BpmResult:
    """Estimate BPM using librosa."""
    try:
        bpm = _librosa_tempo(file_path)
        return BpmResult(bpm=bpm, confidence=0.85, source="librosa")
    except Exception as exc:
        return BpmResult(source="librosa", error=str(exc))


def analyze_key(file_path: str) -> KeyResult:
    """Estimate musical key using librosa chroma features."""
    try:
        key, mode = _librosa_key(file_path)
        return KeyResult(key=key, mode=mode, confidence=0.7)
    except Exception as exc:
        return KeyResult(error=str(exc))


def cross_check_bpm(a: BpmResult, b: BpmResult) -> AnalysisResult:
    """Cross-check BPM estimates from two sources.

    Handles octave errors (half/double BPM) and flags true disagreements.
    """
    result = AnalysisResult(bpm_aubio=a.bpm, bpm_librosa=b.bpm)

    if a.bpm is None and b.bpm is None:
        return result

    if a.bpm is None:
        result.bpm_final = b.bpm
        result.confidence = b.confidence
        return result

    if b.bpm is None:
        result.bpm_final = a.bpm
        result.confidence = a.confidence
        return result

    # Check for octave error (half/double relationship)
    if _is_octave_related(a.bpm, b.bpm):
        result.bpm_final = max(a.bpm, b.bpm)  # prefer higher in octave ambiguity
        result.confidence = max(a.confidence, b.confidence)
        result.disagreement = False
        return result

    diff = abs(a.bpm - b.bpm)

    if diff <= BPM_DISAGREEMENT_THRESHOLD:
        result.bpm_final = (a.bpm + b.bpm) / 2.0
        result.confidence = min(a.confidence, b.confidence)
        result.disagreement = False
    else:
        # True disagreement -- use higher confidence source
        if a.confidence >= b.confidence:
            result.bpm_final = a.bpm
        else:
            result.bpm_final = b.bpm
        result.confidence = max(a.confidence, b.confidence) * 0.6
        result.disagreement = True

    return result


def _is_octave_related(bpm_a: float, bpm_b: float) -> bool:
    """Check if two BPM values are related by a factor of ~2 (octave error)."""
    if bpm_a <= 0 or bpm_b <= 0:
        return False
    ratio = max(bpm_a, bpm_b) / min(bpm_a, bpm_b)
    return abs(ratio - 2.0) < 0.1


def _aubio_tempo(file_path: str) -> float:
    """Run aubio tempo estimation. This is the real implementation; tests mock this."""
    import aubio
    win_s = 1024
    hop_s = 512
    samplerate = 0  # use file's native rate

    src = aubio.source(file_path, samplerate, hop_s)
    tempo = aubio.tempo("default", win_s, hop_s, src.samplerate)

    beats = []
    total_frames = 0
    while True:
        samples, read = src()
        is_beat = tempo(samples)
        if is_beat:
            beats.append(tempo.get_last_s())
        total_frames += read
        if read < hop_s:
            break

    bpm = tempo.get_bpm()
    return float(bpm)


def _get_cached_audio(file_path: str) -> tuple:
    """Get audio from cache or load it. Cache is per-file, single entry."""
    cached = getattr(_audio_cache, "entry", None)
    if cached is not None and cached[0] == file_path:
        return cached[1], cached[2]
    y, sr = _load_audio(file_path)
    _audio_cache.entry = (file_path, y, sr)
    return y, sr


def clear_audio_cache() -> None:
    """Clear the audio cache after processing a track."""
    _audio_cache.entry = None


def _load_audio(file_path: str, duration: float = 60.0) -> tuple:
    """Load audio once for reuse across BPM and key analysis.

    Limits to `duration` seconds to avoid loading multi-minute files entirely.
    Tests mock this function.
    """
    import librosa
    y, sr = librosa.load(file_path, sr=22050, mono=True, duration=duration)
    return y, sr


def _librosa_tempo_from_audio(y, sr) -> float:
    """Estimate BPM from pre-loaded audio arrays."""
    import librosa
    tempo = librosa.beat.tempo(y=y, sr=sr)
    if hasattr(tempo, '__len__'):
        return float(tempo[0])
    return float(tempo)


def _librosa_key_from_audio(y, sr) -> tuple[str, str]:
    """Estimate key from pre-loaded audio arrays."""
    import librosa
    import numpy as np
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    chroma_mean = np.mean(chroma, axis=1)

    key_names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    key_idx = int(np.argmax(chroma_mean))
    key_name = key_names[key_idx]

    # Simple major/minor detection
    major_profile = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
    minor_profile = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

    shifted_chroma = np.roll(chroma_mean, -key_idx)
    major_corr = float(np.corrcoef(shifted_chroma, major_profile)[0, 1])
    minor_corr = float(np.corrcoef(shifted_chroma, minor_profile)[0, 1])

    mode = "major" if major_corr >= minor_corr else "minor"
    return key_name, mode


def _librosa_tempo(file_path: str) -> float:
    """Run librosa tempo estimation. This is the real implementation; tests mock this."""
    y, sr = _get_cached_audio(file_path)
    return _librosa_tempo_from_audio(y, sr)


def _librosa_key(file_path: str) -> tuple[str, str]:
    """Estimate key using librosa chroma features. Tests mock this."""
    y, sr = _get_cached_audio(file_path)
    return _librosa_key_from_audio(y, sr)


@dataclass
class EnergyResult:
    """Result from energy/dynamics analysis."""

    energy: Optional[float] = None
    dynamic_range: Optional[float] = None
    onset_density: Optional[float] = None
    peak_rms: Optional[float] = None
    error: Optional[str] = None


def analyze_energy(file_path: str) -> EnergyResult:
    """Compute energy features from audio. Tests mock this function."""
    try:
        return _librosa_energy(file_path)
    except Exception as exc:
        return EnergyResult(error=str(exc))


def _librosa_energy(file_path: str) -> EnergyResult:
    """Compute energy features using cached audio. Tests mock this."""
    import librosa
    import numpy as np

    y, sr = _get_cached_audio(file_path)

    # RMS energy per frame
    rms = librosa.feature.rms(y=y)[0]
    mean_rms = float(np.mean(rms))
    peak_rms = float(np.max(rms))

    # Normalize energy to 0-1 range (RMS of a full-scale sine is ~0.707)
    energy = min(1.0, mean_rms / 0.707)

    # Dynamic range: ratio of peak to mean in dB
    if mean_rms > 0:
        dynamic_range = float(20.0 * np.log10(peak_rms / mean_rms))
    else:
        dynamic_range = 0.0

    # Onset density: onsets per second
    onsets = librosa.onset.onset_detect(y=y, sr=sr)
    duration = len(y) / sr
    onset_density = float(len(onsets) / duration) if duration > 0 else 0.0

    return EnergyResult(
        energy=energy,
        dynamic_range=dynamic_range,
        onset_density=onset_density,
        peak_rms=peak_rms,
    )


def analyze_bpm_and_key(file_path: str) -> tuple[BpmResult, KeyResult]:
    """Load audio once and compute both BPM and key. Main optimization entry point."""
    try:
        y, sr = _load_audio(file_path)
    except Exception as exc:
        return (
            BpmResult(source="librosa", error=str(exc)),
            KeyResult(error=str(exc)),
        )

    try:
        bpm = _librosa_tempo_from_audio(y, sr)
        bpm_result = BpmResult(bpm=bpm, confidence=0.85, source="librosa")
    except Exception as exc:
        bpm_result = BpmResult(source="librosa", error=str(exc))

    try:
        key, mode = _librosa_key_from_audio(y, sr)
        key_result = KeyResult(key=key, mode=mode, confidence=0.7)
    except Exception as exc:
        key_result = KeyResult(error=str(exc))

    return bpm_result, key_result