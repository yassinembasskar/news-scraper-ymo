"""
GNews API client.

Fetches articles from GNews across every configured language for a given
date range, normalized to RawArticle (see source_client.py) so scraper.py
never touches GNews's raw JSON shape. This module only knows how to talk to
the GNews API:
  - it does NOT decide what date range to use (that's `services/scraper.py`,
    which reads `last_scraped` from Mongo via `repository.py`)
  - it does NOT do keyword-match tagging (that's `services/keyword_matcher.py`)
  - it does NOT touch MongoDB at all

One language's requests failing (e.g. the daily quota runs out partway
through) does not abort the whole fetch — it's recorded as an error for that
language, and the remaining languages are still attempted. This is what lets
`scraper.py` report a "partial" run instead of only "success"/"failed".
"""

import time
from datetime import datetime
from typing import List, Optional

import requests

from app.config import settings
from app.keywords import KEYWORDS_BY_LANGUAGE
from app.services.source_client import FetchResult, RawArticle, best_description

BASE_URL = "https://gnews.io/api/v4/search"

MAX_QUERY_CHARS = 190          # safety margin under GNews's 200-char query limit
ARTICLES_PER_PAGE = 10         # free plan max per request
MAX_PAGES = 5                  # safety cap per query chunk
REQUEST_DELAY_SECONDS = 1.1    # stay under GNews's 1 request/sec limit


class GNewsAPIError(Exception):
    """Raised when a single language's fetch fails outright (non-200 after
    the 429 retry). Caught inside fetch_articles() so one language failing
    doesn't stop the others."""

    def __init__(self, language: str, status_code: int, body: str):
        self.language = language
        self.status_code = status_code
        self.body = body
        super().__init__(f"GNews API error for language='{language}': {status_code} {body}")


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
    """Returns quoted, pre-chunked query strings for one language. Each
    keyword is wrapped in double quotes for exact-phrase matching — without
    quotes, GNews treats a space as an AND operator, so a multi-word term
    like "wiring harness" would silently become wiring AND harness."""
    keywords = KEYWORDS_BY_LANGUAGE.get(language, [])
    quoted = [f'"{kw}"' for kw in keywords]
    return ["(" + " OR ".join(chunk) + ")" for chunk in _chunk_keywords(quoted)]


def _to_iso8601(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _parse_gnews_date(value: Optional[str]) -> datetime:
    """Parses GNews's publishedAt ('YYYY-MM-DDTHH:MM:SSZ', optionally with
    fractional seconds) into a timezone-aware datetime."""
    if not value:
        raise ValueError("Missing publishedAt")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _get(params: dict, language: str) -> requests.Response:
    """Thin wrapper so a connection-level failure (timeout, DNS, reset)
    raises the same error type as an HTTP-level failure, instead of an
    uncaught requests exception that would crash the whole multi-source
    scrape rather than just this language/source."""
    try:
        return requests.get(BASE_URL, params=params, timeout=30)
    except requests.exceptions.RequestException as e:
        raise GNewsAPIError(language, 0, str(e)) from e


def _normalize(raw: dict, language: str) -> RawArticle:
    """Turns one raw GNews article dict into a RawArticle. Raises
    (KeyError, ValueError) if a required field is missing/unparseable —
    caught by the caller and counted as malformed rather than propagated."""
    return RawArticle(
        url=raw["url"],
        title=raw.get("title", "") or "",
        # GNews's `content` is a truncated body preview ("...[+2893 chars]")
        # — richer than the one-line `description`, so prefer it when
        # present. Never the FULL article body; that's still extractor.py's
        # job via a real page fetch.
        description=best_description(raw.get("content"), raw.get("description")),
        source_name=(raw.get("source") or {}).get("name"),
        published_at=_parse_gnews_date(raw.get("publishedAt")),
        language=raw.get("lang", language),
    )


def _fetch_language(language: str, date_from: datetime, date_to: datetime) -> tuple:
    """Fetches all articles for a single language across its keyword chunks
    and pages, normalized to RawArticle. Returns (articles, malformed_count).
    Raises GNewsAPIError if a request fails even after the 429 retry."""
    all_articles: List[RawArticle] = []
    malformed_count = 0
    queries = _build_queries(language)

    for query in queries:
        page = 1
        while page <= MAX_PAGES:
            params = {
                "apikey": settings.gnews_api_key,
                "q": query,
                "in": "title",
                "lang": language,
                "max": ARTICLES_PER_PAGE,
                "page": page,
                "sortby": "publishedAt",
                "from": _to_iso8601(date_from),
                "to": _to_iso8601(date_to),
            }

            response = _get(params, language)
            time.sleep(REQUEST_DELAY_SECONDS)

            if response.status_code == 429:
                time.sleep(5)
                response = _get(params, language)
                time.sleep(REQUEST_DELAY_SECONDS)

            if response.status_code != 200:
                raise GNewsAPIError(language, response.status_code, response.text)

            data = response.json()
            results = data.get("articles", []) or []
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
    """Fetches articles from GNews across ALL configured languages for the
    given date range, normalized to RawArticle. Returns a FetchResult
    combining every language's articles plus a list of any per-language
    errors encountered — one language failing does not prevent the others
    from being attempted.
    """
    result = FetchResult()

    for language in KEYWORDS_BY_LANGUAGE:
        try:
            articles, malformed_count = _fetch_language(language, date_from, date_to)
            result.articles.extend(articles)
            result.malformed_count += malformed_count
        except GNewsAPIError as e:
            result.errors.append(str(e))

    return result