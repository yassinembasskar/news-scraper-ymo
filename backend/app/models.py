"""
Pydantic schemas for the things this backend persists:
  - articles (news articles, tagged with matched keywords)
  - the scrape-status and embedding-status documents in `meta`

These are pure data shapes — no Mongo-specific code here. Reading from and
writing to MongoDB (including converting to/from Mongo's `_id`) lives in
`repository.py`.
"""

from datetime import datetime, timezone
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, HttpUrl

IngestionStatus = Literal["No Extraction", "Pending", "Successful", "Failed", "Expired"]
EmbeddingStatus = Literal["Not Embedded", "Pending", "Embedded", "Failed"]
class ArticleBase(BaseModel):
    """Fields common to every article, before it has a database id."""
    url: HttpUrl
    title: str
    description: Optional[str] = None
    source_name: Optional[str] = None
    published_at: datetime
    language: str
    matched_keywords: List[str] = Field(default_factory=list)
    ingestion_status: IngestionStatus = "No Extraction"
    content: Optional[str] = None
    # Populated by the embedding job (services/embedder.py), not by the
    # scraper — mirrors how ingestion_status/content work: new articles get
    # the defaults below, upsert_article() protects existing values from
    # being clobbered by a later re-scrape of the same url.
    chunk_ids: List[str] = Field(default_factory=list)
    number_of_chunks: int = 0
    embedding_status: EmbeddingStatus = "Not Embedded"

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


class EmbeddingRunStatus(BaseModel):
    """The single `meta` document (_id: "embedding_status") tracking
    embedding-job state. in_progress is the Mongo-backed guard against
    overlapping runs — no staleness timeout yet, so a run that crashes
    without reaching its finally block would need a manual reset.
    total/processed reflect the run currently in progress (or the last
    completed one) — used to show live progress in the frontend."""
    last_run_at: Optional[datetime] = None
    last_run_status: Optional[Literal["success", "partial", "failed", "stopped"]] = None
    in_progress: bool = False
    total: int = 0
    processed: int = 0
    started_at: Optional[datetime] = None
    # Set by the stop endpoint, checked by the loop between articles (there's
    # no safe way to interrupt a Python thread mid-encode/-upsert) — the run
    # exits cleanly at the next opportunity rather than instantly.
    stop_requested: bool = False
    # Exponential moving average of seconds-per-article, recomputed after
    # every article. Used instead of a plain elapsed/processed average so
    # the ETA reflects the current rate, not one dragged down forever by a
    # slow first article (e.g. one-time model/thread warmup on a fresh
    # container start).
    avg_seconds_per_article: Optional[float] = None