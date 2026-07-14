"""
Router: extraction triggering.

Kicks off one extraction pass as a background task and returns
immediately — a full backlog can take many minutes (each article is
loaded in a real browser, one at a time), far longer than any HTTP request
should reasonably block for. Progress is visible by reloading the article
list: articles show 'Pending' while being processed, then settle into
'Successful'/'Failed'. There's no separate "job status" endpoint — the
existing per-article ingestion_status via GET /articles already shows this.
"""

from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel

from app.services import extractor

router = APIRouter()


class ExtractionTriggerResponse(BaseModel):
    status: str  # "started" | "already_running"
    message: str


@router.post("/articles/extract", response_model=ExtractionTriggerResponse)
def trigger_extraction(background_tasks: BackgroundTasks):
    """Starts an extraction run in the background. Safe to call
    repeatedly — if one's already running, this reports that instead of
    starting a second overlapping run."""
    if extractor.is_extraction_running():
        return ExtractionTriggerResponse(
            status="already_running",
            message="An extraction is already in progress. Reload in a bit to check progress.",
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