"""ML classification stage runner -- adds ML-based metadata for categorization and playlist curation."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from musesleuth.bpm_analyzer import clear_audio_cache
from musesleuth.edm_classifier import classify_edm_genre


@dataclass
class MLClassificationResult:
    """Result of the ML classification stage for a single track."""

    success: bool = True
    metadata_id: str = ""
    error: Optional[str] = None


def run_ml_classify_for_track(
    conn: sqlite3.Connection,
    metadata_id: str,
    file_path: str,
) -> MLClassificationResult:
    """Run ML classification for a single track.

    Uses audio features from musical_features table and performs additional
    audio analysis to compute:
    1. Genre classification (primary/secondary)
    2. Mood tags
    3. Danceability score
    4. Acoustic vs Electronic score
    5. Vocal type detection
    6. Era prediction
    7. Tempo category
    8. Energy category
    9. Quality assessment
    """
    path = Path(file_path)

    try:
        if not path.exists():
            return MLClassificationResult(
                success=False,
                metadata_id=metadata_id,
                error=f"File not found: {file_path}",
            )

        mf = conn.execute(
            """SELECT bpm_final, energy, dynamic_range, onset_density, peak_rms, key_name, key_mode
               FROM musical_features WHERE metadata_id = ?""",
            (metadata_id,),
        ).fetchone()

        if mf is None:
            return MLClassificationResult(
                success=False,
                metadata_id=metadata_id,
                error=f"No musical features found for {metadata_id}",
            )

        bpm = mf["bpm_final"] or 120.0
        energy = mf["energy"] or 0.5
        dynamic_range = mf["dynamic_range"] or 6.0
        onset_density = mf["onset_density"] or 2.0
        peak_rms = mf["peak_rms"] or 0.1
        key_name = mf["key_name"] or "C"
        key_mode = mf["key_mode"] or "major"

        spectral = _analyze_spectral_features(file_path)

        edm_result = classify_edm_genre(file_path)
        if edm_result and edm_result.get("confidence", 0) > 0.1:
            genre_result = {
                "primary": edm_result["genre_primary"],
                "secondary": edm_result.get("genre_raw", edm_result["genre_primary"]),
                "confidence": edm_result["confidence"],
            }
        else:
            genre_result = {"primary": "unknown", "secondary": "unknown", "confidence": 0.0}
        mood_tags = _detect_mood(bpm, energy, dynamic_range, spectral)
        danceability = _compute_danceability(bpm, energy, onset_density, spectral)
        acousticness = _compute_acousticness(spectral, dynamic_range)
        electronic_score = 1.0 - acousticness
        vocal_result = _detect_vocal_type(file_path, spectral)
        era_result = _predict_era(bpm, spectral, energy)
        tempo_cat = _categorize_tempo(bpm)
        energy_cat = _categorize_energy(energy)
        quality = _assess_quality(spectral, dynamic_range, peak_rms)

        conn.execute(
            """
            INSERT OR REPLACE INTO ml_features
                (metadata_id, genre_primary, genre_secondary, genre_confidence,
                 mood_tags, danceability, acousticness, electronic_score,
                 vocal_type, vocal_confidence, era_prediction, era_confidence,
                 tempo_category, energy_category, quality_score, quality_issues,
                 spectral_centroid, spectral_bandwidth, spectral_rolloff,
                 spectral_flatness, zero_crossing_rate,
                 mfcc_mean, mfcc_std, spectral_contrast,
                 harmonic_percussive_ratio,
                 classified_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                metadata_id,
                genre_result["primary"],
                genre_result["secondary"],
                genre_result["confidence"],
                json.dumps(mood_tags),
                danceability,
                acousticness,
                electronic_score,
                vocal_result["type"],
                vocal_result["confidence"],
                era_result["era"],
                era_result["confidence"],
                tempo_cat,
                energy_cat,
                quality["score"],
                json.dumps(quality["issues"]),
                spectral.get("spectral_centroid"),
                spectral.get("spectral_bandwidth"),
                spectral.get("spectral_rolloff"),
                spectral.get("spectral_flatness"),
                spectral.get("zero_crossing_rate"),
                json.dumps(spectral.get("mfcc_mean")),
                json.dumps(spectral.get("mfcc_std")),
                json.dumps(spectral.get("spectral_contrast")),
                spectral.get("harmonic_percussive_ratio"),
            ),
        )

        conn.commit()

        return MLClassificationResult(success=True, metadata_id=metadata_id)
    finally:
        clear_audio_cache()


