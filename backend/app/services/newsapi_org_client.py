"""
NewsAPI.org (/v2/everything) client.

Second source alongside GNews (see source_client.py for the shared
RawArticle/FetchResult shape both normalize into, and scraper.py's SOURCES
registry). Same per-language, per-keyword-chunk, per-page fetch pattern as
gnews_client.py, adapted to NewsAPI.org's own query syntax, limits, and
response shape:
  - q max 500 chars (vs GNews's 200) -> fewer keyword chunks needed
  - pageSize max 100 (vs GNews's 10) -> fewer pages needed per chunk
  - free "Developer" plan cannot page past the first ~100 results per
    query (a 426 Upgrade Required past that) -> capped at MAX_PAGES=1 here
    rather than treating it as a hard error worth aborting the language over

NOTE: NewsAPI.org's free plan is documented as development/testing use
only, not for a live/production product — accepted knowingly for this
project rather than something to silently build around.
"""

import time
from datetime import datetime
from typing import List, Optional

import requests

from app.config import settings
from app.keywords import KEYWORDS_BY_LANGUAGE
from app.services.source_client import FetchResult, RawArticle, best_description

BASE_URL = "https://newsapi.org/v2/everything"

MAX_QUERY_CHARS = 490          # safety margin under NewsAPI.org's 500-char q limit
ARTICLES_PER_PAGE = 100        # free plan max per request
MAX_PAGES = 1                  # free plan can't page past ~100 results/query anyway
REQUEST_DELAY_SECONDS = 1.0    # polite spacing; no documented per-second limit like GNews's


class NewsApiOrgError(Exception):
    """Raised when a single language's fetch fails outright (non-200 after
    the 429 retry). Caught inside fetch_articles() so one language failing
    doesn't stop the others."""

    def __init__(self, language: str, status_code: int, body: str):
        self.language = language
        self.status_code = status_code
        self.body = body
        super().__init__(f"NewsAPI.org error for language='{language}': {status_code} {body}")


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
    keyword is wrapped in double quotes for exact-phrase matching, same
    reasoning as gnews_client.py's _build_queries."""
    keywords = KEYWORDS_BY_LANGUAGE.get(language, [])
    quoted = [f'"{kw}"' for kw in keywords]
    return ["(" + " OR ".join(chunk) + ")" for chunk in _chunk_keywords(quoted)]


def _to_iso8601(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _parse_published_at(value: Optional[str]) -> datetime:
    """Parses NewsAPI.org's publishedAt ('YYYY-MM-DDTHH:MM:SSZ') into a
    timezone-aware datetime."""
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
        raise NewsApiOrgError(language, 0, str(e)) from e


def _normalize(raw: dict, language: str) -> RawArticle:
    """Turns one raw NewsAPI.org article dict into a RawArticle. Raises
    (KeyError, ValueError) if a required field is missing/unparseable —
    caught by the caller and counted as malformed rather than propagated."""
    return RawArticle(
        url=raw["url"],
        title=raw.get("title", "") or "",
        # NewsAPI.org's `content` is truncated to 200 chars ("...[+N
        # chars]") — richer than the one-line `description`, so prefer it
        # when present. Never the FULL article body; that's still
        # extractor.py's job via a real page fetch.
        description=best_description(raw.get("content"), raw.get("description")),
        source_name=(raw.get("source") or {}).get("name"),
        published_at=_parse_published_at(raw.get("publishedAt")),
        language=language,
    )


def _fetch_language(language: str, date_from: datetime, date_to: datetime) -> tuple:
    """Fetches all articles for a single language across its keyword chunks
    and pages, normalized to RawArticle. Returns (articles, malformed_count).
    Raises NewsApiOrgError if a request fails even after the 429 retry."""
    all_articles: List[RawArticle] = []
    malformed_count = 0
    queries = _build_queries(language)

    for query in queries:
        page = 1
        while page <= MAX_PAGES:
            params = {
                "apiKey": settings.newsapi_org_api_key,
                "q": query,
                "searchIn": "title",
                "language": language,
                "pageSize": ARTICLES_PER_PAGE,
                "page": page,
                "sortBy": "publishedAt",
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
                raise NewsApiOrgError(language, response.status_code, response.text)

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
    """Fetches articles from NewsAPI.org across ALL configured languages for
    the given date range, normalized to RawArticle. Returns a FetchResult
    combining every language's articles plus a list of any per-language
    errors encountered — one language failing does not prevent the others
    from being attempted. No-ops (returns an empty result) if no API key is
    configured yet, so this source can be registered before the key exists
    without breaking the run.
    """
    result = FetchResult()

    if not settings.newsapi_org_api_key:
        return result

    for language in KEYWORDS_BY_LANGUAGE:
        try:
            articles, malformed_count = _fetch_language(language, date_from, date_to)
            result.articles.extend(articles)
            result.malformed_count += malformed_count
        except NewsApiOrgError as e:
            result.errors.append(str(e))

    return result
