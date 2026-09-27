"""
Router: title-gate triggering, status, and stop.

Kicks off one title-gate pass as a background task and returns
immediately — mirrors routers/extraction.py exactly (see that file's
docstring for the general shape). Progress is visible by polling
GET /articles/title-gate/status, alongside the other stages' equivalent
status endpoints (GET /articles/preprocessing/status, etc.).
"""

from typing import Optional

from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel

from app import repository
from app.services import title_gate

router = APIRouter()


class TitleGateTriggerResponse(BaseModel):
    status: str  # "started" | "already_running" | "skipped" | "stopping" | "not_running"
    message: str


class TitleGateStatusResponse(BaseModel):
    in_progress: bool
    total: int
    processed: int
    estimated_minutes_remaining: Optional[float] = None
    # Day-level view of the backlog (see repository.get_title_gate_day_progress)
    # — always freshly computed from current DB state, independent of
    # whether a run is currently active.
    days_gated: int = 0
    days_total: int = 0
    current_day: Optional[str] = None
    # Unscoped, all-time totals — see repository.get_title_gate_day_progress.
    gated_total: int = 0
    archived_total: int = 0
    articles_total: int = 0


@router.post("/articles/title-gate", response_model=TitleGateTriggerResponse)
def trigger_title_gate(background_tasks: BackgroundTasks):
    """Starts a title-gate run in the background. Safe to call repeatedly
    — if one's already running, or nothing is pending, this reports that
    instead of starting a redundant run."""
    if title_gate.is_title_gate_running():
        return TitleGateTriggerResponse(
            status="already_running",
            message="A title-gate run is already in progress. Reload in a bit to check progress.",
        )

    other_stage = repository.get_running_pipeline_stage()
    if other_stage:
        return TitleGateTriggerResponse(
            status="already_running",
            message=(
                f"A {other_stage} run is currently in progress — title gating "
                "doesn't run at the same time as the other stages (they'd "
                "fight each other for CPU). Try again once it finishes."
            ),
        )

    if not repository.get_articles_pending_title_gate():
        return TitleGateTriggerResponse(
            status="skipped",
            message="Every article has already been through the title gate — nothing left to check.",
        )

    background_tasks.add_task(title_gate.run_title_gate_job)
    return TitleGateTriggerResponse(
        status="started",
        message="Title gating started in the background. Reload the page in a bit to see progress.",
    )


@router.get("/articles/title-gate/status", response_model=TitleGateStatusResponse)
def get_title_gate_run_status():
    """Live progress for the frontend to poll — same shape and same
    recency-weighted ETA as the other stages' status endpoints, plus a
    day-level view of the backlog (days_gated/days_total/current_day),
    computed fresh from the DB regardless of whether a run is active."""
    day_progress = repository.get_title_gate_day_progress()
    status = repository.get_title_gate_status()
    if status is None:
        return TitleGateStatusResponse(in_progress=False, total=0, processed=0, **day_progress)

    eta_minutes = None
    if status.in_progress and status.avg_seconds_per_article is not None:
        remaining_articles = max(status.total - status.processed, 0)
        eta_minutes = round((status.avg_seconds_per_article * remaining_articles) / 60, 1)

    return TitleGateStatusResponse(
        in_progress=status.in_progress,
        total=status.total,
        processed=status.processed,
        estimated_minutes_remaining=eta_minutes,
        **day_progress,
    )


@router.post("/articles/title-gate/stop", response_model=TitleGateTriggerResponse)
def stop_title_gate():
    """Requests that the current run stop after the article it's on right
    now finishes. See routers/extraction.py's stop_extraction() — identical
    reasoning."""
    if title_gate.request_title_gate_stop():
        return TitleGateTriggerResponse(
            status="stopping",
            message=(
                "Stop requested. The current article will finish, then the "
                "run will end. Reload in a bit to confirm it stopped."
            ),
        )
    return TitleGateTriggerResponse(
        status="not_running",
        message="No title-gate run is currently in progress.",
    )
