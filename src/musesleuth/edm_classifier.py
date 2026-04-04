"""EDM subgenre classification using pre-trained deep learning model.

Wraps the Joint_ShortChunkCNN_Res late-fusion model from
https://github.com/ddman1101/EDM-subgenre-classifier

Pipeline: audio -> mel-spectrogram + tempograms -> slice -> chunk -> model -> majority vote
"""
from __future__ import annotations

import logging
import os
import sys
from collections import Counter
from pathlib import Path

import librosa
import numpy as np
import torch

# Register EDM model modules so torch.load can unpickle the saved classes.
# The pickle may reference "model", "modules", or "__main__" depending on how the
# original author saved the model. We cover all cases.
from musesleuth.edm_model import model as _edm_model_mod
from musesleuth.edm_model import modules as _edm_modules_mod

sys.modules.setdefault("model", _edm_model_mod)
sys.modules.setdefault("modules", _edm_modules_mod)

import __main__ as _main

for _cls_name in ("Joint_ShortChunkCNN_Res", "Joint_ShortChunkCNN_Res_early", "ShortChunkCNN_Res"):
    if hasattr(_edm_model_mod, _cls_name) and not hasattr(_main, _cls_name):
        setattr(_main, _cls_name, getattr(_edm_model_mod, _cls_name))
for _cls_name in ("Res_2d", "Conv_1d", "Conv_2d", "Res_2d_mp", "ResSE_1d"):
    if hasattr(_edm_modules_mod, _cls_name) and not hasattr(_main, _cls_name):
        setattr(_main, _cls_name, getattr(_edm_modules_mod, _cls_name))

logger = logging.getLogger(__name__)


def _install_gru_compat_patch() -> None:
    """Monkey-patch GRU so old pickles can deserialize on modern PyTorch.

    Must be called before torch.load -- the GRU.__setstate__ in new PyTorch
    assumes _flat_weights and proj_size exist, but old pickles lack them.
    """
    import torch.nn as nn

    _original_gru_setstate = getattr(nn.GRU, "__setstate__", None)

    def _patched_setstate(self: nn.GRU, state: dict) -> None:
        state = dict(state)
        if "_flat_weights" not in state or not isinstance(state.get("_flat_weights"), list):
            state["_flat_weights"] = []
        if "_flat_weights_names" not in state:
            state["_flat_weights_names"] = []
        if "proj_size" not in state:
            state["proj_size"] = 0
        self.__dict__.update(state)

    nn.GRU.__setstate__ = _patched_setstate


_install_gru_compat_patch()

EDM_GENRES = [
    "hard-dance",
    "deep-house",
    "glitch-hop",
    "progressive-big-room",
    "electro-big-room",
    "trap-future-bass",
    "jumpup-drum-and-bass",
    "progressive-trance",
    "uplifting-trance",
    "garage-baseline-grime",
    "electro-house",
    "house",
    "psy-trance",
    "leftfield-bass",
    "hip-hop-r-and-b",
    "tech-trance",
    "minimal-deep-tech",
    "progressive-house",
    "future-house",
    "tech-house",
    "leftfield-house-and-techno",
    "electronica-downtempo",
    "liquid-drum-and-bass",
    "breaks",
    "dance",
    "dubstep",
    "dub-techno",
    "reggae-dancehall-dub",
    "hardcore-hard-techno",
    "jungle-drum-and-bass",
]

GENRE_MAPPING = {
    "hard-dance": "hardcore",
    "deep-house": "deep_house",
    "glitch-hop": "electronic",
    "progressive-big-room": "big_room",
    "electro-big-room": "electro_house",
    "trap-future-bass": "trap",
    "jumpup-drum-and-bass": "drumnbass",
    "progressive-trance": "progressive_trance",
    "uplifting-trance": "uplifting_trance",
    "garage-baseline-grime": "garage",
    "electro-house": "electro_house",
    "house": "house",
    "psy-trance": "psy_trance",
    "leftfield-bass": "bass",
    "hip-hop-r-and-b": "hip-hop",
    "tech-trance": "tech_trance",
    "minimal-deep-tech": "minimal_techno",
    "progressive-house": "progressive_house",
    "future-house": "future_house",
    "tech-house": "tech_house",
    "leftfield-house-and-techno": "techno",
    "electronica-downtempo": "downtempo",
    "liquid-drum-and-bass": "liquid",
    "breaks": "breaks",
    "dance": "dance",
    "dubstep": "dubstep",
    "dub-techno": "dub_techno",
    "reggae-dancehall-dub": "reggae",
    "hardcore-hard-techno": "hardcore",
    "jungle-drum-and-bass": "jungle",
}

