"""
API client.

Every HTTP call to the FastAPI backend lives here — Streamlit components
call these functions and get back plain dicts/lists, never construct a URL
or call `requests` themselves. This mirrors repository.py's role on the
backend: one centralized place to change if an endpoint's path or shape
ever changes.
"""

from typing import List, Optional

import requests

from config import settings

DEFAULT_TIMEOUT = 30
# /update can take a while: GNews rate-limiting adds a deliberate ~1.1s
# delay per request, across all languages and keyword chunks.
UPDATE_TIMEOUT = 120
# /articles/extract loads each pending article in a real browser — slower
# per-item than /update, and scales with however many articles are pending.
EXTRACT_TIMEOUT = 300


def _get(path: str, params: Optional[dict] = None, timeout: int = DEFAULT_TIMEOUT) -> dict:
    response = requests.get(f"{settings.backend_api_url}{path}", params=params, timeout=timeout)
    response.raise_for_status()
    return response.json()


def _post(path: str, params: Optional[dict] = None, timeout: int = DEFAULT_TIMEOUT) -> dict:
    response = requests.post(f"{settings.backend_api_url}{path}", params=params, timeout=timeout)
    response.raise_for_status()
    return response.json()


def _put(path: str, json: Optional[dict] = None, timeout: int = DEFAULT_TIMEOUT) -> dict:
    response = requests.put(f"{settings.backend_api_url}{path}", json=json, timeout=timeout)
    response.raise_for_status()
    return response.json()


# ── Health ──────────────────────────────────────────────────────────────

def get_health() -> dict:
    return _get("/health")


# ── Scrape / update ─────────────────────────────────────────────────────

def trigger_update() -> dict:
    """Triggers one incremental scrape. Uses a longer timeout than other
    calls since a real run can take a while."""
    return _post("/update", timeout=UPDATE_TIMEOUT)


def get_scrape_status() -> dict:
    return _get("/scrape-status")


# ── Extraction ──────────────────────────────────────────────────────────

def trigger_extraction() -> dict:
    """Triggers one extraction pass (expire stale content, then extract
    every 'No Extraction' article). Uses a longer timeout since it loads
    each pending article in a real browser."""
    return _post("/articles/extract", timeout=EXTRACT_TIMEOUT)


# ── Articles ────────────────────────────────────────────────────────────

def get_articles(
    language: Optional[str] = None,
    keyword: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> List[dict]:
    """date_from/date_to are ISO strings. When drilling down from a clicked
    chart bucket, pass its date_from/date_to straight through — no parsing
    or reformatting needed, since the backend already returns them ready
    to use for exactly this purpose."""
    params = {}
    if language:
        params["language"] = language
    if keyword:
        params["keyword"] = keyword
    if date_from:
        params["date_from"] = date_from
    if date_to:
        params["date_to"] = date_to
    return _get("/articles", params=params)


def submit_manual_content(article_id: str, content: str) -> dict:
    """Manually pastes an article's content — typically after automatic
    extraction failed. Stored identically to a successful extraction."""
    return _put(f"/articles/{article_id}/content", json={"content": content})


# ── Stats ───────────────────────────────────────────────────────────────

def get_overview() -> dict:
    return _get("/stats/overview")


def get_day_chart(iso_date: str) -> dict:
    """iso_date: 'YYYY-MM-DD'."""
    return _get("/stats/chart/day", params={"date": iso_date})


def get_week_chart(iso_year: int, iso_week: int) -> dict:
    return _get("/stats/chart/week", params={"iso_year": iso_year, "iso_week": iso_week})


def get_month_chart(year: int, month: int) -> dict:
    return _get("/stats/chart/month", params={"year": year, "month": month})


def get_bucket_summary(date_from: str, date_to: str) -> dict:
    """date_from/date_to are ISO strings — same pass-through pattern as
    get_articles, typically taken directly from a clicked chart bucket."""
    return _get("/stats/bucket-summary", params={"date_from": date_from, "date_to": date_to})