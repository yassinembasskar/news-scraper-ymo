"""
Data-access layer.

Every Mongo query in the backend lives here. Routers and services call these
functions instead of importing `articles`/`meta` and writing queries inline —
that way there's exactly one place to change if a query or the dedup
strategy ever needs to change.
"""

from datetime import datetime, timezone
from typing import List, Optional

from app.database import articles, meta
from app.models import ArticleCreate, ArticleOut, ScrapeStatus
from app.database import articles, meta, db 

META_DOC_ID = "scrape_status"


def _serialize_article(doc: dict) -> dict:
    """Converts a raw Mongo document into a dict matching ArticleOut's shape."""
    doc = dict(doc)
    doc["id"] = str(doc.pop("_id"))
    return doc


def upsert_article(article: ArticleCreate) -> bool:
    """Inserts a new article, or updates it in place if the url already
    exists (this is the dedup mechanism for repeated/overlapping scrape
    runs). Returns True if this was a new article, False if it updated
    an existing one.
    """
    data = article.model_dump()
    data["url"] = str(data["url"])  # HttpUrl -> plain string for storage

    result = articles.update_one(
        {"url": data["url"]},
        {"$set": data},
        upsert=True,
    )
    return result.upserted_id is not None


def get_articles(
    language: Optional[str] = None,
    keyword: Optional[str] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> List[ArticleOut]:
    """Returns articles matching the given filters, newest first.
    Any filter left as None is simply not applied."""
    query: dict = {}

    if language:
        query["language"] = language

    if keyword:
        query["matched_keywords"] = keyword

    if date_from or date_to:
        date_filter = {}
        if date_from:
            date_filter["$gte"] = date_from
        if date_to:
            date_filter["$lte"] = date_to
        query["published_at"] = date_filter

    cursor = articles.find(query).sort("published_at", -1)
    return [ArticleOut(**_serialize_article(doc)) for doc in cursor]


def get_scrape_status() -> Optional[ScrapeStatus]:
    """Returns the current scrape status, or None if the scraper has
    never run yet."""
    doc = meta.find_one({"_id": META_DOC_ID})
    if doc is None:
        return None
    doc = dict(doc)
    doc.pop("_id")
    return ScrapeStatus(**doc)


def set_scrape_status(status: ScrapeStatus) -> None:
    """Upserts the single scrape-status document."""
    meta.update_one(
        {"_id": META_DOC_ID},
        {"$set": status.model_dump()},
        upsert=True,
    )


def log_scrape_run(
    status: str,
    message: str,
    date_from: Optional[datetime],
    date_to: Optional[datetime],
    new_articles: int,
    duplicate_articles: int,
    excluded_articles: int,
    malformed_articles: int,
    errors: List[str],
) -> None:
    """Appends a permanent record of one scrape run to the scrape_runs
    collection. Unlike the `meta` document (overwritten every run), this is
    an insert — every run gets its own record, enabling a run history."""
    db["scrape_runs"].insert_one({
        "status": status,
        "message": message,
        "date_from": date_from,
        "date_to": date_to,
        "new_articles": new_articles,
        "duplicate_articles": duplicate_articles,
        "excluded_articles": excluded_articles,
        "malformed_articles": malformed_articles,
        "errors": errors,
        "logged_at": datetime.now(timezone.utc),
    })

def get_articles_for_stats(
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> List[dict]:
    """Returns a lean projection of articles for stats/bucketing purposes —
    only published_at, language, and matched_keywords, not full ArticleOut
    objects (no need for url/description/etc. just to count things).
    All bucketing math (hour/day/week grouping, Morocco timezone conversion,
    month-boundary week-splitting, top-keyword tallying) happens in
    services/stats.py, not here — this function only fetches raw data.
    """
    query: dict = {}
    if date_from or date_to:
        date_filter = {}
        if date_from:
            date_filter["$gte"] = date_from
        if date_to:
            date_filter["$lte"] = date_to
        query["published_at"] = date_filter

    projection = {"_id": 0, "published_at": 1, "language": 1, "matched_keywords": 1}
    return list(articles.find(query, projection))

