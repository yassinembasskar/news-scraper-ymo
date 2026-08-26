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
    include_archived: bool = Query(
        False,
        description="Include articles the title-gate LLM archived (deemed unable to affect the tracked business, "
        "so never extracted). Off by default.",
    ),
):
    """Returns articles matching the given filters, newest first. Any
    filter left unset is simply not applied."""
    return repository.get_articles(
        language=language,
        keyword=keyword,
        date_from=date_from,
        date_to=date_to,
        include_archived=include_archived,
    )


@router.get("/articles/{article_id}", response_model=ArticleOut)
def get_article(article_id: str):
    """Fetches one article by id — full shape, including content and
    score_explanation, for the frontend's article detail page."""
    try:
        found = repository.get_articles_by_ids([article_id])
    except InvalidId:
        raise HTTPException(status_code=404, detail="Article not found")

    if not found:
        raise HTTPException(status_code=404, detail="Article not found")

    return found[0]


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


class FlushArchivedResponse(BaseModel):
    deleted_count: int


class DisarchiveResponse(BaseModel):
    status: str  # "ok" | "not_found"


@router.get("/archived", response_model=List[ArticleOut])
def list_archived_articles():
    """Every article the title-gate LLM archived — the Archived review
    page's data source. Unlike GET /articles?include_archived=true, this
    returns *only* archived articles (no other filters), since the review
    page's whole purpose is auditing that one bucket."""
    return repository.get_archived_articles()


@router.post("/archived/{article_id}/disarchive", response_model=DisarchiveResponse)
def disarchive_article(article_id: str):
    """Reverses one title-gate decision a human judged wrong — the article
    goes back to "No Extraction" and re-enters the normal pipeline on the
    next extraction run."""
    try:
        changed = repository.disarchive_article(article_id)
    except InvalidId:
        return DisarchiveResponse(status="not_found")
    return DisarchiveResponse(status="ok" if changed else "not_found")


@router.post("/archived/flush", response_model=FlushArchivedResponse)
def flush_archived_articles():
    """Permanently deletes every currently-Archived article — the "Flush
    archived" button's destructive action. Irreversible: unlike
    disarchiving, there's no undo once this runs."""
    deleted = repository.flush_archived_articles()
    return FlushArchivedResponse(deleted_count=deleted)