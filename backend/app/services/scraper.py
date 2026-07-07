"""
Scrape orchestrator.

This is the only place that decides WHEN to scrape and WHAT to do with the
results. It ties together the pieces built separately:
  - repository.py       -> reads/writes last_scraped, upserts articles
  - gnews_client.py      -> knows how to talk to GNews, nothing else
  - keyword_matcher.py   -> tags articles with matched keywords, flags exclusions

Date-range decision (single global last_scraped, shared across all languages):
  - never scraped before      -> last 7 days, ending today
  - already scraped today     -> do nothing, report "skipped"
  - scraped on an earlier day -> catch up from that day to today
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from pydantic import ValidationError

from app import repository
from app.keywords import KEYWORDS_BY_LANGUAGE
from app.models import ArticleCreate, ScrapeStatus
from app.services import gnews_client, keyword_matcher

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


def _parse_gnews_date(value: Optional[str]) -> datetime:
    """Parses GNews's publishedAt ('YYYY-MM-DDTHH:MM:SSZ', optionally with
    fractional seconds) into a timezone-aware datetime."""
    if not value:
        raise ValueError("Missing publishedAt")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


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


def _process_raw_article(raw: dict) -> tuple:
    """Turns one raw GNews article into an ArticleCreate, or reports why it
    was skipped. Returns ('kept', ArticleCreate) / ('excluded', None) /
    ('malformed', None)."""
    language = raw.get("lang", "en")
    title = raw.get("title", "") or ""
    description = raw.get("description")

    if keyword_matcher.is_excluded(title, language):
        return "excluded", None

    matched_keywords = keyword_matcher.find_matched_keywords(title, description, language)

    try:
        article = ArticleCreate(
            url=raw["url"],
            title=title,
            description=description,
            source_name=(raw.get("source") or {}).get("name"),
            published_at=_parse_gnews_date(raw.get("publishedAt")),
            language=language,
            matched_keywords=matched_keywords,
        )
    except (ValidationError, KeyError, ValueError):
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
        base += f" Errors in {len(summary.errors)} language(s): " + "; ".join(summary.errors)

    return base


def run_scrape() -> ScrapeSummary:
    """Runs one incremental scrape and returns a summary. Safe to call
    repeatedly — if already caught up today, it no-ops instead of burning
    GNews quota for nothing."""
    now = datetime.now(timezone.utc)

    date_range = _determine_date_range(now)
    if date_range is None:
        summary = ScrapeSummary(status="skipped", message="")
        summary.message = _build_message(summary)
        return summary

    date_from, date_to = date_range

    fetch_result = gnews_client.fetch_articles(date_from, date_to)

    new_count = 0
    duplicate_count = 0
    excluded_count = 0
    malformed_count = 0

    for raw in fetch_result.articles:
        outcome, article = _process_raw_article(raw)

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

    if not fetch_result.errors:
        run_status = "success"
    elif len(fetch_result.errors) >= len(KEYWORDS_BY_LANGUAGE):
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
        errors=fetch_result.errors,
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