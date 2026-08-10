"""
NewsData.io (/api/1/latest) client.

Third source alongside GNews and NewsAPI.org (see source_client.py for the
shared RawArticle/FetchResult shape they all normalize into, and
scraper.py's SOURCES registry).

Confirmed against the live API (not just docs) before writing this:
  - Free tier's /latest endpoint REJECTS from_date/to_date entirely (422
    UnsupportedParameter) — /archive supports date ranges but needs a paid
    plan. So unlike GNews/NewsAPI.org, this source can't ask the API to
    only return a date range; instead it fetches whatever "latest" has and
    filters to [date_from, date_to] client-side in _fetch_language, stopping
    pagination early once a page's articles are all older than date_from
    (results come back newest-first, so nothing further back is worth
    another request/credit).
  - The article URL field is `link`, not `url`; source name is a flat
    `source_name` (no nesting like GNews's source.name).
  - `pubDate` is "YYYY-MM-DD HH:MM:SS" with a separate `pubDateTZ` field
    (observed always "UTC") — no 'T'/'Z' the way GNews/NewsAPI.org format it.
  - The response's own `language` field is a full word ("english"), not the
    "en"/"fr"/"ar" codes this app uses — ignored in favor of the language
    we requested, same as gnews_client's fallback for its `lang` field.
  - Pagination is cursor-based: the response's `nextPage` token (not a page
    number) is passed back as the `page` param for the next request.
  - Free tier page size is 10 results/request (confirmed live), same order
    as GNews.
"""

import time
from datetime import datetime, timezone
from typing import List, Optional

import requests

from app.config import settings
from app.keywords import KEYWORDS_BY_LANGUAGE
from app.services.source_client import FetchResult, RawArticle

BASE_URL = "https://newsdata.io/api/1/latest"

MAX_QUERY_CHARS = 95           # confirmed live: 100 is the hard cap; -3 to leave room for _build_queries' wrapping "( )"
ARTICLES_PER_PAGE = 10          # free plan page size (confirmed live)
MAX_PAGES = 2                   # ~54 keywords/language at a 95-char budget makes ~11 chunks/language (33 total) —
                                 # kept low so one run can't come close to the 200-credit/day cap; usually cut even
                                 # shorter by the date-range early-stop below
REQUEST_DELAY_SECONDS = 1.0     # polite spacing; no documented per-second limit


class NewsDataIoError(Exception):
    """Raised when a single language's fetch fails outright (non-200 after
    the 429 retry). Caught inside fetch_articles() so one language failing
    doesn't stop the others."""

    def __init__(self, language: str, status_code: int, body: str):
        self.language = language
        self.status_code = status_code
        self.body = body
        super().__init__(f"NewsData.io error for language='{language}': {status_code} {body}")


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
    """Returns quoted, pre-chunked qInTitle strings for one language. Each
    keyword is wrapped in double quotes for exact-phrase matching, same
    reasoning as gnews_client.py's _build_queries."""
    keywords = KEYWORDS_BY_LANGUAGE.get(language, [])
    quoted = [f'"{kw}"' for kw in keywords]
    return ["(" + " OR ".join(chunk) + ")" for chunk in _chunk_keywords(quoted)]


def _parse_pub_date(value: Optional[str]) -> datetime:
    """Parses NewsData.io's pubDate ('YYYY-MM-DD HH:MM:SS', always UTC per
    the accompanying pubDateTZ field) into a timezone-aware datetime."""
    if not value:
        raise ValueError("Missing pubDate")
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def _get(params: dict, language: str) -> requests.Response:
    """Thin wrapper so a connection-level failure (timeout, DNS, reset)
    raises the same error type as an HTTP-level failure, instead of an
    uncaught requests exception that would crash the whole multi-source
    scrape rather than just this language/source."""
    try:
        return requests.get(BASE_URL, params=params, timeout=30)
    except requests.exceptions.RequestException as e:
        raise NewsDataIoError(language, 0, str(e)) from e


def _normalize(raw: dict, language: str) -> RawArticle:
    """Turns one raw NewsData.io result dict into a RawArticle. Raises
    (KeyError, ValueError) if a required field is missing/unparseable —
    caught by the caller and counted as malformed rather than propagated."""
    return RawArticle(
        url=raw["link"],
        title=raw.get("title", "") or "",
        description=raw.get("description"),
        source_name=raw.get("source_name"),
        published_at=_parse_pub_date(raw.get("pubDate")),
        language=language,
    )


def _fetch_language(language: str, date_from: datetime, date_to: datetime) -> tuple:
    """Fetches articles for a single language across its keyword chunks and
    pages, normalized to RawArticle and filtered to [date_from, date_to]
    client-side (the free /latest endpoint can't do this server-side — see
    module docstring). Returns (articles, malformed_count). Raises
    NewsDataIoError if a request fails even after the 429 retry."""
    all_articles: List[RawArticle] = []
    malformed_count = 0
    queries = _build_queries(language)

    for query in queries:
        next_page: Optional[str] = None
        page_count = 0

        while page_count < MAX_PAGES:
            params = {
                "apikey": settings.newsdata_io_api_key,
                "qInTitle": query,
                "language": language,
            }
            if next_page:
                params["page"] = next_page

            response = _get(params, language)
            time.sleep(REQUEST_DELAY_SECONDS)

            if response.status_code == 429:
                time.sleep(5)
                response = _get(params, language)
                time.sleep(REQUEST_DELAY_SECONDS)

            if response.status_code != 200:
                raise NewsDataIoError(language, response.status_code, response.text)

            data = response.json()
            results = data.get("results", []) or []
            page_count += 1

            page_articles: List[RawArticle] = []
            for raw in results:
                try:
                    page_articles.append(_normalize(raw, language))
                except (KeyError, ValueError):
                    malformed_count += 1

            all_articles.extend(a for a in page_articles if date_from <= a.published_at <= date_to)

            next_page = data.get("nextPage")
            # Results come back newest-first, so once every article on this
            # page is already older than the window, nothing on a further
            # page could be newer — stop instead of spending more credits.
            page_all_too_old = bool(page_articles) and all(a.published_at < date_from for a in page_articles)
            if not next_page or not results or page_all_too_old:
                break

    return all_articles, malformed_count


def fetch_articles(date_from: datetime, date_to: datetime) -> FetchResult:
    """Fetches articles from NewsData.io across ALL configured languages,
    normalized to RawArticle and filtered to the given date range
    client-side. Returns a FetchResult combining every language's articles
    plus a list of any per-language errors encountered — one language
    failing does not prevent the others from being attempted. No-ops
    (returns an empty result) if no API key is configured yet, so this
    source can be registered before the key exists without breaking the
    run.
    """
    result = FetchResult()

    if not settings.newsdata_io_api_key:
        return result

    for language in KEYWORDS_BY_LANGUAGE:
        try:
            articles, malformed_count = _fetch_language(language, date_from, date_to)
            result.articles.extend(articles)
            result.malformed_count += malformed_count
        except NewsDataIoError as e:
            result.errors.append(str(e))

    return result
