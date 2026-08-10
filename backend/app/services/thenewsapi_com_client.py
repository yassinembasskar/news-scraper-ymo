"""
TheNewsAPI.com (/v1/news/all) client.

Fifth source alongside GNews, NewsAPI.org, NewsData.io, and Currents (see
source_client.py for the shared RawArticle/FetchResult shape they all
normalize into, and scraper.py's SOURCES registry).

Confirmed against the live API (not just the vendor list) before writing
this — and this one's free tier is by far the tightest of the five:
  - Page size is hard-capped at 3 articles/request — a `limit` param is
    accepted but silently ignored; `meta.limit` in the response always
    comes back 3 regardless of what's requested.
  - `x-usagelimit-limit: 100` response header confirms a 100 requests/day
    budget total. Combined with the 3-article page size, MAX_PAGES is kept
    very small (2) so one run across 3 languages x several keyword chunks
    can't come close to exhausting the day's budget.
  - `published_after`/`published_before` only take a plain date
    (YYYY-MM-DD, no time component) — confirmed working for filtering, but
    coarser than the other sources' exact timestamps, so results are still
    filtered to the precise [date_from, date_to] window client-side here,
    same reasoning as newsdata_io_client.py.
  - `published_at` is ISO 8601 with microseconds ("...T23:53:54.000000Z"),
    parses fine with the same replace("Z", "+00:00") + fromisoformat used
    elsewhere.
  - `source` is just the bare domain (e.g. "yardbarker.com"), not a
    friendly publisher name, but still more useful than None so it's used
    as-is for source_name.
"""

import time
from datetime import datetime, timezone
from typing import List, Optional

import requests

from app.config import settings
from app.keywords import KEYWORDS_BY_LANGUAGE
from app.services.source_client import FetchResult, RawArticle, best_description

BASE_URL = "https://api.thenewsapi.com/v1/news/all"

MAX_QUERY_CHARS = 200          # confirmed live: 159 chars worked fine; kept a margin since the exact cap isn't documented
ARTICLES_PER_PAGE = 3           # confirmed live hard cap — the `limit` param is accepted but ignored
MAX_PAGES = 2                   # 100 requests/day total across all languages/chunks — kept low deliberately
REQUEST_DELAY_SECONDS = 1.0     # polite spacing; x-ratelimit-limit header suggested a short per-minute window too


class TheNewsApiComError(Exception):
    """Raised when a single language's fetch fails outright (non-200 after
    the 429 retry). Caught inside fetch_articles() so one language failing
    doesn't stop the others."""

    def __init__(self, language: str, status_code: int, body: str):
        self.language = language
        self.status_code = status_code
        self.body = body
        super().__init__(f"TheNewsAPI.com error for language='{language}': {status_code} {body}")


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
    """Returns quoted, pre-chunked search strings for one language, same
    reasoning as gnews_client.py's _build_queries."""
    keywords = KEYWORDS_BY_LANGUAGE.get(language, [])
    quoted = [f'"{kw}"' for kw in keywords]
    return ["(" + " OR ".join(chunk) + ")" for chunk in _chunk_keywords(quoted)]


def _parse_published_at(value: Optional[str]) -> datetime:
    """Parses TheNewsAPI.com's published_at ('YYYY-MM-DDTHH:MM:SS.ffffffZ')
    into a timezone-aware datetime."""
    if not value:
        raise ValueError("Missing published_at")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _get(params: dict, language: str) -> requests.Response:
    """Thin wrapper so a connection-level failure (timeout, DNS, reset —
    this API's Cloudflare edge was observed hanging past the timeout on a
    live test) raises the same error type as an HTTP-level failure, instead
    of an uncaught requests exception that would crash the whole multi-
    source scrape rather than just this language/source."""
    try:
        return requests.get(BASE_URL, params=params, timeout=30)
    except requests.exceptions.RequestException as e:
        raise TheNewsApiComError(language, 0, str(e)) from e


def _normalize(raw: dict, language: str) -> RawArticle:
    """Turns one raw TheNewsAPI.com article dict into a RawArticle. Raises
    (KeyError, ValueError) if a required field is missing/unparseable —
    caught by the caller and counted as malformed rather than propagated."""
    return RawArticle(
        url=raw["url"],
        title=raw.get("title", "") or "",
        # `snippet` is a longer truncated preview than the one-line
        # `description`, so prefer it when present. Never the FULL article
        # body; that's still extractor.py's job via a real page fetch.
        description=best_description(raw.get("snippet"), raw.get("description")),
        source_name=raw.get("source"),
        published_at=_parse_published_at(raw.get("published_at")),
        language=language,
    )


def _fetch_language(language: str, date_from: datetime, date_to: datetime) -> tuple:
    """Fetches articles for a single language across its keyword chunks and
    pages, normalized to RawArticle and filtered to [date_from, date_to]
    client-side (the API's own date params are date-only, coarser than what
    we need — see module docstring). Returns (articles, malformed_count).
    Raises TheNewsApiComError if a request fails even after the 429 retry.
    """
    all_articles: List[RawArticle] = []
    malformed_count = 0
    queries = _build_queries(language)

    for query in queries:
        page = 1
        while page <= MAX_PAGES:
            params = {
                "api_token": settings.thenewsapi_com_api_key,
                "search": query,
                "language": language,
                "published_after": date_from.date().isoformat(),
                "published_before": date_to.date().isoformat(),
                "sort": "published_at",
                "page": page,
            }

            response = _get(params, language)
            time.sleep(REQUEST_DELAY_SECONDS)

            if response.status_code == 429:
                time.sleep(5)
                response = _get(params, language)
                time.sleep(REQUEST_DELAY_SECONDS)

            if response.status_code != 200:
                raise TheNewsApiComError(language, response.status_code, response.text)

            data = response.json()
            results = data.get("data", []) or []
            for raw in results:
                try:
                    article = _normalize(raw, language)
                except (KeyError, ValueError):
                    malformed_count += 1
                    continue
                if date_from <= article.published_at <= date_to:
                    all_articles.append(article)

            if len(results) < ARTICLES_PER_PAGE:
                break  # last page reached
            page += 1

    return all_articles, malformed_count


def fetch_articles(date_from: datetime, date_to: datetime) -> FetchResult:
    """Fetches articles from TheNewsAPI.com across ALL configured languages
    for the given date range, normalized to RawArticle. Returns a
    FetchResult combining every language's articles plus a list of any
    per-language errors encountered — one language failing does not
    prevent the others from being attempted. No-ops (returns an empty
    result) if no API key is configured yet, so this source can be
    registered before the key exists without breaking the run.
    """
    result = FetchResult()

    if not settings.thenewsapi_com_api_key:
        return result

    for language in KEYWORDS_BY_LANGUAGE:
        try:
            articles, malformed_count = _fetch_language(language, date_from, date_to)
            result.articles.extend(articles)
            result.malformed_count += malformed_count
        except TheNewsApiComError as e:
            result.errors.append(str(e))

    return result
