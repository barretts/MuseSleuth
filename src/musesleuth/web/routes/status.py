"""Pipeline status API route."""
from __future__ import annotations

from fastapi import APIRouter, Request

from musesleuth.job_queue import STAGES, get_job_counts
from musesleuth.pipeline import PipelineOrchestrator

router = APIRouter()


@router.get("/status")
async def pipeline_status(request: Request):
    """Return pipeline status metrics."""
    db = request.app.state.db

    orch = PipelineOrchestrator(db)
    stats = orch.get_stats()
    stage_counts = get_job_counts(db)

    stages_data = []
    for stage in STAGES:
        counts = stage_counts.get(stage, {})
        stages_data.append({
            "name": stage,
            "pending": counts.get("pending", 0),
            "running": counts.get("running", 0),
            "done": counts.get("done", 0),
            "failed": counts.get("failed", 0),
        })

    return {"stats": vars(stats), "stages": stages_data}
