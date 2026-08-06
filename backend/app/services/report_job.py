"""
Daily-report generation orchestrator (LLM Insight / Stage 5).

The background-job counterpart to services/scoring_job.py — same Mongo-backed
in_progress guard, stop_requested cooperative flag, and progress/ETA tracking
— but the per-item unit of work is one Ollama LLM assessment (see
services/llm_insight.py) instead of a tree walk, and the output is a single
daily_reports document rather than per-article field updates.

Workflow, every time a day is generated:
  1. Guard   -> refuse to start if any pipeline stage is already running
                (report generation shares the single-stage-at-a-time guard —
                the LLM is heavy, and it must not fight embedding/scoring for
                CPU). Mongo-backed, survives a restart.
  2. Gather  -> every article published that UTC day with a relativity_score
                at/above MIN_RELATIVITY_SCORE, highest first.
  3. Assess  -> for each candidate: get English text (the original for
                English articles; a cached translation for non-English ones,
                translating and caching it on the article the first time),
                then one llm_insight.assess() call on that English text. A
                failure on one article is logged and does not abort the run.
                Relevant results become insight cards.
  4. Store   -> one daily_reports document for the day (even when zero
                articles were relevant — an empty report is a real,
                distinct "processed, nothing found" result).

A day with no candidate articles at all still produces an empty report
immediately, so the calendar can mark it processed rather than leaving it
looking un-generated forever.
"""

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

from app import repository
from app.models import DailyReport, ReportInsight
from app.services import llm_insight

RATE_EMA_ALPHA = 0.3

# Only articles scoring at least this on the relativity tree are fed to the
# LLM — matches the threshold the Stage 3 validation used. Keeps a busy
# day's LLM workload bounded to plausibly-relevant articles.
MIN_RELATIVITY_SCORE = 60


@dataclass
class ReportSummary:
    status: str  # "success" | "partial" | "failed" | "stopped"
    message: str
    report_date: str = ""
    articles_processed: int = 0
    relevant_count: int = 0
    failed_articles: int = 0
    errors: List[str] = field(default_factory=list)


def _english_text(article) -> tuple:
    """Returns (title, content) in English for one article, ready to assess.

    English articles: their own title/content, untouched.
    Non-English articles: the cached translation if one exists; otherwise
    translate once, cache it on the article (translation_status ->
    "Translated"), and use it. Every later report run reuses the cached
    text rather than paying the translation LLM call again — the whole point
    of storing it. A translation failure marks the article's
    translation_status "Failed" and re-raises, so the per-article try/except
    in the run loop counts it as a failure (and it won't be silently retried
    every run)."""
    if (article.language or "en").lower() == "en":
        return article.title, article.content or ""

    if article.translation_status == "Translated" and article.translated_content:
        return article.translated_title or article.title, article.translated_content

    try:
        title_en, body_en = llm_insight.translate_to_english(article.title, article.content or "")
    except Exception:
        repository.set_article_translation(article.id, None, None, "Failed")
        raise

    repository.set_article_translation(article.id, title_en, body_en, "Translated")
    return title_en, body_en


def run_report_generation(report_date: str) -> ReportSummary:
    """Generates (or regenerates) the report for one UTC day. Always writes
    a daily_reports document at the end — including when the run is stopped
    or partially fails — so the day is never left in an ambiguous
    'in-between' state. Whatever insights were collected before a stop are
    kept."""
    now = datetime.now(timezone.utc)
    candidates = repository.get_articles_for_report(report_date, MIN_RELATIVITY_SCORE)

    repository.set_report_started(now, report_date)
    total = len(candidates)
    repository.set_report_progress(total, 0)

    insights: List[ReportInsight] = []
    processed = 0
    failed_count = 0
    errors: List[str] = []
    avg_seconds_per_article: Optional[float] = None
    last_checkpoint = time.monotonic()

    stopped = False
    for article in candidates:
        status = repository.get_report_status()
        if status and status.stop_requested:
            stopped = True
            break

        try:
            title_en, content_en = _english_text(article)
            results = llm_insight.assess(title_en, content_en)
            for result in results:
                insights.append(
                    ReportInsight(
                        article_id=article.id,
                        source_title=article.title,
                        title=result.get("title", article.title),
                        confidence=int(result.get("confidence", 0)),
                        concerned_param=result.get("concerned_param", ""),
                        effect=result.get("effect", "neutral"),
                        recommended_action=result.get("recommended_action", ""),
                    )
                )
        except Exception as e:
            failed_count += 1
            errors.append(f"{article.title}: {e}")

        processed += 1

        checkpoint = time.monotonic()
        duration = checkpoint - last_checkpoint
        last_checkpoint = checkpoint
        avg_seconds_per_article = (
            duration if avg_seconds_per_article is None
            else RATE_EMA_ALPHA * duration + (1 - RATE_EMA_ALPHA) * avg_seconds_per_article
        )

        repository.set_report_progress(total, processed)
        repository.set_report_rate(avg_seconds_per_article)

    report = DailyReport(
        report_date=report_date,
        generated_at=datetime.now(timezone.utc),
        articles_processed=processed,
        relevant_count=len(insights),
        insights=insights,
    )
    repository.upsert_daily_report(report)

    if stopped:
        run_status = "stopped"
    elif failed_count == 0:
        run_status = "success"
    elif processed == failed_count:
        run_status = "failed"
    else:
        run_status = "partial"

    message = (
        f"Report {run_status} for {report_date}: {len(insights)} relevant "
        f"insight(s) from {processed} article(s), {failed_count} failed."
    )

    repository.set_report_run_outcome(now, run_status)

    return ReportSummary(
        status=run_status,
        message=message,
        report_date=report_date,
        articles_processed=processed,
        relevant_count=len(insights),
        failed_articles=failed_count,
        errors=errors,
    )


def is_report_running() -> bool:
    """Returns True if a report-generation run is currently in progress, per
    the Mongo-backed flag."""
    status = repository.get_report_status()
    return bool(status and status.in_progress)


def request_report_stop() -> bool:
    """Asks the current run to stop after the article it's working on right
    now finishes. Returns False if nothing is running to stop."""
    if not is_report_running():
        return False
    repository.set_report_stop_requested(True)
    return True


def run_report_job(report_date: str) -> None:
    """Background-task entrypoint: guards against overlapping runs (its own
    and any other pipeline stage's), always clears in_progress/stop_requested
    in a finally block whether the run succeeded, was stopped, or raised."""
    if is_report_running():
        return
    # Re-checked here (not just in the router) to close the race where two
    # trigger requests for different stages land close enough together that
    # both pass the router's check before either flag is set.
    if repository.get_running_pipeline_stage():
        return

    repository.set_report_in_progress(True)
    try:
        summary = run_report_generation(report_date)
        print(f"[report] {summary.message}")
    finally:
        repository.set_report_in_progress(False)
        repository.set_report_stop_requested(False)
