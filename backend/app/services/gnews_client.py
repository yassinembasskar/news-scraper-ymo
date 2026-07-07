"""
GNews API client.

Fetches raw articles from GNews across every configured language for a given
date range. This module only knows how to talk to the GNews API:
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
from dataclasses import dataclass, field
from datetime import datetime
from typing import List

import requests

from app.config import settings
from app.keywords import KEYWORDS_BY_LANGUAGE

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


@dataclass
class FetchResult:
    articles: List[dict] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)  # human-readable, one per failed language


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


def _fetch_language(language: str, date_from: datetime, date_to: datetime) -> List[dict]:
    """Fetches all raw articles for a single language across its keyword
    chunks and pages. Raises GNewsAPIError if a request fails even after
    the 429 retry."""
    all_articles: List[dict] = []
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

            response = requests.get(BASE_URL, params=params, timeout=30)
            time.sleep(REQUEST_DELAY_SECONDS)

            if response.status_code == 429:
                time.sleep(5)
                response = requests.get(BASE_URL, params=params, timeout=30)
                time.sleep(REQUEST_DELAY_SECONDS)

            if response.status_code != 200:
                raise GNewsAPIError(language, response.status_code, response.text)

            data = response.json()
            results = data.get("articles", []) or []
            for article in results:
                article.setdefault("lang", language)  # fallback if GNews ever omits it
            all_articles.extend(results)

            if len(results) < ARTICLES_PER_PAGE:
                break  # last page reached
            page += 1

    return all_articles


def fetch_articles(date_from: datetime, date_to: datetime) -> FetchResult:
    """Fetches raw articles from GNews across ALL configured languages for
    the given date range. Returns a FetchResult combining every language's
    articles plus a list of any per-language errors encountered — one
    language failing does not prevent the others from being attempted.
    """
    result = FetchResult()

    for language in KEYWORDS_BY_LANGUAGE:
        try:
            result.articles.extend(_fetch_language(language, date_from, date_to))
        except GNewsAPIError as e:
            result.errors.append(str(e))

    return result