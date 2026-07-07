"""
Pydantic schemas for the two things this backend persists:
  - articles (news articles, tagged with matched keywords)
  - the single scrape-status document in `meta`

These are pure data shapes — no Mongo-specific code here. Reading from and
writing to MongoDB (including converting to/from Mongo's `_id`) lives in
`repository.py`.
"""

from datetime import datetime, timezone
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, HttpUrl


class ArticleBase(BaseModel):
    """Fields common to every article, before it has a database id."""
    url: HttpUrl
    title: str
    description: Optional[str] = None
    source_name: Optional[str] = None
    published_at: datetime
    language: str
    matched_keywords: List[str] = Field(default_factory=list)


class ArticleCreate(ArticleBase):
    """Shape the scraper builds before upserting into Mongo."""
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ArticleOut(ArticleBase):
    """Shape returned by the API — includes the Mongo-generated id."""
    id: str
    fetched_at: datetime


class ScrapeStatus(BaseModel):
    """The single `meta` document tracking incremental scrape state."""
    last_scraped: Optional[datetime] = None
    last_run_at: Optional[datetime] = None
    last_run_status: Optional[Literal["success", "partial", "failed"]] = None