# Feature slicing constants (from original training pipeline)
_TEMPO_SLICE_START = 1292
_TEMPO_SLICE_END = 2584
_MEL_SLICE_END = 5168
_CHUNK_TEMPO = 50
_CHUNK_MEL = 200


def _patch_gru_compat(model: torch.nn.Module) -> None:
    """Fix GRU modules from old PyTorch pickles for modern PyTorch."""
    for _name, module in model.named_modules():
        if isinstance(module, torch.nn.GRU):
            if not hasattr(module, "proj_size"):
                module.proj_size = 0
            if not hasattr(module, "_flat_weights_names"):
                module._flat_weights_names = []
            if not hasattr(module, "_flat_weights"):
                module._flat_weights = []
            if not module._flat_weights_names:
                module._flat_weights_names = [
                    "weight_ih_l0", "weight_hh_l0", "bias_ih_l0", "bias_hh_l0",
                ]
                for layer in range(1, module.num_layers):
                    module._flat_weights_names.extend([
                        f"weight_ih_l{layer}", f"weight_hh_l{layer}",
                        f"bias_ih_l{layer}", f"bias_hh_l{layer}",
                    ])
            module._flat_weights = [
                getattr(module, wn) for wn in module._flat_weights_names
                if hasattr(module, wn)
            ]


def _find_model_path() -> str | None:
    """Search for the pretrained model in known locations."""
    env_path = os.environ.get("EDM_MODEL_PATH")
    if env_path and os.path.exists(env_path):
        return env_path

    search_roots = [
        Path(__file__).resolve().parent.parent.parent,  # MuseSleuth project root
        Path("C:/code/EDM-subgenre-classifier"),
    ]
    for root in search_roots:
        for name in ("model_pkl/joint-model-all-2500.pkl", "edm_model.pkl"):
            candidate = root / name
            if candidate.exists():
                return str(candidate)
    return None


