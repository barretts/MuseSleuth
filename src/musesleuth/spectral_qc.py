"""Spectrogram-derived quality analysis used by analyze/backfill stages."""
from __future__ import annotations

from pathlib import Path


def analyze_spectrogram_quality(file_path: str, output_path: str | None = None) -> dict:
    """Compute spectrogram quality metrics and optionally render a grayscale image.

    Returns a dict with:
      - lowpass_cutoff_hz: estimated frequency containing ~99.5% of spectral energy
      - silence_ratio: ratio of frames with very low RMS energy
      - clipping_ratio: ratio of samples at/near digital full scale
      - spectrogram_path: output path if rendered, else None
    """
    defaults = {
        "lowpass_cutoff_hz": None,
        "silence_ratio": None,
        "clipping_ratio": None,
        "spectrogram_path": None,
    }

    try:
        import librosa
        import numpy as np
    except Exception:
        return defaults

    try:
        y, sr = librosa.load(file_path, sr=22050, mono=True, duration=45.0)
    except Exception:
        return defaults

    if y is None or len(y) == 0:
        return defaults

    abs_y = np.abs(y)
    clipping_ratio = float(np.mean(abs_y >= 0.999))

    try:
        rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=512)[0]
        if rms.size:
            silence_threshold = max(1e-5, float(np.max(rms)) * 0.05)
            silence_ratio = float(np.mean(rms <= silence_threshold))
        else:
            silence_ratio = 0.0
    except Exception:
        silence_ratio = 0.0

    try:
        spec = np.abs(librosa.stft(y=y, n_fft=2048, hop_length=512))
        spec_power = spec.mean(axis=1)
        total = float(spec_power.sum())
        if total > 0:
            cum = np.cumsum(spec_power) / total
            freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
            idx = int(np.searchsorted(cum, 0.995, side="left"))
            idx = max(0, min(idx, len(freqs) - 1))
            lowpass_cutoff_hz = float(freqs[idx])
        else:
            lowpass_cutoff_hz = 0.0
    except Exception:
        lowpass_cutoff_hz = None

    rendered_path: str | None = None
    if output_path:
        try:
            mel = librosa.feature.melspectrogram(
                y=y,
                sr=sr,
                n_fft=2048,
                hop_length=512,
                n_mels=128,
                fmax=sr / 2,
                power=2.0,
            )
            db = librosa.power_to_db(mel, ref=np.max)
            norm = np.clip((db + 80.0) / 80.0, 0.0, 1.0)

            target_w = 256
            if norm.shape[1] > target_w:
                idx = np.linspace(0, norm.shape[1] - 1, target_w).astype(int)
                norm = norm[:, idx]

            img = (norm * 255.0).astype(np.uint8)
            img = img[::-1, :]  # low -> high frequency bottom->top
            out = Path(output_path)
            out.parent.mkdir(parents=True, exist_ok=True)

            # Portable Graymap (P5) keeps deps minimal.
            h, w = img.shape
            with out.open("wb") as f:
                f.write(f"P5\n{w} {h}\n255\n".encode("ascii"))
                f.write(img.tobytes())
            rendered_path = str(out)
        except Exception:
            rendered_path = None

    return {
        "lowpass_cutoff_hz": lowpass_cutoff_hz,
        "silence_ratio": silence_ratio,
        "clipping_ratio": clipping_ratio,
        "spectrogram_path": rendered_path,
    }

