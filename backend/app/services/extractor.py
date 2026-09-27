"""
Extraction — page-load/text-pull logic shared by services/preprocessing.py's
per-cluster extraction phase (which reuses _extract_one, _extraction_lock,
and the other pieces below directly) and this module's own
extract_single_article(), the per-article "Retry extraction" action shown
on Failed articles.

The bulk "Extract Articles" stage this module originally implemented
(run_extraction/run_extraction_job, an expire-then-extract-everything
workflow triggered by its own button) was retired in favor of
preprocessing.py's per-cluster extraction phase — extraction now only ever
runs on clustered articles as part of that job, or on one article at a
time via extract_single_article() below. Automatic content expiration
(marking old articles' content wiped after a calendar month) was retired
with it (2026-09-10): freeing space by deleting old content is now a
choice for the user to make deliberately, not something the pipeline
decides on its own.

Playwright + trafilatura loads each article's page and pulls its main body
text (same pipeline validated in test_extraction.py). 'Failed' is only
ever set after an extraction was actually attempted for that article (page
load errored, or the page loaded but no body text could be pulled out of
it — e.g. a paywall, cookie wall, or bot/protection wall).
"""

import re
import threading
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import sync_playwright
import trafilatura

from app import repository

OUTPUT_DIR = Path("extracted_articles")
TIMEOUT_MS = 30000

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0"
)

_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*]')

# Guards against two extraction runs executing at once — the bulk
# per-cluster phase in preprocessing.py and this module's single-article
# retry both acquire this before launching a Chromium instance; whichever
# can't get it just refuses/no-ops instead of racing the other one for
# the same browser resources.
_extraction_lock = threading.Lock()


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


@dataclass
class SingleExtractionResult:
    status: str  # "Successful" | "Failed" | "already_running" | "not_found"
    message: str


def extract_single_article(article_id: str) -> SingleExtractionResult:
    """Retries extraction for exactly one article — the per-article 'Retry
    extraction' button shown on Failed articles. Shares _extraction_lock
    with preprocessing.py's bulk per-cluster extraction phase: if that's
    currently in progress, this refuses rather than launching a second
    Chromium instance to compete with it for resources. Runs
    synchronously — a single article is fast enough that BackgroundTasks
    isn't needed here.
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