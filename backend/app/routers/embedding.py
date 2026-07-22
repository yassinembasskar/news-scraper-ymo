"""
Router: embedding triggering.

Kicks off one embedding pass as a background task and returns
immediately — embedding + upserting a backlog of articles can take
several minutes (each article is chunked, embedded, and upserted into
Pinecone one at a time), far longer than any HTTP request should
reasonably block for. Progress is visible by reloading the article list:
articles show their embedding_status ("Not Embedded" -> "Pending" ->
"Embedded"/"Failed"). There's no separate "job status" endpoint — same
pattern as /articles/extract.
"""

from typing import Optional

from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel

from app import repository
from app.services import embedder

router = APIRouter()


class EmbeddingTriggerResponse(BaseModel):
    status: str  # "started" | "already_running"
    message: str


class EmbeddingStatusResponse(BaseModel):
    in_progress: bool
    total: int
    processed: int
    estimated_minutes_remaining: Optional[float] = None


@router.post("/articles/embed", response_model=EmbeddingTriggerResponse)
def trigger_embedding(background_tasks: BackgroundTasks):
    """Starts an embedding run in the background. Safe to call
    repeatedly — if one's already running, this reports that instead of
    starting a second overlapping run."""
    if embedder.is_embedding_running():
        return EmbeddingTriggerResponse(
            status="already_running",
            message="An embedding run is already in progress. Reload in a bit to check progress.",
        )

    background_tasks.add_task(embedder.run_embedding_job)
    return EmbeddingTriggerResponse(
        status="started",
        message=(
            "Embedding started in the background. Reload the page in a "
            "minute or two to see progress — articles will show as "
            "'Pending' while being processed, then settle into "
            "'Embedded'/'Failed'."
        ),
    )


@router.get("/articles/embed/status", response_model=EmbeddingStatusResponse)
def get_embedding_run_status():
    """Live progress for the frontend to poll — how many of the current
    (or most recently finished) run's candidates have been processed, plus
    a rough ETA extrapolated from a recency-weighted average time per
    article (see embedder.RATE_EMA_ALPHA) rather than a plain run-long
    average, so one slow article early on doesn't permanently inflate it."""
    status = repository.get_embedding_status()
    if status is None:
        return EmbeddingStatusResponse(in_progress=False, total=0, processed=0)

    eta_minutes = None
    if status.in_progress and status.avg_seconds_per_article is not None:
        remaining_articles = max(status.total - status.processed, 0)
        eta_minutes = round((status.avg_seconds_per_article * remaining_articles) / 60, 1)

    return EmbeddingStatusResponse(
        in_progress=status.in_progress,
        total=status.total,
        processed=status.processed,
        estimated_minutes_remaining=eta_minutes,
    )


@router.post("/articles/embed/stop", response_model=EmbeddingTriggerResponse)
def stop_embedding():
    """Requests that the current run stop after the article it's on right
    now finishes — not instant, since there's no safe way to interrupt a
    Python thread mid-computation. Once it stops, in_progress goes back to
    False and any article left at "Pending" is reset to "Not Embedded"
    (see run_embedding_job's finally block)."""
    if embedder.request_embedding_stop():
        return EmbeddingTriggerResponse(
            status="stopping",
            message=(
                "Stop requested. The current article will finish, then the "
                "run will end. Reload in a bit to confirm it stopped."
            ),
        )
    return EmbeddingTriggerResponse(
        status="not_running",
        message="No embedding run is currently in progress.",
    )
