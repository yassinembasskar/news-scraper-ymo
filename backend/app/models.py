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
EmbeddingStatus = Literal["Not Embedded", "Pending", "Embedded", "Failed", "Expired"]
ScoredStatus = Literal["Not Scored", "Pending", "Scored", "Failed"]
TranslationStatus = Literal["Not Translated", "Translated", "Failed"]
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
    # Populated by the relativity-scoring job (services/scorer.py, not yet
    # written) — same protection pattern as the embedding fields above.
    relativity_score: Optional[float] = None
    scored_status: ScoredStatus = "Not Scored"
    score_explanation: Optional[dict] = None
    # Accumulated from "more"/"less relative" feedback clicks (see
    # routers/scoring.py's submit_relativity_feedback) — added on top of
    # the tree-computed raw score, clamped to [0, 100], every time this
    # article is (re)scored. Separate from the tree-threshold nudges that
    # same feedback also makes: those shape future/other articles'
    # scoring but rarely move this article's own number (threshold
    # changes don't affect a similarity value already well clear of the
    # boundary), so this field is what guarantees a visible per-article
    # change instead.
    relativity_score_adjustment: int = 0
    # Populated on demand by the LLM Insight / report stage
    # (services/report_job.py), the first time a non-English article is
    # assessed: the article is translated to English once, cached here, and
    # reused on every later report run instead of being re-translated.
    # Same scraper/upsert protection pattern as the fields above. English
    # articles are never translated and keep "Not Translated" (nothing reads
    # it for them). translated_content is the full English block
    # ("Title: ...\n\n<body>") fed straight to the LLM assessment.
    translated_title: Optional[str] = None
    translated_content: Optional[str] = None
    translation_status: TranslationStatus = "Not Translated"

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
    last_run_status: Optional[Literal["success", "partial", "failed", "stopped", "skipped"]] = None
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


class ScoringRunStatus(BaseModel):
    """The single `meta` document (_id: "scoring_status") tracking the
    relativity-scoring job's state — identical shape to EmbeddingRunStatus,
    same reasoning throughout (see that model's docstring/comments)."""
    last_run_at: Optional[datetime] = None
    last_run_status: Optional[Literal["success", "partial", "failed", "stopped", "skipped"]] = None
    in_progress: bool = False
    total: int = 0
    processed: int = 0
    started_at: Optional[datetime] = None
    stop_requested: bool = False
    avg_seconds_per_article: Optional[float] = None


class ExtractionRunStatus(BaseModel):
    """The single `meta` document (_id: "extraction_status") tracking the
    extraction job's state — identical shape to EmbeddingRunStatus/
    ScoringRunStatus, same reasoning throughout (see EmbeddingRunStatus's
    docstring/comments). Layered on top of (not replacing) extractor.py's
    in-process threading.Lock, which remains the hard guard against two
    Playwright/Chromium instances running in the same process — this
    document exists so progress/ETA can be polled and a run can be stopped
    cooperatively, the same way embedding/scoring already work."""
    last_run_at: Optional[datetime] = None
    last_run_status: Optional[Literal["success", "partial", "failed", "stopped", "skipped"]] = None
    in_progress: bool = False
    total: int = 0
    processed: int = 0
    started_at: Optional[datetime] = None
    stop_requested: bool = False
    avg_seconds_per_article: Optional[float] = None


class ReportRunStatus(BaseModel):
    """The single `meta` document (_id: "report_status") tracking the LLM
    Insight / daily-report generation job — same shape and same reasoning
    as ScoringRunStatus, plus `report_date`: which calendar day (UTC,
    "YYYY-MM-DD") the in-progress (or most recent) run is generating, so the
    frontend polling a specific day's generation knows whether the running
    job is the one it triggered. total/processed count that day's candidate
    articles, one LLM assessment each."""
    last_run_at: Optional[datetime] = None
    last_run_status: Optional[Literal["success", "partial", "failed", "stopped", "skipped"]] = None
    in_progress: bool = False
    total: int = 0
    processed: int = 0
    started_at: Optional[datetime] = None
    stop_requested: bool = False
    avg_seconds_per_article: Optional[float] = None
    report_date: Optional[str] = None


class ReportInsight(BaseModel):
    """One relevant-insight card in a daily report — the LLM's structured
    assessment of a single article, plus which article it came from so the
    frontend card can link back to it."""
    article_id: str
    source_title: str
    title: str
    confidence: int
    concerned_param: str
    effect: Literal["positive", "negative", "neutral"]
    recommended_action: str


class DailyReport(BaseModel):
    """One generated daily report, stored in the `daily_reports` collection
    keyed by report_date ("YYYY-MM-DD", UTC). The presence of a document is
    itself the "this day has been processed" signal the calendar reads;
    insights == [] is a valid, fully-processed outcome (nothing relevant
    that day), distinct from no document at all (never generated)."""
    report_date: str
    generated_at: datetime
    articles_processed: int
    relevant_count: int
    insights: List[ReportInsight] = Field(default_factory=list)