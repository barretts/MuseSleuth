"""Evaluation & metrics for DJ playlist quality assessment."""
from __future__ import annotations

import sqlite3

from musesleuth.transition import load_track_features, transition_cost
from musesleuth.playlist_generator import camelot_compatible


def compute_playlist_metrics(
    conn: sqlite3.Connection,
    track_ids: list[str],
) -> dict:
    """Compute quality metrics for a DJ playlist.

    Returns a dict with:
      - track_count
      - total_transition_cost
      - mean_transition_cost
      - max_transition_cost
      - bpm_range  (max - min BPM)
      - key_compatibility_pct  (% of adjacent pairs that are Camelot-compatible)
      - energy_smoothness  (mean absolute energy delta between adjacent tracks)
    """
    n = len(track_ids)
    if n == 0:
        return _empty_metrics()

    # Load features
    features = []
    for mid in track_ids:
        feat = load_track_features(conn, mid)
        features.append(feat)

    # Transition costs
    costs: list[float] = []
    for i in range(n - 1):
        if features[i] and features[i + 1]:
            costs.append(transition_cost(features[i], features[i + 1]))
        else:
            costs.append(0.0)

    total_cost = sum(costs)
    mean_cost = total_cost / len(costs) if costs else 0.0
    max_cost = max(costs) if costs else 0.0

    # BPM range
    bpms = [f.bpm for f in features if f and f.bpm is not None]
    bpm_range = (max(bpms) - min(bpms)) if bpms else 0.0

    # Key compatibility percentage
    key_compat_count = 0
    key_pair_count = 0
    for i in range(n - 1):
        if features[i] and features[i + 1]:
            k1 = features[i].camelot_key
            k2 = features[i + 1].camelot_key
            if k1 and k2:
                key_pair_count += 1
                if camelot_compatible(k1, k2):
                    key_compat_count += 1
    key_compat_pct = (key_compat_count / key_pair_count * 100.0) if key_pair_count > 0 else 0.0

    # Energy smoothness (mean absolute delta)
    energies = [f.energy for f in features if f and f.energy is not None]
    if len(energies) >= 2:
        energy_deltas = [abs(energies[i + 1] - energies[i]) for i in range(len(energies) - 1)]
        energy_smoothness = sum(energy_deltas) / len(energy_deltas)
    else:
        energy_smoothness = 0.0

    return {
        "track_count": n,
        "total_transition_cost": total_cost,
        "mean_transition_cost": mean_cost,
        "max_transition_cost": max_cost,
        "bpm_range": bpm_range,
        "key_compatibility_pct": key_compat_pct,
        "energy_smoothness": energy_smoothness,
    }


def compare_playlists(
    conn: sqlite3.Connection,
    track_ids_a: list[str],
    track_ids_b: list[str],
) -> dict:
    """Compare two playlists side-by-side.

    Returns a dict with playlist_a, playlist_b metrics and lower_cost indicator.
    """
    metrics_a = compute_playlist_metrics(conn, track_ids_a)
    metrics_b = compute_playlist_metrics(conn, track_ids_b)

    lower_cost = "a" if metrics_a["total_transition_cost"] <= metrics_b["total_transition_cost"] else "b"

    return {
        "playlist_a": metrics_a,
        "playlist_b": metrics_b,
        "lower_cost": lower_cost,
    }


def _empty_metrics() -> dict:
    return {
        "track_count": 0,
        "total_transition_cost": 0.0,
        "mean_transition_cost": 0.0,
        "max_transition_cost": 0.0,
        "bpm_range": 0.0,
        "key_compatibility_pct": 0.0,
        "energy_smoothness": 0.0,
    }
