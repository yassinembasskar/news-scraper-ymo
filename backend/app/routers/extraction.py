"""
Router: single-article extraction retry.

The bulk "Extract articles" stage was retired on 2026-08-27 — extraction
now only happens as part of the preprocessing job (see
services/preprocessing.py and routers/preprocessing.py), scoped to
clustered articles. This router keeps the one thing that isn't part of
that bulk flow: retrying extraction for one specific Failed article (the
"Retry extraction" button shown on that article), which stays available
independent of when the next preprocessing run happens.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from app.services import extractor

router = APIRouter()


class SingleExtractionResponse(BaseModel):
    status: str  # "Successful" | "Failed" | "already_running" | "not_found"
    message: str


@router.post("/articles/{article_id}/extract", response_model=SingleExtractionResponse)
def retry_single_extraction(article_id: str):
    """Retries automatic extraction for exactly one article — the
    per-article 'Retry extraction' button shown on Failed articles. Runs
    synchronously (fast enough for one article) and refuses if a
    preprocessing run's extraction phase is currently using the shared
    extraction lock, rather than competing with it for the same Chromium
    instance."""
    result = extractor.extract_single_article(article_id)
    return SingleExtractionResponse(status=result.status, message=result.message)
