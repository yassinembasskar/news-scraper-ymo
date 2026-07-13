"""
Router: extraction triggering.

Thin HTTP wrapper around services.extractor.run_extraction() — no Mongo
calls and no business logic here, just request/response shaping. All the
actual work (expiring stale content, finding 'No Extraction' articles,
running Playwright + trafilatura) already happens inside run_extraction().
"""

from dataclasses import asdict
from typing import List

from fastapi import APIRouter
from pydantic import BaseModel

from app.services.extractor import run_extraction

router = APIRouter()


class ExtractionSummaryResponse(BaseModel):
    status: str  # "success" | "partial" | "failed" | "skipped"
    message: str
    expired_articles: int = 0
    extracted_articles: int = 0
    failed_articles: int = 0
    errors: List[str] = []


@router.post("/articles/extract", response_model=ExtractionSummaryResponse)
def trigger_extraction():
    """Runs one extraction pass: expires stale content, then extracts
    every article still in 'No Extraction' state. Safe to call repeatedly."""
    summary = run_extraction()
    return ExtractionSummaryResponse(**asdict(summary))
