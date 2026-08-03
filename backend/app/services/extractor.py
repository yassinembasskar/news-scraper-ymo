"""
Extraction orchestrator.

This is the "Extract articles" counterpart to services/scraper.py's
run_scrape(). It ties together:
  - repository.py             -> expires stale content, finds not-yet-
                                 extracted articles, persists results
  - Playwright + trafilatura  -> loads each article's page and pulls its
                                 main body text (same pipeline validated in
                                 test_extraction.py)

Workflow, every time it's triggered:
  1. Expire  -> any article published more than a calendar month ago is
                marked 'Expired' (content cleared), regardless of its
                current status — 'No Extraction', 'Pending', 'Failed', and
                'Successful' are all treated the same way here.
  2. Gather  -> every remaining article still in 'No Extraction' state.
                Because step 1 already ran, none of these can be old — an
                old 'No Extraction' article would already be 'Expired' by
                now. 'Failed' articles are deliberately NOT retried
                automatically (avoids repeatedly re-attempting articles
                that structurally can't be extracted, e.g. a hard
                paywall) — those are only fixed via the manual-paste
                endpoint.
  3. Extract -> for each candidate, mark 'Pending', load + extract, then
                store the final 'Successful'/'Failed' status (and content,
                on success) — and on success, also write the extracted
                text to its own file for manual review. 'Failed' is only
                ever set after an extraction was actually attempted for
                that article (page load errored, or the page loaded but
                no body text could be pulled out of it — e.g. a paywall,
                cookie wall, or bot/protection wall). An article the loop
                never reaches because a stop was requested, or because the
                run was interrupted by a crash/restart, is left exactly as
                it was found ('No Extraction') — never 'Failed' just for
                not having been gotten to.

Progress/stop, mirroring services/embedder.py and services/scoring_job.py:
  - The Mongo document meta._id="extraction_status" tracks in_progress/
    total/processed/stop_requested/avg_seconds_per_article, so the
    frontend can poll a progress bar and request a cooperative stop the
    same way it already does for embedding and scoring.
  - _extraction_lock (in-process) remains the hard guard against two
    Playwright/Chromium instances running at once in this process — it
    doesn't survive a restart, which is fine, since a killed process has
    no lock left to hold anyway. The Mongo in_progress flag is reset on
    every backend startup (see main.py's lifespan) for the same reason
    embedding/scoring's flags are.
"""

import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from dateutil.relativedelta import relativedelta
from playwright.sync_api import sync_playwright
import trafilatura

from app import repository

OUTPUT_DIR = Path("extracted_articles")
TIMEOUT_MS = 30000
EXPIRY_MONTHS = 1
# Weight given to each new per-article duration when updating the
# seconds-per-article moving average — see embedder.py's RATE_EMA_ALPHA for
# why this is recency-weighted rather than a plain run-long average.
RATE_EMA_ALPHA = 0.3

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0"
)

_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*]')

# Guards against two extraction runs executing at once — e.g. a user
# clicking "Extract articles" again after a slow request appears to have
# timed out client-side, not realizing the first run is still going
# server-side. Only one run_extraction() call should ever hold this at a
# time; a run that can't acquire it just no-ops instead of racing the
# first one for the same Chromium instance.
_extraction_lock = threading.Lock()


@dataclass
class ExtractionSummary:
    status: str  # "success" | "partial" | "failed" | "skipped" | "stopped"
    message: str
    expired_articles: int = 0
    extracted_articles: int = 0
    failed_articles: int = 0
    errors: List[str] = field(default_factory=list)


def _safe_filename(title: str) -> str:
    cleaned = _INVALID_FILENAME_CHARS.sub("_", title).strip()
    return cleaned[:40] or "untitled"


def _extract_one(page, url: str) -> tuple:
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
        html = page.content()
    except Exception as e:
        return "Failed", f"Could not load page: {e}"

    text = trafilatura.extract(html)
    if not text or not text.strip():
        return "Failed", (
            "Page loaded, but no main content could be extracted "
            "(possible paywall, cookie wall, or unsupported page layout)."
        )

    return "Successful", text