class EDMClassifier:
    """EDM subgenre classifier using pre-trained PyTorch late-fusion model.

    The model expects ~2+ minutes of audio. It extracts mel-spectrograms and
    tempograms, slices them into aligned chunks, runs each through the CNN,
    and takes a majority vote for the final prediction.
    """

    def __init__(self, model_path: str | None = None):
        if model_path is None:
            model_path = _find_model_path()
        if model_path is None or not os.path.exists(model_path):
            raise FileNotFoundError(
                "EDM model not found. Set EDM_MODEL_PATH env var or place "
                "joint-model-all-2500.pkl in the expected location."
            )

        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        logger.info("Loading EDM model from %s (device=%s)", model_path, self.device)

        checkpoint = torch.load(model_path, map_location=self.device, weights_only=False)
        self.model = checkpoint.module if hasattr(checkpoint, "module") else checkpoint
        _patch_gru_compat(self.model)
        self.model = self.model.to(self.device)
        self.model.eval()

    def extract_features(self, file_path: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Extract mel-spectrogram and tempogram features from full audio."""
        y, _sr = librosa.load(file_path, sr=22050, mono=True)
        mel = librosa.feature.melspectrogram(y=y, sr=22050)
        oenv = librosa.onset.onset_strength(y=y, sr=22050, hop_length=512)
        a_tempo = librosa.feature.tempogram(onset_envelope=oenv, sr=22050, hop_length=512)
        f_tempo = librosa.feature.fourier_tempogram(onset_envelope=oenv, sr=22050, hop_length=512)
        # Original pipeline casts complex fourier tempogram to float32 (takes real part)
        f_tempo = f_tempo.real.astype("float32")
        return mel, a_tempo, f_tempo

    def _prepare_chunks(
        self, mel: np.ndarray, a_tempo: np.ndarray, f_tempo: np.ndarray,
    ) -> tuple[list[np.ndarray], list[np.ndarray], list[np.ndarray]] | None:
        """Slice, normalize, and chunk features into model-ready windows."""
        if a_tempo.shape[1] < _TEMPO_SLICE_END or mel.shape[1] < _MEL_SLICE_END:
            return None

        a = np.array(a_tempo[:, _TEMPO_SLICE_START:_TEMPO_SLICE_END], dtype="float32")
        f = np.array(f_tempo[:, _TEMPO_SLICE_START:_TEMPO_SLICE_END], dtype="float32")
        m = np.array(mel[:, :_MEL_SLICE_END], dtype="float32")

        a = (a - np.mean(a)) / (np.std(a) + 1e-8)
        f = (f - np.mean(f)) / (np.std(f) + 1e-8)
        m = (m - np.mean(m)) / (np.std(m) + 1e-8)

        if np.any(np.isnan(a)) or np.any(np.isnan(f)) or np.any(np.isnan(m)):
            return None

        n_chunks = min(
            a.shape[1] // _CHUNK_TEMPO,
            f.shape[1] // _CHUNK_TEMPO,
            m.shape[1] // _CHUNK_MEL,
        )
        if n_chunks == 0:
            return None

        a_out = [a[:, k * _CHUNK_TEMPO:(k + 1) * _CHUNK_TEMPO] for k in range(n_chunks)]
        f_out = [f[:, k * _CHUNK_TEMPO:(k + 1) * _CHUNK_TEMPO] for k in range(n_chunks)]
        m_out = [m[:, k * _CHUNK_MEL:(k + 1) * _CHUNK_MEL] for k in range(n_chunks)]
        return a_out, f_out, m_out

    def predict(self, file_path: str) -> dict:
        """Predict EDM subgenre for a track with chunk-level majority vote.

        Returns dict with genre_primary, genre_raw, confidence, vote_confidence, n_chunks.
        """
        mel, a_tempo, f_tempo = self.extract_features(file_path)
        chunks = self._prepare_chunks(mel, a_tempo, f_tempo)

        if chunks is None:
            return {
                "genre_primary": "unknown",
                "genre_raw": "unknown",
                "confidence": 0.0,
                "error": "Audio too short for EDM classification (need ~2 min)",
            }

        a_chunks, f_chunks, m_chunks = chunks
        chunk_preds: list[int] = []
        all_probs: list[np.ndarray] = []

        with torch.no_grad():
            for a, f, m in zip(a_chunks, f_chunks, m_chunks):
                t_a = torch.from_numpy(a).unsqueeze(0).to(self.device)         # (1, 384, 50)
                t_f = torch.from_numpy(f).unsqueeze(0).to(self.device)         # (1, 193, 50)
                t_m = torch.from_numpy(m).unsqueeze(0).unsqueeze(0).to(self.device)  # (1, 1, 128, 200)

                out = self.model(t_a, t_f, t_m)
                probs = out[0].cpu().numpy()
                all_probs.append(probs)
                chunk_preds.append(int(np.argmax(probs)))

        votes = Counter(chunk_preds)
        winner_idx = votes.most_common(1)[0][0]
        vote_conf = votes[winner_idx] / len(chunk_preds)
        avg_probs = np.mean(all_probs, axis=0)
        model_conf = float(avg_probs[winner_idx])

        genre_raw = EDM_GENRES[winner_idx] if winner_idx < len(EDM_GENRES) else "unknown"
        genre_mapped = GENRE_MAPPING.get(genre_raw, genre_raw)

        return {
            "genre_primary": genre_mapped,
            "genre_raw": genre_raw,
            "confidence": model_conf,
            "vote_confidence": vote_conf,
            "n_chunks": len(chunk_preds),
        }


_classifier: EDMClassifier | None = None


def get_edm_classifier() -> EDMClassifier | None:
    """Get or create the global EDM classifier instance (singleton)."""
    global _classifier
    if _classifier is None:
        try:
            _classifier = EDMClassifier()
        except FileNotFoundError:
            logger.warning("EDM classifier model not found -- genre classification disabled")
            return None
    return _classifier


def classify_edm_genre(file_path: str) -> dict:
    """Classify EDM genre for a track file. Safe to call -- returns unknown on any failure."""
    classifier = get_edm_classifier()
    if classifier is None:
        return {"genre_primary": "unknown", "genre_raw": "unknown", "confidence": 0.0}

    try:
        return classifier.predict(file_path)
    except Exception as e:
        logger.exception("EDM classification failed for %s", file_path)
        return {"genre_primary": "unknown", "genre_raw": "unknown", "confidence": 0.0, "error": str(e)}
