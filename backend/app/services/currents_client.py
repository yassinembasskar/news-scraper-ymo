"""
Currents API (/v1/search) client.

Fourth source alongside GNews, NewsAPI.org, and NewsData.io (see
source_client.py for the shared RawArticle/FetchResult shape they all
normalize into, and scraper.py's SOURCES registry).

Confirmed against the live API (not just the vendor list) before writing
this:
  - start_date/end_date must be RFC 3339 ("...T00:00:00Z"), and the range
    between them is capped at 7 days per request (a 400 INVALID_QUERY
    otherwise) — unlike GNews/NewsAPI.org, which accept an arbitrary range
    in one call. _chunk_date_range() below splits a longer catch-up range
    (e.g. after the app hasn't run in a while) into <=7-day windows, each
    fetched as its own request.
  - Article objects have NO source/publisher field at all (checked the full
    key list) — only `author`, which is usually empty. source_name is left
    None for this source rather than guessed from the URL domain.
  - `published` is "YYYY-MM-DD HH:MM:SS +0000", not GNews/NewsAPI.org's
    'T'/'Z' ISO form.
  - Pagination is a plain `page_number` integer, fixed page size 20
    (confirmed live); paginate until a page returns fewer than 20.
"""

import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import requests

from app.config import settings
from app.keywords import KEYWORDS_BY_LANGUAGE
from app.services.source_client import FetchResult, RawArticle

BASE_URL = "https://api.currentsapi.services/v1/search"

MAX_QUERY_CHARS = 200          # confirmed live: 159 chars worked fine; kept a margin since the exact cap isn't documented
ARTICLES_PER_PAGE = 20          # confirmed live page size
MAX_PAGES = 3                   # safety cap per query chunk per date window
MAX_WINDOW_DAYS = 7             # confirmed live: the API's own hard cap on start_date..end_date
REQUEST_DELAY_SECONDS = 1.0     # polite spacing; no documented per-second limit


class CurrentsApiError(Exception):
    """Raised when a single language's fetch fails outright (non-200 after
    the 429 retry). Caught inside fetch_articles() so one language failing
    doesn't stop the others."""

    def __init__(self, language: str, status_code: int, body: str):
        self.language = language
        self.status_code = status_code
        self.body = body
        super().__init__(f"Currents API error for language='{language}': {status_code} {body}")


def _chunk_keywords(keywords: List[str], max_chars: int = MAX_QUERY_CHARS, joiner: str = " OR ") -> List[List[str]]:
    """Splits a keyword list into groups that each fit within max_chars once joined."""
    chunks: List[List[str]] = []
    current: List[str] = []
    for kw in keywords:
        candidate = current + [kw]
        if len(joiner.join(candidate)) > max_chars and current:
            chunks.append(current)
            current = [kw]
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _build_queries(language: str) -> List[str]:
    """Returns quoted, pre-chunked keyword strings for one language, same
    reasoning as gnews_client.py's _build_queries."""
    keywords = KEYWORDS_BY_LANGUAGE.get(language, [])
    quoted = [f'"{kw}"' for kw in keywords]
    return ["(" + " OR ".join(chunk) + ")" for chunk in _chunk_keywords(quoted)]


def _chunk_date_range(date_from: datetime, date_to: datetime, max_days: int = MAX_WINDOW_DAYS) -> List[tuple]:
    """Splits [date_from, date_to] into consecutive windows no longer than
    max_days, since the API rejects any single request spanning more."""
    windows = []
    window_start = date_from
    step = timedelta(days=max_days)
    while window_start < date_to:
        window_end = min(window_start + step, date_to)
        windows.append((window_start, window_end))
        window_start = window_end
    return windows or [(date_from, date_to)]


def _to_rfc3339(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_published(value: Optional[str]) -> datetime:
    """Parses Currents' published ('YYYY-MM-DD HH:MM:SS +0000') into a
    timezone-aware datetime."""
    if not value:
        raise ValueError("Missing published")
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S %z")


def _get(params: dict, language: str) -> requests.Response:
    """Thin wrapper so a connection-level failure (timeout, DNS, reset)
    raises the same error type as an HTTP-level failure, instead of an
    uncaught requests exception that would crash the whole multi-source
    scrape rather than just this language/source."""
    try:
        return requests.get(BASE_URL, params=params, timeout=30)
    except requests.exceptions.RequestException as e:
        raise CurrentsApiError(language, 0, str(e)) from e


def _normalize(raw: dict, language: str) -> RawArticle:
    """Turns one raw Currents article dict into a RawArticle. Raises
    (KeyError, ValueError) if a required field is missing/unparseable —
    caught by the caller and counted as malformed rather than propagated."""
    return RawArticle(
        url=raw["url"],
        title=raw.get("title", "") or "",
        description=raw.get("description"),
        source_name=None,  # Currents doesn't expose a publisher/source field
        published_at=_parse_published(raw.get("published")),
        language=language,
    )


def _fetch_language(language: str, date_from: datetime, date_to: datetime) -> tuple:
    """Fetches all articles for a single language across its keyword
    chunks, date windows, and pages, normalized to RawArticle. Returns
    (articles, malformed_count). Raises CurrentsApiError if a request fails
    even after the 429 retry."""
    all_articles: List[RawArticle] = []
    malformed_count = 0
    queries = _build_queries(language)
    windows = _chunk_date_range(date_from, date_to)

    for query in queries:
        for window_start, window_end in windows:
            page = 1
            while page <= MAX_PAGES:
                params = {
                    "apiKey": settings.currents_api_key,
                    "keywords": query,
                    "language": language,
                    "start_date": _to_rfc3339(window_start),
                    "end_date": _to_rfc3339(window_end),
                    "page_number": page,
                }

                response = _get(params, language)
                time.sleep(REQUEST_DELAY_SECONDS)

                if response.status_code == 429:
                    time.sleep(5)
                    response = _get(params, language)
                    time.sleep(REQUEST_DELAY_SECONDS)

                if response.status_code != 200:
                    raise CurrentsApiError(language, response.status_code, response.text)

                data = response.json()
                results = data.get("news", []) or []
                for raw in results:
                    try:
                        all_articles.append(_normalize(raw, language))
                    except (KeyError, ValueError):
                        malformed_count += 1

                if len(results) < ARTICLES_PER_PAGE:
                    break  # last page reached
                page += 1

    return all_articles, malformed_count


def fetch_articles(date_from: datetime, date_to: datetime) -> FetchResult:
    """Fetches articles from Currents across ALL configured languages for
    the given date range, normalized to RawArticle. Returns a FetchResult
    combining every language's articles plus a list of any per-language
    errors encountered — one language failing does not prevent the others
    from being attempted. No-ops (returns an empty result) if no API key is
    configured yet, so this source can be registered before the key exists
    without breaking the run.
    """
    result = FetchResult()

    if not settings.currents_api_key:
        return result

    for language in KEYWORDS_BY_LANGUAGE:
        try:
            articles, malformed_count = _fetch_language(language, date_from, date_to)
            result.articles.extend(articles)
            result.malformed_count += malformed_count
        except CurrentsApiError as e:
            result.errors.append(str(e))

    return result
