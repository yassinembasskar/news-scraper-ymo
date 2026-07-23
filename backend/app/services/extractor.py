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
                text to its own file for manual review.
"""

import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from dateutil.relativedelta import relativedelta
from playwright.sync_api import sync_playwright
import trafilatura

from app import repository

OUTPUT_DIR = Path("extracted_articles")
TIMEOUT_MS = 30000
EXPIRY_MONTHS = 1

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
    status: str  # "success" | "partial" | "failed" | "skipped"
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
        return ExtractionSummary(
            status="skipped",
            message=f"{expired_count} article(s) expired. Nothing left to extract.",
            expired_articles=expired_count,
        )

    OUTPUT_DIR.mkdir(exist_ok=True)

    extracted_count = 0
    failed_count = 0
    errors: List[str] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(user_agent=USER_AGENT, viewport={"width": 1280, "height": 800})
        page = context.new_page()

        for article in candidates:
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

        browser.close()

    if failed_count == 0:
        run_status = "success"
    elif extracted_count == 0:
        run_status = "failed"
    else:
        run_status = "partial"

    message = (
        f"Extraction {run_status}: {expired_count} expired, "
        f"{extracted_count} extracted, {failed_count} failed."
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
    """Returns True if an extraction is currently in progress in this
    process. Used by the router both to give the user immediate feedback
    ("already running") and — via the lock itself — as the actual
    authoritative guard against overlapping runs."""
    return _extraction_lock.locked()


def run_extraction_job() -> None:
    """Background-task entrypoint: acquires the lock, runs the unchanged
    run_extraction(), then releases it. Nothing is waiting on a return
    value here — the endpoint that scheduled this already responded.
    Progress is only visible by reloading the article list and checking
    each article's ingestion_status; the final summary is logged
    server-side (docker compose logs backend) rather than shown in the UI.
    """
    if not _extraction_lock.acquire(blocking=False):
        # The router already checks is_extraction_running() before
        # scheduling this, so this should be rare — but it's the real
        # guard, not just a nicety, in case two requests land close
        # enough together to both pass that check.
        return
    try:
        summary = run_extraction()
        print(f"[extraction] {summary.message}")
    finally:
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