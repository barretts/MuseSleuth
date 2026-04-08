"""Deep audio embedding extraction using PANNs (PyTorch) with librosa fallback."""
from __future__ import annotations

import io
import logging
import sqlite3
from pathlib import Path
from typing import Optional

import numpy as np

from musesleuth.timbre import serialize_array

log = logging.getLogger(__name__)


def compute_embedding(
    file_path: str,
    scope: str = "global",
    intro_s: float = 30.0,
    outro_s: float = 30.0,
    sr: int = 22050,
    duration: float = 120.0,
) -> Optional[np.ndarray]:
    """Compute an audio embedding for a file.

    Args:
        file_path: Path to audio file.
        scope: "global" (full track), "intro" (first intro_s seconds),
               or "outro" (last outro_s seconds).
        intro_s: Duration of intro segment in seconds.
        outro_s: Duration of outro segment in seconds.
        sr: Target sample rate.
        duration: Max duration to load in seconds.

    Returns:
        1-D numpy array embedding, or None on failure.
    """
    path = Path(file_path)
    if not path.exists():
        log.warning("embed: file not found: %s", file_path)
        return None

    # Load audio
    log.debug("embed: loading %s scope=%s sr=%d dur=%.0fs", file_path, scope, sr, duration)
    y = _load_audio(file_path, sr=sr, duration=duration)
    if y is None or len(y) == 0:
        log.warning("embed: failed to load audio: %s", file_path)
        return None

    return _compute_embedding_from_audio(
        y,
        sr,
        scope=scope,
        intro_s=intro_s,
        outro_s=outro_s,
        file_path=file_path,
    )


def _compute_embedding_from_audio(
    y: np.ndarray,
    sr: int,
    scope: str,
    intro_s: float,
    outro_s: float,
    file_path: str,
) -> Optional[np.ndarray]:
    """Compute an embedding from already-decoded audio for a requested scope."""
    # Slice to scope
    y = _slice_scope(y, sr, scope, intro_s, outro_s)
    if y is None or len(y) == 0:
        log.warning("embed: empty after slicing scope=%s: %s", scope, file_path)
        return None

    # Try PANNs first, fall back to MFCCs
    vec = _panns_embedding(y, sr)
    if vec is None:
        vec = _mfcc_embedding(y, sr)
        if vec is not None:
            log.debug("embed: %s scope=%s mfcc_stat dim=%d", file_path, scope, len(vec))
    else:
        log.debug("embed: %s scope=%s panns dim=%d", file_path, scope, len(vec))

    if vec is None:
        log.warning("embed: all backends failed for %s scope=%s", file_path, scope)

    return vec


def run_embedding_for_track(
    conn: sqlite3.Connection,
    metadata_id: str,
    file_path: str,
) -> bool:
    """Compute and store global, intro, and outro embeddings for a track.

    Returns True on success, False on failure.
    """
    log.debug("run_embedding_for_track: mid=%s path=%s", metadata_id, file_path)
    path = Path(file_path)
    if not path.exists():
        log.warning("run_embedding_for_track: file not found mid=%s path=%s", metadata_id, file_path)
        return False

    y = _load_audio(file_path, sr=22050, duration=120.0)
    if y is None or len(y) == 0:
        log.warning("run_embedding_for_track: failed to load audio mid=%s path=%s", metadata_id, file_path)
        return False

    scopes = ["global", "intro", "outro"]
    any_success = False
    model = _detect_model()

    for scope in scopes:
        vec = _compute_embedding_from_audio(
            y,
            22050,
            scope=scope,
            intro_s=30.0,
            outro_s=30.0,
            file_path=file_path,
        )
        if vec is None:
            continue

        blob = serialize_array(vec.tolist())

        # Use INSERT OR REPLACE with the UNIQUE(metadata_id, model, scope) constraint
        # First delete any existing row to avoid AUTOINCREMENT issues
        conn.execute(
            "DELETE FROM embeddings WHERE metadata_id = ? AND model = ? AND scope = ?",
            (metadata_id, model, scope),
        )
        conn.execute(
            """
            INSERT INTO embeddings
                (metadata_id, model, scope, dim, vector, computed_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            """,
            (metadata_id, model, scope, len(vec), blob),
        )
        any_success = True

    if any_success:
        conn.commit()
        log.debug("run_embedding_for_track: mid=%s stored embeddings", metadata_id)
    else:
        log.warning("run_embedding_for_track: all scopes failed mid=%s path=%s", metadata_id, file_path)
    return any_success


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_audio(
    file_path: str, sr: int = 22050, duration: float = 120.0
) -> Optional[np.ndarray]:
    """Load audio as mono float32 numpy array."""
    try:
        import librosa
        y, _ = librosa.load(file_path, sr=sr, mono=True, duration=duration)
        return y
    except Exception:
        return None


