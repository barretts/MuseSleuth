"""Similarity graph construction and k-NN queries using embeddings."""
from __future__ import annotations

import sqlite3
from typing import Optional

import numpy as np

from musesleuth.timbre import deserialize_array


def build_similarity_graph(
    conn: sqlite3.Connection,
    k: int = 10,
    scope: str = "global",
    metric: str = "cosine",
) -> int:
    """Build a k-NN similarity graph from embeddings and persist to similarity_edges.

    Uses brute-force cosine distance (falls back from FAISS if unavailable).
    Clears existing edges for the given metric before inserting.

    Returns the number of edges inserted.
    """
    rows = conn.execute(
        """
        SELECT metadata_id, vector
        FROM embeddings
        WHERE scope = ?
        """,
        (scope,),
    ).fetchall()

    if len(rows) < 2:
        return 0

    # Load all embeddings into numpy arrays
    mids: list[str] = []
    vecs: list[np.ndarray] = []
    for row in rows:
        vec = deserialize_array(row["vector"])
        if vec is None:
            continue
        mids.append(row["metadata_id"])
        vecs.append(np.array(vec, dtype=np.float32))

    if len(mids) < 2:
        return 0

    mat = np.vstack(vecs)  # shape (n, dim)

    # Try FAISS first, fall back to brute-force
    neighbors = _faiss_knn(mat, k) or _brute_knn(mat, k)

    # Clear existing edges for this metric
    conn.execute(
        "DELETE FROM similarity_edges WHERE metric = ?",
        (metric,),
    )

    edge_count = 0
    for i, (nn_indices, nn_dists) in enumerate(neighbors):
        src_id = mids[i]
        for j_idx, dist in zip(nn_indices, nn_dists):
            if j_idx == i:
                continue  # skip self
            dst_id = mids[j_idx]
            conn.execute(
                """
                INSERT OR REPLACE INTO similarity_edges
                    (src_id, dst_id, metric, value, computed_at)
                VALUES (?, ?, ?, ?, datetime('now'))
                """,
                (src_id, dst_id, metric, float(dist)),
            )
            edge_count += 1

    conn.commit()
    return edge_count


def find_neighbors(
    conn: sqlite3.Connection,
    metadata_id: str,
    k: int = 10,
    metric: str = "cosine",
) -> list[dict]:
    """Query the k nearest neighbors of a track from the similarity_edges table.

    Returns a list of dicts with keys: metadata_id, value (distance), sorted ascending.
    """
    rows = conn.execute(
        """
        SELECT dst_id AS metadata_id, value
        FROM similarity_edges
        WHERE src_id = ? AND metric = ?
        ORDER BY value ASC
        LIMIT ?
        """,
        (metadata_id, metric, k),
    ).fetchall()

    return [{"metadata_id": r["metadata_id"], "value": r["value"]} for r in rows]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _faiss_knn(
    mat: np.ndarray, k: int
) -> Optional[list[tuple[list[int], list[float]]]]:
    """Try k-NN via FAISS. Returns None if FAISS is not available."""
    try:
        import faiss
    except ImportError:
        return None

    n, dim = mat.shape
    actual_k = min(k + 1, n)  # +1 because FAISS includes self

    # Normalize for cosine similarity (use inner product on L2-normalized vectors)
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    mat_norm = (mat / norms).astype(np.float32)

    index = faiss.IndexFlatIP(dim)
    index.add(mat_norm)
    sims, indices = index.search(mat_norm, actual_k)

    result = []
    for i in range(n):
        nn_idx = []
        nn_dist = []
        for j in range(actual_k):
            idx = int(indices[i, j])
            if idx == i:
                continue
            # Convert inner product similarity to cosine distance
            dist = 1.0 - float(sims[i, j])
            nn_idx.append(idx)
            nn_dist.append(max(0.0, dist))
        result.append((nn_idx[:k], nn_dist[:k]))

    return result


def _brute_knn(
    mat: np.ndarray, k: int
) -> list[tuple[list[int], list[float]]]:
    """Brute-force k-NN using cosine distance."""
    n, dim = mat.shape

    # Normalize
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    mat_norm = mat / norms

    # Cosine similarity matrix
    sim_matrix = mat_norm @ mat_norm.T  # (n, n)

    result = []
    for i in range(n):
        dists = 1.0 - sim_matrix[i]  # cosine distance
        dists[i] = float("inf")  # exclude self

        # Get top-k nearest (smallest distance)
        actual_k = min(k, n - 1)
        if actual_k <= 0:
            result.append(([], []))
            continue

        top_indices = np.argpartition(dists, actual_k)[:actual_k]
        top_indices = top_indices[np.argsort(dists[top_indices])]

        nn_idx = top_indices.tolist()
        nn_dist = [max(0.0, float(dists[j])) for j in top_indices]
        result.append((nn_idx, nn_dist))

    return result
