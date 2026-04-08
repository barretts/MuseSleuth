"""DJ playlist optimizer: greedy nearest-neighbor + 2-opt local refinement."""
from __future__ import annotations

import sqlite3
from typing import Optional

from musesleuth.db import generate_metadata_id
from musesleuth.playlist_generator import PlaylistStrategy, PlaylistResult, _save_playlist
from musesleuth.transition import transition_cost, load_track_features, TrackFeatures


def greedy_order(
    conn: sqlite3.Connection,
    candidate_ids: list[str],
    seed_id: Optional[str] = None,
) -> list[str]:
    """Order candidates using greedy nearest-neighbor on transition cost.

    Starts from seed_id (or first candidate) and greedily picks the
    lowest-cost next track until all candidates are placed.
    """
    if not candidate_ids:
        return []
    if len(candidate_ids) == 1:
        return list(candidate_ids)

    # Pre-load features for all candidates
    features: dict[str, TrackFeatures] = {}
    for mid in candidate_ids:
        feat = load_track_features(conn, mid)
        if feat is not None:
            features[mid] = feat
        else:
            features[mid] = TrackFeatures()

    # Start from seed or first candidate
    if seed_id and seed_id in features:
        current = seed_id
    else:
        current = candidate_ids[0]

    remaining = set(candidate_ids)
    remaining.discard(current)
    path = [current]

    while remaining:
        current_feat = features[current]
        best_mid = None
        best_cost = float("inf")

        for mid in remaining:
            cost = transition_cost(current_feat, features[mid])
            if cost < best_cost:
                best_cost = cost
                best_mid = mid

        if best_mid is None:
            break

        path.append(best_mid)
        remaining.discard(best_mid)
        current = best_mid

    return path


def total_path_cost(
    conn: sqlite3.Connection,
    path: list[str],
) -> float:
    """Compute the total transition cost along a path of track IDs."""
    if len(path) <= 1:
        return 0.0

    features: dict[str, TrackFeatures] = {}
    for mid in path:
        feat = load_track_features(conn, mid)
        features[mid] = feat if feat is not None else TrackFeatures()

    total = 0.0
    for i in range(len(path) - 1):
        total += transition_cost(features[path[i]], features[path[i + 1]])
    return total


def two_opt_refine(
    conn: sqlite3.Connection,
    path: list[str],
    max_iterations: int = 100,
) -> list[str]:
    """Improve a path using 2-opt local search.

    Repeatedly reverses sub-segments if doing so reduces total cost.
    """
    if len(path) <= 2:
        return list(path)

    # Pre-load features
    features: dict[str, TrackFeatures] = {}
    for mid in path:
        feat = load_track_features(conn, mid)
        features[mid] = feat if feat is not None else TrackFeatures()

    def _pair_cost(a: str, b: str) -> float:
        return transition_cost(features[a], features[b])

    best = list(path)
    improved = True
    iteration = 0

    while improved and iteration < max_iterations:
        improved = False
        iteration += 1
        for i in range(1, len(best) - 1):
            for j in range(i + 1, len(best)):
                # Cost of current edges: (i-1,i) + (j,j+1 if exists)
                old_cost = _pair_cost(best[i - 1], best[i])
                if j + 1 < len(best):
                    old_cost += _pair_cost(best[j], best[j + 1])

                # Cost after reversing segment [i..j]
                new_cost = _pair_cost(best[i - 1], best[j])
                if j + 1 < len(best):
                    new_cost += _pair_cost(best[i], best[j + 1])

                if new_cost < old_cost - 1e-9:
                    best[i:j + 1] = reversed(best[i:j + 1])
                    improved = True

    return best


class DjFlowPlaylist(PlaylistStrategy):
    """DJ-style playlist: filter, greedy order, 2-opt refine."""

    strategy_name = "dj_flow"

    def generate(
        self,
        conn: sqlite3.Connection,
        name: str,
        params: dict,
        *,
        limit: int = 50,
        description: Optional[str] = None,
    ) -> PlaylistResult:
        seed_id = params.get("seed_id")

        # Fetch eligible tracks (quality_verdict = 'pass' or NULL)
        rows = conn.execute(
            """
            SELECT t.metadata_id
            FROM tracks t
            JOIN playlist_signals ps ON ps.metadata_id = t.metadata_id
            WHERE ps.quality_verdict IS NULL OR ps.quality_verdict = 'pass'
            """
        ).fetchall()

        candidate_ids = [r["metadata_id"] for r in rows]

        if not candidate_ids:
            pid = generate_metadata_id()
            return _save_playlist(conn, pid, name, description, self.strategy_name, params, [])

        # Limit candidates before optimization (for perf)
        if len(candidate_ids) > limit:
            candidate_ids = candidate_ids[:limit]

        # Greedy order
        ordered = greedy_order(conn, candidate_ids, seed_id=seed_id)

        # 2-opt refinement
        refined = two_opt_refine(conn, ordered, max_iterations=50)

        pid = generate_metadata_id()
        return _save_playlist(conn, pid, name, description, self.strategy_name, params, refined)