def _slice_scope(
    y: np.ndarray,
    sr: int,
    scope: str,
    intro_s: float,
    outro_s: float,
) -> Optional[np.ndarray]:
    """Slice audio array to the requested scope."""
    duration_s = len(y) / sr

    if scope == "intro":
        end = min(intro_s, duration_s)
        return y[: int(end * sr)]
    elif scope == "outro":
        start = max(0, duration_s - outro_s)
        return y[int(start * sr) :]
    else:  # global
        return y


def _detect_model() -> str:
    """Return the model name that will be used for embeddings."""
    try:
        import torch
        return "panns_cnn14"
    except ImportError:
        return "mfcc_stat"


def _panns_embedding(y: np.ndarray, sr: int) -> Optional[np.ndarray]:
    """Try to compute embedding via PANNs Cnn14.

    PANNs requires a pre-downloaded checkpoint. If not available,
    returns None so the caller falls back to MFCC-based embedding.
    """
    try:
        import torch
        import torchaudio
    except ImportError:
        return None

    model_dir = Path.home() / ".musesleuth" / "models"
    checkpoint_path = model_dir / "Cnn14_mAP=0.431.pth"

    if not checkpoint_path.exists():
        # Model not downloaded yet -- fall back
        return None

    try:
        # Resample to 32 kHz as PANNs expects
        if sr != 32000:
            waveform = torch.from_numpy(y).unsqueeze(0).float()
            resampler = torchaudio.transforms.Resample(orig_freq=sr, new_freq=32000)
            waveform = resampler(waveform)
        else:
            waveform = torch.from_numpy(y).unsqueeze(0).float()

        # Load model (lazy, cached)
        model = _get_panns_model(checkpoint_path)
        model.eval()

        with torch.no_grad():
            output = model(waveform)
            # PANNs Cnn14 outputs embedding of shape (batch, 2048)
            if "embedding" in output:
                emb = output["embedding"].squeeze(0).numpy()
            else:
                return None

        return emb.astype(np.float32)
    except Exception:
        return None


_panns_model_cache: dict = {}


def _get_panns_model(checkpoint_path: Path):
    """Load and cache PANNs model."""
    key = str(checkpoint_path)
    if key not in _panns_model_cache:
        import torch
        # Lazy import of PANNs model definition
        # This requires the checkpoint to include model architecture
        checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
        # Depending on checkpoint format, adapt loading
        _panns_model_cache[key] = checkpoint
    return _panns_model_cache[key]


def _mfcc_embedding(y: np.ndarray, sr: int, n_mfcc: int = 40) -> Optional[np.ndarray]:
    """Fallback embedding: statistical summary of MFCCs.

    Produces a fixed-size vector by concatenating mean, std, min, max
    of n_mfcc MFCCs -> 4 * n_mfcc = 160 dimensions.
    Deterministic and always available via librosa.
    """
    try:
        import librosa
        mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=n_mfcc)

        mean = np.mean(mfcc, axis=1)
        std = np.std(mfcc, axis=1)
        mn = np.min(mfcc, axis=1)
        mx = np.max(mfcc, axis=1)

        vec = np.concatenate([mean, std, mn, mx]).astype(np.float32)
        return vec
    except Exception:
        return None
