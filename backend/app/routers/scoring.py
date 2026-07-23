"""
Router: relativity-scoring triggering.

Mirrors routers/embedding.py exactly — same trigger/status/stop shape,
same reasoning (a scoring run over hundreds of articles is far too slow
for a single HTTP request, so it runs as a background task and is
polled for progress instead).
"""

from typing import Optional

from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel

from app import repository
from app.services import scoring_job

router = APIRouter()


class ScoringTriggerResponse(BaseModel):
    status: str  # "started" | "already_running" | "stopping" | "not_running"
    message: str


class ScoringStatusResponse(BaseModel):
    in_progress: bool
    total: int
    processed: int
    estimated_minutes_remaining: Optional[float] = None


@router.post("/articles/score", response_model=ScoringTriggerResponse)
def trigger_scoring(background_tasks: BackgroundTasks):
    """Starts a scoring run in the background. Safe to call repeatedly —
    if one's already running, this reports that instead of starting a
    second overlapping run."""
    if scoring_job.is_scoring_running():
        return ScoringTriggerResponse(
            status="already_running",
            message="A scoring run is already in progress. Reload in a bit to check progress.",
        )

    background_tasks.add_task(scoring_job.run_scoring_job)
    return ScoringTriggerResponse(
        status="started",
        message=(
            "Scoring started in the background. Reload the page in a "
            "minute or two to see progress — articles will show as "
            "'Pending' while being processed, then settle into "
            "'Scored'/'Failed'."
        ),
    )


@router.get("/articles/score/status", response_model=ScoringStatusResponse)
def get_scoring_run_status():
    """Live progress for the frontend to poll — same shape and same
    recency-weighted ETA as /articles/embed/status."""
    status = repository.get_scoring_status()
    if status is None:
        return ScoringStatusResponse(in_progress=False, total=0, processed=0)

    eta_minutes = None
    if status.in_progress and status.avg_seconds_per_article is not None:
        remaining_articles = max(status.total - status.processed, 0)
        eta_minutes = round((status.avg_seconds_per_article * remaining_articles) / 60, 1)

    return ScoringStatusResponse(
        in_progress=status.in_progress,
        total=status.total,
        processed=status.processed,
        estimated_minutes_remaining=eta_minutes,
    )


@router.post("/articles/score/stop", response_model=ScoringTriggerResponse)
def stop_scoring():
    """Requests that the current run stop after the article it's on right
    now finishes. See routers/embedding.py's stop_embedding() — identical
    reasoning."""
    if scoring_job.request_scoring_stop():
        return ScoringTriggerResponse(
            status="stopping",
            message=(
                "Stop requested. The current article will finish, then the "
                "run will end. Reload in a bit to confirm it stopped."
            ),
        )
    return ScoringTriggerResponse(
        status="not_running",
        message="No scoring run is currently in progress.",
    )
