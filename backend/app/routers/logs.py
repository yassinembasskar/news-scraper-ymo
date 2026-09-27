"""
Router: pipeline run-history logs.

Every stage already writes one permanent record per run to its own
collection (scrape_runs, title_gate_runs, clustering_runs) — see the
various log_*_run() functions in repository.py. This endpoint merges all
of them into one time-ordered list so the frontend's Logs page can show
the full time-spent-per-phase picture across the whole pipeline in one
place, instead of three separate collections. Insight generation isn't a
separate collection here — it's preprocessing's own phase 6, so its
history is already inside "preprocessing" entries' phase_durations.
"""

from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from app import repository

router = APIRouter()


class RunLogEntry(BaseModel):
    stage: str  # "scrape" | "title_gate" | "preprocessing"
    status: str
    message: str
    duration_seconds: Optional[float] = None
    # Only ever present on "preprocessing" entries — the other stages are
    # single-phase, so their overall duration_seconds already says it all.
    phase_durations: Optional[Dict[str, float]] = None
    logged_at: datetime


@router.get("/logs", response_model=List[RunLogEntry])
def get_logs(limit: int = 100):
    """Most recent `limit` runs across every stage, newest first."""
    return [RunLogEntry(**entry) for entry in repository.get_recent_run_logs(limit)]
