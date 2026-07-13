"""
Data-access layer.

Every Mongo query in the backend lives here. Routers and services call these
functions instead of importing `articles`/`meta` and writing queries inline —
that way there's exactly one place to change if a query or the dedup
strategy ever needs to change.
"""

from datetime import datetime, timezone
from typing import List, Optional

from bson import ObjectId
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

    ingestion_status and content are deliberately excluded from the
    overwrite: they belong to the extraction workflow, not to GNews data.
    An article re-fetched by a later scrape (e.g. during a catch-up retry
    that re-covers a date range) must never wipe out previously-extracted
    content or reset its status back to "No Extraction" — $setOnInsert
    only applies these on a genuinely new document.
    """
    data = article.model_dump()
    data["url"] = str(data["url"])  # HttpUrl -> plain string for storage

    ingestion_status = data.pop("ingestion_status")
    content = data.pop("content")

    result = articles.update_one(
        {"url": data["url"]},
        {
            "$set": data,
            "$setOnInsert": {"ingestion_status": ingestion_status, "content": content},
        },
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


def get_articles_by_ids(article_ids: List[str]) -> List[ArticleOut]:
    """Fetches specific articles by their Mongo _id — used when the user
    selects specific articles to scrape."""
    object_ids = [ObjectId(i) for i in article_ids]
    cursor = articles.find({"_id": {"$in": object_ids}})
    return [ArticleOut(**_serialize_article(doc)) for doc in cursor]


def set_ingestion_status(article_id: str, status: str) -> None:
    """Updates just the ingestion status for one article — e.g. to
    'Pending' when a scrape job starts."""
    articles.update_one(
        {"_id": ObjectId(article_id)},
        {"$set": {"ingestion_status": status}},
    )


def set_ingestion_status_bulk(article_ids: List[str], status: str) -> None:
    """Bulk version of set_ingestion_status — e.g. marking a whole batch as
    'Pending' at once, or a whole batch as 'Expired' at once, when the user
    selects several articles and some/all fall in the same bucket."""
    if not article_ids:
        return
    object_ids = [ObjectId(i) for i in article_ids]
    articles.update_many(
        {"_id": {"$in": object_ids}},
        {"$set": {"ingestion_status": status}},
    )


def set_article_content(article_id: str, content: str, status: str) -> None:
    """Stores extracted (or manually pasted) content and its resulting
    status together, so the two fields are never left inconsistent with
    each other (e.g. content present but status still 'Pending')."""
    articles.update_one(
        {"_id": ObjectId(article_id)},
        {"$set": {"content": content, "ingestion_status": status}},
    )


def expire_old_content(cutoff: datetime) -> int:
    """Purges content and marks as 'Expired' every article published before
    `cutoff`, regardless of its current ingestion status (so a stale
    'No Extraction'/'Failed' article is excluded from future extraction
    attempts, not just previously-'Successful' ones). Runs as step 1 of the
    Extract Articles workflow, every time it's clicked — there is no
    separate scheduler for this. Already-'Expired' articles are left alone
    (nothing to change). Returns how many articles were affected.
    """
    result = articles.update_many(
        {
            "published_at": {"$lt": cutoff},
            "ingestion_status": {"$ne": "Expired"},
        },
        {"$set": {"content": None, "ingestion_status": "Expired"}},
    )
    return result.modified_count


def get_articles_pending_extraction() -> List[ArticleOut]:
    """Returns every article still in its initial 'No Extraction' state —
    the candidate pool for the Extract Articles workflow. Called after
    expire_old_content(), so anything past the cutoff has already been
    flipped to 'Expired' and won't show up here."""
    cursor = articles.find({"ingestion_status": "No Extraction"})
    return [ArticleOut(**_serialize_article(doc)) for doc in cursor]