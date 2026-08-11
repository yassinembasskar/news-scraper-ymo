"""
Router: scrape triggering and status.

Thin HTTP wrapper around services.scraper.run_scrape() and
repository.get_scrape_status() — no Mongo calls and no business logic here,
just request/response shaping. All the actual work (deciding the date range,
calling GNews, keyword matching, upserting into `articles`) already happens
inside run_scrape().
"""

from dataclasses import asdict
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from app import repository
from app.services.scraper import run_scrape

router = APIRouter()


class ScrapeSummaryResponse(BaseModel):
    status: str  # "success" | "partial" | "failed" | "skipped"
    message: str
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None
    new_articles: int = 0
    duplicate_articles: int = 0
    excluded_articles: int = 0
    malformed_articles: int = 0
    archived_articles: int = 0
    errors: List[str] = []


class ScrapeStatusResponse(BaseModel):
    last_scraped: Optional[datetime] = None
    last_run_at: Optional[datetime] = None
    last_run_status: Optional[str] = None


@router.post("/update", response_model=ScrapeSummaryResponse)
def trigger_update():
    """Runs one incremental scrape. Safe to call repeatedly — if already
    fully caught up today, it no-ops (status="skipped") instead of burning
    GNews quota for nothing."""
    summary = run_scrape()
    return ScrapeSummaryResponse(**asdict(summary))


@router.get("/scrape-status", response_model=ScrapeStatusResponse)
def get_status():
    """Returns the current scrape watermark: the date up to which data was
    last successfully covered, and the outcome of the most recent attempt.
    Returns all-null fields if the scraper has never run."""
    status = repository.get_scrape_status()
    if status is None:
        return ScrapeStatusResponse()
    return ScrapeStatusResponse(**status.model_dump())