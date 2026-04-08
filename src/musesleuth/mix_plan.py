"""Mix plan generation: cue points, transition types, and serialization."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field, asdict
from typing import Optional

from musesleuth.transition import load_track_features, transition_cost
from musesleuth.playlist_generator import camelot_compatible


@dataclass
class MixPlanEntry:
    """One entry in a mix plan, representing a track with cue/transition info."""

    metadata_id: str = ""
    position: int = 0
    cue_in_s: Optional[float] = None
    cue_out_s: Optional[float] = None
    transition_type: str = "cut"
    transition_cost: float = 0.0
    bpm: Optional[float] = None
    camelot_key: Optional[str] = None


def generate_mix_plan(
    conn: sqlite3.Connection,
    track_ids: list[str],
) -> list[MixPlanEntry]:
    """Generate a mix plan with cue points and transition annotations.

    For each track, reads structure segments to determine cue-in (end of intro)
    and cue-out (start of outro). Adjacent pairs get transition type and cost.

    Returns a list of MixPlanEntry in playlist order.
    """
    if not track_ids:
        return []

    plan: list[MixPlanEntry] = []

    for i, mid in enumerate(track_ids):
        feat = load_track_features(conn, mid)

        # Read structure segments for cue points
        cue_in, cue_out = _get_cue_points(conn, mid)

        # Compute transition cost relative to previous track
        cost = 0.0
        t_type = "none" if i == 0 else "cut"

        if i > 0:
            prev_feat = load_track_features(conn, track_ids[i - 1])
            if feat and prev_feat:
                cost = transition_cost(prev_feat, feat)
                # Determine transition type based on compatibility
                t_type = _determine_transition_type(prev_feat, feat)

        entry = MixPlanEntry(
            metadata_id=mid,
            position=i,
            cue_in_s=cue_in,
            cue_out_s=cue_out,
            transition_type=t_type,
            transition_cost=cost,
            bpm=feat.bpm if feat else None,
            camelot_key=feat.camelot_key if feat else None,
        )
        plan.append(entry)

    return plan


def serialize_mix_plan(plan: list[MixPlanEntry]) -> str:
    """Serialize a mix plan to a JSON string."""
    return json.dumps([asdict(entry) for entry in plan], indent=2)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_cue_points(
    conn: sqlite3.Connection,
    metadata_id: str,
) -> tuple[Optional[float], Optional[float]]:
    """Read intro end and outro start from structure_segments.

    Returns (cue_in_s, cue_out_s). cue_in is the end of the intro segment,
    cue_out is the start of the outro segment.
    """
    cue_in: Optional[float] = None
    cue_out: Optional[float] = None

    intro = conn.execute(
        "SELECT end_s FROM structure_segments WHERE metadata_id = ? AND kind = 'intro' LIMIT 1",
        (metadata_id,),
    ).fetchone()
    if intro:
        cue_in = intro["end_s"]

    outro = conn.execute(
        "SELECT start_s FROM structure_segments WHERE metadata_id = ? AND kind = 'outro' LIMIT 1",
        (metadata_id,),
    ).fetchone()
    if outro:
        cue_out = outro["start_s"]

    # If beatgrid phrase boundaries are present, snap cue points to phrase edges.
    row = conn.execute(
        "SELECT phrase_boundaries_json FROM beat_grids WHERE metadata_id = ?",
        (metadata_id,),
    ).fetchone()
    if row and row["phrase_boundaries_json"]:
        try:
            phrase_boundaries = json.loads(row["phrase_boundaries_json"])
            if isinstance(phrase_boundaries, list):
                cue_in = _snap_to_phrase_boundary(cue_in, phrase_boundaries, direction="ceil")
                cue_out = _snap_to_phrase_boundary(cue_out, phrase_boundaries, direction="floor")
        except Exception:
            pass

    return cue_in, cue_out


def _snap_to_phrase_boundary(
    cue: Optional[float],
    boundaries: list[float],
    *,
    direction: str,
) -> Optional[float]:
    """Snap cue to nearest phrase boundary in requested direction."""
    if cue is None or not boundaries:
        return cue

    vals = [float(v) for v in boundaries]

    if direction == "ceil":
        candidates = [v for v in vals if v >= cue]
        return min(candidates) if candidates else cue

    if direction == "floor":
        candidates = [v for v in vals if v <= cue]
        return max(candidates) if candidates else cue

    return cue


def _determine_transition_type(prev_feat, cur_feat) -> str:
    """Determine transition type between two tracks.

    "blend" if BPM is close and keys are compatible, otherwise "cut".
    """
    bpm_close = False
    key_compat = False

    if prev_feat.bpm is not None and cur_feat.bpm is not None:
        bpm_close = abs(prev_feat.bpm - cur_feat.bpm) <= 6.0

    if prev_feat.camelot_key and cur_feat.camelot_key:
        key_compat = camelot_compatible(prev_feat.camelot_key, cur_feat.camelot_key)

    if bpm_close and key_compat:
        return "blend"
    return "cut"