_GPU_DEVICE = None
_GPU_TRANSFORMS = None


def _get_gpu_transforms():
    """Lazily initialise torchaudio transforms on GPU (singleton)."""
    global _GPU_DEVICE, _GPU_TRANSFORMS
    if _GPU_TRANSFORMS is not None:
        return _GPU_DEVICE, _GPU_TRANSFORMS

    import torch
    import torchaudio.transforms as T

    _GPU_DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    _GPU_TRANSFORMS = {
        "mfcc": T.MFCC(
            sample_rate=22050, n_mfcc=13,
            melkwargs={"n_fft": 2048, "n_mels": 128, "hop_length": 512},
        ).to(_GPU_DEVICE),
        "spectrogram": T.Spectrogram(
            n_fft=2048, hop_length=512, power=2.0,
        ).to(_GPU_DEVICE),
    }
    return _GPU_DEVICE, _GPU_TRANSFORMS


def _analyze_spectral_features(file_path: str, *, hpss: bool = False) -> dict:
    """Extract spectral and timbral features using GPU-accelerated torchaudio.

    Audio is loaded with librosa (CPU/disk), then feature extraction runs on GPU.
    Set hpss=True to also compute harmonic-percussive ratio (slow, CPU-only).
    """
    import librosa
    import numpy as np
    import torch

    _defaults = {
        "spectral_centroid": 0.0,
        "spectral_bandwidth": 0.0,
        "spectral_rolloff": 0.0,
        "spectral_flatness": 0.0,
        "zero_crossing_rate": 0.0,
        "tempo_librosa": 120.0,
        "mfcc_mean": [0.0] * 13,
        "mfcc_std": [0.0] * 13,
        "spectral_contrast": [0.0] * 7,
        "harmonic_percussive_ratio": None,
    }

    try:
        y, sr = librosa.load(file_path, sr=22050, mono=True, duration=30.0)
    except Exception:
        return _defaults

    try:
        tempo = librosa.beat.tempo(y=y, sr=sr)
        tempo_val = float(tempo[0]) if hasattr(tempo, "__len__") else float(tempo)
    except Exception:
        tempo_val = 120.0

    zcr = float(np.mean((y[:-1] * y[1:]) < 0))

    device, transforms = _get_gpu_transforms()
    waveform = torch.from_numpy(y).unsqueeze(0).to(device)

    # MFCCs on GPU
    try:
        mfcc = transforms["mfcc"](waveform).squeeze(0)
        mfcc_mean = [round(float(v), 4) for v in mfcc.mean(dim=1).cpu()]
        mfcc_std = [round(float(v), 4) for v in mfcc.std(dim=1).cpu()]
    except Exception:
        mfcc_mean = [0.0] * 13
        mfcc_std = [0.0] * 13

    # Power spectrogram on GPU -> derive centroid, bandwidth, rolloff, flatness
    try:
        spec = transforms["spectrogram"](waveform).squeeze(0)
        freqs = torch.linspace(0, 22050 / 2, spec.shape[0], device=device)

        spec_energy = spec.sum(dim=0, keepdim=True).clamp(min=1e-10)
        spec_norm = spec / spec_energy

        centroid = (spec_norm * freqs.unsqueeze(1)).sum(dim=0)
        spectral_centroid = float(centroid.mean().cpu())

        deviation = freqs.unsqueeze(1) - centroid.unsqueeze(0)
        bandwidth = torch.sqrt((spec_norm * deviation ** 2).sum(dim=0))
        spectral_bandwidth = float(bandwidth.mean().cpu())

        cumsum = spec.cumsum(dim=0)
        rolloff_mask = (cumsum / spec_energy) >= 0.85
        rolloff_idx = rolloff_mask.int().argmax(dim=0)
        spectral_rolloff = float(freqs[rolloff_idx].mean().cpu())

        log_spec = torch.log(spec + 1e-10)
        geo_mean = torch.exp(log_spec.mean(dim=0))
        arith_mean = spec.mean(dim=0).clamp(min=1e-10)
        spectral_flatness = float((geo_mean / arith_mean).mean().cpu())
    except Exception:
        spectral_centroid = 0.0
        spectral_bandwidth = 0.0
        spectral_rolloff = 0.0
        spectral_flatness = 0.0

    # Spectral contrast (librosa, CPU -- no torchaudio equivalent)
    try:
        sc = librosa.feature.spectral_contrast(y=y, sr=sr)
        spectral_contrast = [round(float(v), 4) for v in np.mean(sc, axis=1)]
    except Exception:
        spectral_contrast = [0.0] * 7

    # HPSS -- optional, CPU-only, expensive
    hp_ratio = None
    if hpss:
        try:
            y_harm, y_perc = librosa.effects.hpss(y)
            rms_h = float(np.sqrt(np.mean(y_harm ** 2)))
            rms_p = float(np.sqrt(np.mean(y_perc ** 2)))
            hp_ratio = round(rms_h / (rms_h + rms_p), 4) if (rms_h + rms_p) > 0 else 0.5
        except Exception:
            hp_ratio = 0.5

    return {
        "spectral_centroid": spectral_centroid,
        "spectral_bandwidth": spectral_bandwidth,
        "spectral_rolloff": spectral_rolloff,
        "spectral_flatness": spectral_flatness,
        "zero_crossing_rate": zcr,
        "tempo_librosa": tempo_val,
        "mfcc_mean": mfcc_mean,
        "mfcc_std": mfcc_std,
        "spectral_contrast": spectral_contrast,
        "harmonic_percussive_ratio": hp_ratio,
    }