def run_extraction() -> ExtractionSummary:
    now = datetime.now(timezone.utc)
    cutoff = now - relativedelta(months=EXPIRY_MONTHS)

    expired_count = repository.expire_old_content(cutoff)
    candidates = repository.get_articles_pending_extraction()

    if not candidates:
        # Nothing was actually run — no browser opened, no article touched
        # — so this must not overwrite last_run_at/last_run_status (that
        # stays showing the most recent run that actually did something)
        # or get an entry in extraction_runs. It's deliberately not
        # persisted anywhere; the caller (trigger_extraction()) already
        # checks for this same "nothing pending" case up front and
        # responds immediately, so in practice this branch is only a rare
        # fallback (e.g. everything still pending got expired away between
        # that check and this one) rather than the normal path.
        return ExtractionSummary(
            status="skipped",
            message="All articles are already extracted — nothing left to extract.",
            expired_articles=expired_count,
        )

    OUTPUT_DIR.mkdir(exist_ok=True)

    total = len(candidates)
    extracted_count = 0
    failed_count = 0
    errors: List[str] = []
    repository.set_extraction_started(now)
    repository.set_extraction_progress(total, 0)

    avg_seconds_per_article: Optional[float] = None
    last_checkpoint = time.monotonic()

    stopped = False
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(user_agent=USER_AGENT, viewport={"width": 1280, "height": 800})
        page = context.new_page()

        for article in candidates:
            status_doc = repository.get_extraction_status()
            if status_doc and status_doc.stop_requested:
                stopped = True
                break

            repository.set_ingestion_status(article.id, "Pending")
            status, text = _extract_one(page, str(article.url))

            if status == "Successful":
                repository.set_article_content(article.id, text, status)
                filename = _safe_filename(article.title) + ".txt"
                (OUTPUT_DIR / filename).write_text(
                    f"URL: {article.url}\nSTATUS: {status}\n\n{text}", encoding="utf-8"
                )
                extracted_count += 1
            else:
                repository.set_ingestion_status(article.id, "Failed")
                failed_count += 1
                errors.append(f"{article.title}: {text}")

            checkpoint = time.monotonic()
            duration = checkpoint - last_checkpoint
            last_checkpoint = checkpoint
            avg_seconds_per_article = (
                duration if avg_seconds_per_article is None
                else RATE_EMA_ALPHA * duration + (1 - RATE_EMA_ALPHA) * avg_seconds_per_article
            )

            repository.set_extraction_progress(total, extracted_count + failed_count)
            repository.set_extraction_rate(avg_seconds_per_article)

        browser.close()

    if stopped:
        run_status = "stopped"
    elif failed_count == 0:
        run_status = "success"
    elif extracted_count == 0:
        run_status = "failed"
    else:
        run_status = "partial"

    message = (
        f"Extraction {run_status}: {expired_count} expired, "
        f"{extracted_count} extracted, {failed_count} failed "
        f"(of {total} candidates)."
    )

    repository.set_extraction_run_outcome(now, run_status)
    repository.log_extraction_run(
        status=run_status,
        message=message,
        expired_articles=expired_count,
        extracted_articles=extracted_count,
        failed_articles=failed_count,
        errors=errors,
    )

    return ExtractionSummary(
        status=run_status,
        message=message,
        expired_articles=expired_count,
        extracted_articles=extracted_count,
        failed_articles=failed_count,
        errors=errors,
    )


def is_extraction_running() -> bool:
    """Returns True if an extraction run is currently in progress, per the
    Mongo-backed flag — survives a process restart (reset in main.py's
    lifespan if the process was killed mid-run), unlike a plain in-process
    lock. Used by the router both to give the user immediate feedback
    ("already running") and by the frontend to poll for progress; see
    run_extraction_job() for the actual concurrency guard."""
    status = repository.get_extraction_status()
    return bool(status and status.in_progress)


def request_extraction_stop() -> bool:
    """Asks the current run to stop after the article it's working on
    right now finishes — there's no safe way to interrupt Playwright
    mid-page-load. Returns False if nothing is running to stop."""
    if not is_extraction_running():
        return False
    repository.set_extraction_stop_requested(True)
    return True


def run_extraction_job() -> None:
    """Background-task entrypoint: guards against overlapping runs with
    both the Mongo in_progress flag (survives a restart, and is what
    is_extraction_running() reports) and _extraction_lock (the hard,
    in-process guard against two Playwright/Chromium instances racing for
    the same resources — the lock is what actually matters here since both
    guards live in the same process anyway). Always clears
    in_progress/stop_requested and resets any article left stuck at
    "Pending" in a finally block — whether the run succeeded, partially
    failed, was stopped, or raised. Nothing is waiting on a return value
    here — the endpoint that scheduled this already responded. Progress is
    visible by polling GET /articles/extract/status; the final summary is
    also logged server-side (docker compose logs backend).
    """
    if is_extraction_running():
        return
    # Re-checked here (not just in the router) to close the race where two
    # trigger requests for different stages land close enough together
    # that both pass the router's check before either flag is actually
    # set — see repository.get_running_pipeline_stage().
    if repository.get_running_pipeline_stage():
        return
    if not _extraction_lock.acquire(blocking=False):
        return

    repository.set_extraction_in_progress(True)
    try:
        summary = run_extraction()
        print(f"[extraction] {summary.message}")
    finally:
        repository.set_extraction_in_progress(False)
        repository.set_extraction_stop_requested(False)
        repository.reset_pending_extraction_articles()
        _extraction_lock.release()


@dataclass
class SingleExtractionResult:
    status: str  # "Successful" | "Failed" | "already_running" | "not_found"
    message: str


def extract_single_article(article_id: str) -> SingleExtractionResult:
    """Retries extraction for exactly one article — the per-article 'Retry
    extraction' button shown on Failed articles. Shares _extraction_lock
    with the bulk run_extraction_job(): if a bulk extraction is currently
    in progress, this refuses rather than launching a second Chromium
    instance to compete with it for resources. Runs synchronously — a
    single article is fast enough that BackgroundTasks isn't needed here,
    unlike the bulk case.
    """
    if not _extraction_lock.acquire(blocking=False):
        return SingleExtractionResult(
            status="already_running",
            message="An extraction is already in progress. Try again once it finishes.",
        )

    try:
        matches = repository.get_articles_by_ids([article_id])
        if not matches:
            return SingleExtractionResult(status="not_found", message="Article not found.")

        article = matches[0]
        repository.set_ingestion_status(article_id, "Pending")

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(user_agent=USER_AGENT, viewport={"width": 1280, "height": 800})
            page = context.new_page()
            status, text = _extract_one(page, str(article.url))
            browser.close()

        if status == "Successful":
            repository.set_article_content(article_id, text, status)
            OUTPUT_DIR.mkdir(exist_ok=True)
            filename = _safe_filename(article.title) + ".txt"
            (OUTPUT_DIR / filename).write_text(
                f"URL: {article.url}\nSTATUS: {status}\n\n{text}", encoding="utf-8"
            )
            return SingleExtractionResult(status="Successful", message="Extraction succeeded.")

        repository.set_ingestion_status(article_id, "Failed")
        return SingleExtractionResult(status="Failed", message=text)
    finally:
        repository.reset_pending_extraction_articles()
        _extraction_lock.release()