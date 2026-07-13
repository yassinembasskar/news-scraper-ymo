"""
Router: reading articles, plus manual content entry.

Thin HTTP wrapper around repository.py — no Mongo calls or filtering logic
here, just request parsing and response shaping.
"""

from datetime import datetime
from typing import List, Optional

from bson.errors import InvalidId
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app import repository
from app.models import ArticleOut

router = APIRouter()


class ManualContentRequest(BaseModel):
    content: str


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


@router.put("/articles/{article_id}/content")
def submit_manual_content(article_id: str, payload: ManualContentRequest):
    """Lets a user manually paste an article's content — typically after
    automatic extraction failed. Stored the same way a successful
    extraction would be (via set_article_content), so the article behaves
    identically afterwards regardless of how its content was obtained."""
    if not payload.content.strip():
        raise HTTPException(status_code=400, detail="Content cannot be empty")

    try:
        exists = repository.get_articles_by_ids([article_id])
    except InvalidId:
        raise HTTPException(status_code=404, detail="Article not found")

    if not exists:
        raise HTTPException(status_code=404, detail="Article not found")

    repository.set_article_content(article_id, payload.content, "Successful")
    return {"status": "ok"}