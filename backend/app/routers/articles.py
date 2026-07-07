"""
Router: reading articles.

Thin HTTP wrapper around repository.get_articles() — no Mongo calls or
filtering logic here, just query-parameter parsing and response shaping.
"""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Query

from app import repository
from app.models import ArticleOut

router = APIRouter()


@router.get("/articles", response_model=List[ArticleOut])
def list_articles(
    language: Optional[str] = Query(None, description="Filter by language code, e.g. 'en', 'fr', 'ar'"),
    keyword: Optional[str] = Query(None, description="Filter by a single matched keyword, e.g. 'Renault'"),
    date_from: Optional[datetime] = Query(None, description="Only articles published on or after this date"),
    date_to: Optional[datetime] = Query(None, description="Only articles published on or before this date"),
):
    """Returns articles matching the given filters, newest first. Any
    filter left unset is simply not applied."""
    return repository.get_articles(
        language=language,
        keyword=keyword,
        date_from=date_from,
        date_to=date_to,
    )