def _detect_mood(bpm: float, energy: float, dynamic_range: float, spectral: dict) -> list[str]:
    """Detect mood tags based on audio features."""
    moods = []
    sc = spectral.get("spectral_centroid", 2000)

    # Use lower energy thresholds since librosa energy is often low
    if energy > 0.3 and bpm > 120:
        moods.append("energetic")
    if energy < 0.15:
        moods.append("calm")
        moods.append("relaxed")
    if dynamic_range > 10:
        moods.append("dynamic")
    if dynamic_range < 4:
        moods.append("monotoned")
    if sc > 5000:
        moods.append("bright")
    if sc < 2000:
        moods.append("dark")
    if bpm < 80:
        moods.append("melancholic")
    if bpm > 140:
        moods.append("uplifting")
    if bpm >= 125 and bpm <= 135:
        moods.append("danceable")

    if not moods:
        moods.append("neutral")

    return moods[:5]


def _compute_danceability(bpm: float, energy: float, onset_density: float, spectral: dict) -> float:
    """Compute danceability score (0-1)."""
    sfl = spectral.get("spectral_flatness", 0.1)

    bpm_score = 1.0 - abs(bpm - 125) / 125
    bpm_score = max(0.0, min(1.0, bpm_score))

    energy_score = energy

    onset_score = min(1.0, onset_density / 5.0)

    sfl_score = 1.0 - sfl

    danceability = (bpm_score * 0.3 + energy_score * 0.3 + onset_score * 0.25 + sfl_score * 0.15)
    return round(danceability, 3)


