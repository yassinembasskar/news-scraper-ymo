"""
Scrape orchestrator.

This is the only place that decides WHEN to scrape and WHAT to do with the
results. It ties together the pieces built separately:
  - repository.py       -> reads/writes last_scraped, upserts articles
  - gnews_client.py, ... -> each source client knows how to talk to its own
                            API, nothing else — see source_client.py for the
                            common RawArticle/FetchResult shape they all
                            return, so this file never touches a source's
                            raw JSON.
  - keyword_matcher.py   -> tags articles with matched keywords, flags exclusions

Adding a new source is adding a new client module (mirroring
gnews_client.py) and registering its fetch_articles in SOURCES below —
nothing else here needs to change.

Date-range decision (single global last_scraped, shared across all sources
and languages):
  - never scraped before      -> last 7 days, ending today
  - already scraped today     -> do nothing, report "skipped"
  - scraped on an earlier day -> catch up from that day to today
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, List, Optional

from pydantic import ValidationError

from app import repository
from app.models import ArticleCreate, ScrapeStatus
from app.services import (
    currents_client,
    gnews_client,
    keyword_matcher,
    newsapi_org_client,
    newsdata_io_client,
    thenewsapi_com_client,
)
from app.services.source_client import FetchResult, RawArticle

# Every registered source is tried on each run; one source's fetch_articles
# raising or returning errors doesn't stop the others (see run_scrape()).
SOURCES: List[Callable[[datetime, datetime], FetchResult]] = [
    gnews_client.fetch_articles,
    newsapi_org_client.fetch_articles,
    newsdata_io_client.fetch_articles,
    currents_client.fetch_articles,
    thenewsapi_com_client.fetch_articles,
]

DEFAULT_DAYS_BACK = 7


@dataclass
class ScrapeSummary:
    status: str  # "success" | "partial" | "failed" | "skipped"
    message: str
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None
    new_articles: int = 0
    duplicate_articles: int = 0
    excluded_articles: int = 0
    malformed_articles: int = 0
    errors: List[str] = field(default_factory=list)


def _determine_date_range(now: datetime) -> Optional[tuple]:
    """Returns (date_from, date_to) to scrape, or None if already scraped
    today (nothing to do)."""
    status = repository.get_scrape_status()

    if status and status.last_scraped:
        last_scraped = status.last_scraped
        # Mongo may hand this back as a naive UTC datetime; normalize before comparing.
        if last_scraped.tzinfo is None:
            last_scraped = last_scraped.replace(tzinfo=timezone.utc)

        if last_scraped.date() == now.date():
            return None  # already caught up today

        return last_scraped, now

    # Never scraped before.
    return now - timedelta(days=DEFAULT_DAYS_BACK), now


def _process_article(raw: RawArticle) -> tuple:
    """Turns one already-normalized RawArticle (from any source) into an
    ArticleCreate, or reports why it was skipped. Returns
    ('kept', ArticleCreate) / ('excluded', None) / ('malformed', None)."""
    if keyword_matcher.is_excluded(raw.title, raw.language):
        return "excluded", None

    matched_keywords = keyword_matcher.find_matched_keywords(raw.title, raw.description, raw.language)

    try:
        article = ArticleCreate(
            url=raw.url,
            title=raw.title,
            description=raw.description,
            source_name=raw.source_name,
            published_at=raw.published_at,
            language=raw.language,
            matched_keywords=matched_keywords,
        )
    except ValidationError:
        return "malformed", None

    return "kept", article


def _build_message(summary: "ScrapeSummary") -> str:
    if summary.status == "skipped":
        return "Already scraped today — nothing to do."

    parts = [
        f"{summary.new_articles} new",
        f"{summary.duplicate_articles} already seen",
        f"{summary.excluded_articles} excluded",
    ]
    if summary.malformed_articles:
        parts.append(f"{summary.malformed_articles} malformed/skipped")
    base = f"Scrape {summary.status}: " + ", ".join(parts) + "."

    if summary.errors:
        base += f" Errors in {len(summary.errors)} request(s): " + "; ".join(summary.errors)

    return base


def run_scrape() -> ScrapeSummary:
    """Runs one incremental scrape across every registered source (see
    SOURCES) and returns a combined summary. Safe to call repeatedly — if
    already caught up today, it no-ops instead of burning API quota for
    nothing."""
    now = datetime.now(timezone.utc)

    date_range = _determine_date_range(now)
    if date_range is None:
        summary = ScrapeSummary(status="skipped", message="")
        summary.message = _build_message(summary)
        return summary

    date_from, date_to = date_range

    all_raw_articles: List[RawArticle] = []
    all_errors: List[str] = []
    malformed_count = 0

    for fetch_articles in SOURCES:
        fetch_result = fetch_articles(date_from, date_to)
        all_raw_articles.extend(fetch_result.articles)
        all_errors.extend(fetch_result.errors)
        malformed_count += fetch_result.malformed_count

    new_count = 0
    duplicate_count = 0
    excluded_count = 0

    for raw in all_raw_articles:
        outcome, article = _process_article(raw)

        if outcome == "excluded":
            excluded_count += 1
            continue
        if outcome == "malformed":
            malformed_count += 1
            continue

        is_new = repository.upsert_article(article)
        if is_new:
            new_count += 1
        else:
            duplicate_count += 1

    # "failed" means every source came back empty-handed despite errors —
    # not just that some request somewhere failed, since with several
    # sources running one bad request shouldn't overshadow the others still
    # having delivered articles.
    if not all_errors:
        run_status = "success"
    elif not all_raw_articles:
        run_status = "failed"
    else:
        run_status = "partial"

    # Only advance the watermark on a full success. On partial/failed, it
    # stays at date_from so the NEXT run (same day or later) re-requests the
    # full original range instead of silently treating the missed portion
    # as already covered. last_run_at / last_run_status still reflect the
    # most recent attempt either way.
    new_last_scraped = date_to if run_status == "success" else date_from

    repository.set_scrape_status(ScrapeStatus(
        last_scraped=new_last_scraped,
        last_run_at=now,
        last_run_status=run_status,
    ))

    summary = ScrapeSummary(
        status=run_status,
        message="",
        date_from=date_from,
        date_to=date_to,
        new_articles=new_count,
        duplicate_articles=duplicate_count,
        excluded_articles=excluded_count,
        malformed_articles=malformed_count,
        errors=all_errors,
    )
    summary.message = _build_message(summary)

    repository.log_scrape_run(
        status=summary.status,
        message=summary.message,
        date_from=summary.date_from,
        date_to=summary.date_to,
        new_articles=summary.new_articles,
        duplicate_articles=summary.duplicate_articles,
        excluded_articles=summary.excluded_articles,
        malformed_articles=summary.malformed_articles,
        errors=summary.errors,
    )

    return summary