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

# Same reasoning as test_extraction.py: a realistic user agent reduces the
# odds of a site treating an obviously headless browser differently than a
# normal visitor.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0"
)

_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*]')


@dataclass
class ExtractionSummary:
    status: str  # "success" | "partial" | "failed" | "skipped"
    message: str
    expired_articles: int = 0
    extracted_articles: int = 0
    failed_articles: int = 0
    errors: List[str] = field(default_factory=list)


def _safe_filename(title: str) -> str:
    """Sanitizes a title for use as a filename (strips characters the
    filesystem disallows) and caps it at 40 chars per the workflow's naming
    convention."""
    cleaned = _INVALID_FILENAME_CHARS.sub("_", title).strip()
    return cleaned[:40] or "untitled"


def _extract_one(page, url: str) -> tuple:
    """Returns (status, text) for one URL. Never raises — failures are
    captured and returned as a 'Failed' status instead."""
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
    """Runs one full extraction pass and returns a summary. Safe to call
    repeatedly — 'Successful'/'Failed'/'Expired' articles are left
    untouched by the extraction step itself (aside from the expiry sweep,
    which can move any of them to 'Expired')."""
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