def _compute_acousticness(spectral: dict, dynamic_range: float) -> float:
    """Compute acousticness score (0-1)."""
    sfl = spectral.get("spectral_flatness", 0.1)
    sc = spectral.get("spectral_centroid", 2000)
    zcr = spectral.get("zero_crossing_rate", 0.05)

    sfl_score = min(1.0, sfl * 3)
    sc_score = 1.0 - min(1.0, sc / 8000)
    zcr_score = 1.0 - min(1.0, zcr * 10)
    dr_score = min(1.0, dynamic_range / 10)

    acousticness = (sfl_score * 0.3 + sc_score * 0.3 + zcr_score * 0.2 + dr_score * 0.2)
    return round(acousticness, 3)


def _detect_vocal_type(file_path: str, spectral: dict) -> dict:
    """Detect vocal type (vocal, instrumental, spoken)."""
    sfl = spectral.get("spectral_flatness", 0.1)
    sc = spectral.get("spectral_centroid", 2000)
    zcr = spectral.get("zero_crossing_rate", 0.05)

    instrumental_score = sfl * 0.5 + (1.0 - min(1.0, sc / 5000)) * 0.3 + zcr * 0.2

    if sfl > 0.35:
        return {"type": "instrumental", "confidence": min(0.9, sfl * 2)}
    if zcr > 0.15 and sfl > 0.2:
        return {"type": "spoken", "confidence": min(0.8, zcr * 5)}

    return {"type": "vocal", "confidence": 0.7}


def _predict_era(bpm: float, spectral: dict, energy: float) -> dict:
    """Predict era/decade of the track."""
    sc = spectral.get("spectral_centroid", 2000)

    if sc > 6000 and energy > 0.7:
        return {"era": "2020s", "confidence": 0.65}
    if sc > 4500 and energy > 0.5:
        return {"era": "2010s", "confidence": 0.6}
    if sc > 3500:
        return {"era": "2000s", "confidence": 0.55}
    if sc > 2500:
        return {"era": "1990s", "confidence": 0.5}
    if sc < 2000 and energy < 0.5:
        return {"era": "1980s", "confidence": 0.5}

    return {"era": "unknown", "confidence": 0.3}


def _categorize_tempo(bpm: float) -> str:
    """Categorize tempo."""
    if bpm < 80:
        return "slow"
    if bpm < 100:
        return "moderate"
    if bpm < 120:
        return "mid-tempo"
    if bpm < 140:
        return "upbeat"
    if bpm < 170:
        return "fast"
    return "very-fast"


def _categorize_energy(energy: float) -> str:
    """Categorize energy level."""
    if energy < 0.2:
        return "very-low"
    if energy < 0.4:
        return "low"
    if energy < 0.6:
        return "medium"
    if energy < 0.8:
        return "high"
    return "very-high"


def _assess_quality(spectral: dict, dynamic_range: float, peak_rms: float) -> dict:
    """Assess audio quality and identify issues."""
    issues = []
    score = 0.8

    sc = spectral.get("spectral_centroid", 2000)
    sfl = spectral.get("spectral_flatness", 0.1)
    zcr = spectral.get("zero_crossing_rate", 0.05)

    if dynamic_range < 3:
        issues.append("low_dynamic_range")
        score -= 0.2
    elif dynamic_range > 15:
        issues.append("high_dynamic_range")
        score -= 0.1

    if peak_rms > 0.95:
        issues.append("clipping_detected")
        score -= 0.3
    elif peak_rms > 0.9:
        issues.append("near_clipping")
        score -= 0.15

    if sfl > 0.6:
        issues.append("noise_floor_detected")
        score -= 0.15

    if zcr > 0.25:
        issues.append("high_noise")
        score -= 0.1

    if sc < 500:
        issues.append("muffled_audio")
        score -= 0.15

    score = max(0.0, min(1.0, score))

    return {"score": round(score, 3), "issues": issues}
