"""
Router: extraction triggering, status, and stop.

Kicks off one extraction pass as a background task and returns
immediately — a full backlog can take many minutes (each article is
loaded in a real browser, one at a time), far longer than any HTTP request
should reasonably block for. Progress is visible either by reloading the
article list (each shows 'Pending' while being processed, then settles
into 'Successful'/'Failed') or by polling GET /articles/extract/status,
mirroring GET /articles/embed/status and GET /articles/score/status.
"""

from typing import Optional

from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel

from app import repository
from app.services import extractor

router = APIRouter()


class ExtractionTriggerResponse(BaseModel):
    status: str  # "started" | "already_running" | "skipped" | "stopping" | "not_running"
    message: str


class ExtractionStatusResponse(BaseModel):
    in_progress: bool
    total: int
    processed: int
    estimated_minutes_remaining: Optional[float] = None


@router.post("/articles/extract", response_model=ExtractionTriggerResponse)
def trigger_extraction(background_tasks: BackgroundTasks):
    """Starts an extraction run in the background. Safe to call
    repeatedly — if one's already running, this reports that instead of
    starting a second overlapping run. If there's nothing pending
    extraction right now, this says so immediately instead of spinning up
    a background job (and a browser) just to discover the same thing a
    moment later — that "nothing to do" outcome is reported here and
    nowhere else; it never touches last_run_at/last_run_status, which
    keep reflecting the most recent run that actually did something."""
    if extractor.is_extraction_running():
        return ExtractionTriggerResponse(
            status="already_running",
            message="An extraction is already in progress. Reload in a bit to check progress.",
        )

    other_stage = repository.get_running_pipeline_stage()
    if other_stage:
        return ExtractionTriggerResponse(
            status="already_running",
            message=(
                f"A {other_stage} run is currently in progress — extraction, "
                "embedding, and scoring don't run at the same time (they'd "
                "fight each other for CPU). Try again once it finishes."
            ),
        )

    if not repository.get_articles_pending_extraction():
        return ExtractionTriggerResponse(
            status="skipped",
            message="All articles are already extracted — nothing left to extract.",
        )

    background_tasks.add_task(extractor.run_extraction_job)
    return ExtractionTriggerResponse(
        status="started",
        message=(
            "Extraction started in the background. Reload the page in a "
            "minute or two to see progress — articles will show as "
            "Pending while being processed."
        ),
    )


@router.get("/articles/extract/status", response_model=ExtractionStatusResponse)
def get_extraction_run_status():
    """Live progress for the frontend to poll — same shape and same
    recency-weighted ETA as /articles/embed/status and /articles/score/status."""
    status = repository.get_extraction_status()
    if status is None:
        return ExtractionStatusResponse(in_progress=False, total=0, processed=0)

    eta_minutes = None
    if status.in_progress and status.avg_seconds_per_article is not None:
        remaining_articles = max(status.total - status.processed, 0)
        eta_minutes = round((status.avg_seconds_per_article * remaining_articles) / 60, 1)

    return ExtractionStatusResponse(
        in_progress=status.in_progress,
        total=status.total,
        processed=status.processed,
        estimated_minutes_remaining=eta_minutes,
    )


@router.post("/articles/extract/stop", response_model=ExtractionTriggerResponse)
def stop_extraction():
    """Requests that the current run stop after the article it's on right
    now finishes. See routers/embedding.py's stop_embedding() — identical
    reasoning."""
    if extractor.request_extraction_stop():
        return ExtractionTriggerResponse(
            status="stopping",
            message=(
                "Stop requested. The current article will finish, then the "
                "run will end. Reload in a bit to confirm it stopped."
            ),
        )
    return ExtractionTriggerResponse(
        status="not_running",
        message="No extraction run is currently in progress.",
    )


class SingleExtractionResponse(BaseModel):
    status: str  # "Successful" | "Failed" | "already_running" | "not_found"
    message: str


@router.post("/articles/{article_id}/extract", response_model=SingleExtractionResponse)
def retry_single_extraction(article_id: str):
    """Retries automatic extraction for exactly one article — the
    per-article 'Retry extraction' button shown on Failed articles. Runs
    synchronously (fast enough for one article) and refuses if a bulk
    extraction is currently in progress rather than competing with it."""
    result = extractor.extract_single_article(article_id)
    return SingleExtractionResponse(status=result.status, message=result.message)