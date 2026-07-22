"""
Data-access layer.

Every Mongo query in the backend lives here. Routers and services call these
functions instead of importing `articles`/`meta` and writing queries inline —
that way there's exactly one place to change if a query or the dedup
strategy ever needs to change.

One deliberate exception: expire_old_content() also deletes the expiring
articles' vectors from Pinecone, right alongside the Mongo update that
clears their embedding fields — kept together so the two can never drift
out of sync with each other (a Mongo document claiming "Not Embedded"
while stale vectors linger in Pinecone, or vice versa).
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from bson import ObjectId
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from pinecone import Pinecone

from app.config import settings
from app.database import articles, meta
from app.models import ArticleCreate, ArticleOut, EmbeddingRunStatus, ScrapeStatus
from app.database import articles, meta, db

META_DOC_ID = "scrape_status"
META_EMBEDDING_DOC_ID = "embedding_status"
# Must match services/embedder.py's PINECONE_INDEX_NAME — the embedding job
# and this cleanup path have to agree on which index they're both touching.
PINECONE_INDEX_NAME = "yazaki"
PINECONE_DELETE_BATCH_SIZE = 100


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

    ingestion_status, content, and the embedding fields (chunk_ids,
    number_of_chunks, embedding_status) are deliberately excluded from the
    overwrite: they belong to the extraction/embedding workflows, not to
    GNews data. An article re-fetched by a later scrape (e.g. during a
    catch-up retry that re-covers a date range) must never wipe out
    previously-extracted content, previously-embedded chunks, or reset
    their statuses back to their initial values — $setOnInsert only
    applies these on a genuinely new document.
    """
    data = article.model_dump()
    data["url"] = str(data["url"])  # HttpUrl -> plain string for storage

    set_on_insert = {
        key: data.pop(key)
        for key in ("ingestion_status", "content", "chunk_ids", "number_of_chunks", "embedding_status")
    }

    result = articles.update_one(
        {"url": data["url"]},
        {
            "$set": data,
            "$setOnInsert": set_on_insert,
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


def get_embedding_status() -> Optional[EmbeddingRunStatus]:
    """Returns the current embedding-job status, or None if the embedding
    job has never run yet."""
    doc = meta.find_one({"_id": META_EMBEDDING_DOC_ID})
    if doc is None:
        return None
    doc = dict(doc)
    doc.pop("_id")
    return EmbeddingRunStatus(**doc)


def set_embedding_status(status: EmbeddingRunStatus) -> None:
    """Upserts the single embedding-status document."""
    meta.update_one(
        {"_id": META_EMBEDDING_DOC_ID},
        {"$set": status.model_dump()},
        upsert=True,
    )


def set_embedding_in_progress(value: bool) -> None:
    """Flips just the in_progress flag on the embedding-status document,
    leaving last_run_at/last_run_status untouched. This is the actual
    overlap guard for the embedding job (the equivalent of extraction's
    in-process threading.Lock, but Mongo-backed so it survives a process
    restart) — the job sets this True right after confirming no run is
    already in progress, and resets it False in a finally block whether
    the run succeeded or failed."""
    meta.update_one(
        {"_id": META_EMBEDDING_DOC_ID},
        {"$set": {"in_progress": value}},
        upsert=True,
    )


def set_embedding_progress(total: int, processed: int) -> None:
    """Updates the live progress counters on the embedding-status document
    — called once at the start of a run (processed=0) and again after each
    article finishes, so the frontend can poll and show "X/Y done"."""
    meta.update_one(
        {"_id": META_EMBEDDING_DOC_ID},
        {"$set": {"total": total, "processed": processed}},
        upsert=True,
    )


def set_embedding_stop_requested(value: bool) -> None:
    """Flips the stop_requested flag — set True by the stop endpoint, read
    by the run loop between articles, reset back to False once a run
    actually stops (whether via the flag or finishing naturally)."""
    meta.update_one(
        {"_id": META_EMBEDDING_DOC_ID},
        {"$set": {"stop_requested": value}},
        upsert=True,
    )


def reset_pending_embedding_articles() -> int:
    """Resets any article stuck at embedding_status "Pending" back to "Not
    Embedded" — called whenever a run stops (naturally, via a stop request,
    or after a crash) so no article is left in a state that only makes
    sense while a run is actively working on it. Returns the count reset."""
    result = articles.update_many(
        {"embedding_status": "Pending"},
        {"$set": {"embedding_status": "Not Embedded"}},
    )
    return result.modified_count


def set_embedding_rate(avg_seconds_per_article: float) -> None:
    """Updates the exponential moving average of seconds-per-article on the
    embedding-status document — see EmbeddingRunStatus.avg_seconds_per_article
    for why this is recency-weighted rather than a plain run-long average."""
    meta.update_one(
        {"_id": META_EMBEDDING_DOC_ID},
        {"$set": {"avg_seconds_per_article": avg_seconds_per_article}},
        upsert=True,
    )


def set_embedding_started(started_at: datetime) -> None:
    """Records when the current run began — used to estimate time
    remaining (elapsed / processed * articles left) once progress starts
    coming in. Called once, right before the per-article loop starts."""
    meta.update_one(
        {"_id": META_EMBEDDING_DOC_ID},
        {"$set": {"started_at": started_at}},
        upsert=True,
    )


def set_embedding_run_outcome(last_run_at: datetime, last_run_status: str) -> None:
    """Records the outcome of the most recent embedding run without
    touching in_progress — kept separate from set_embedding_in_progress()
    so the two can be called at different points in the job (this one
    once the run's work is done, that one in the guarding finally block)
    without either clobbering the other."""
    meta.update_one(
        {"_id": META_EMBEDDING_DOC_ID},
        {"$set": {"last_run_at": last_run_at, "last_run_status": last_run_status}},
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


def log_embedding_run(
    status: str,
    message: str,
    embedded_articles: int,
    failed_articles: int,
    errors: List[str],
) -> None:
    """Appends a permanent record of one embedding run to the
    embedding_runs collection — the embedding-job counterpart to
    log_scrape_run(). Every run gets its own record, enabling a run
    history, unlike the `meta` embedding-status document which is
    overwritten every run."""
    db["embedding_runs"].insert_one({
        "status": status,
        "message": message,
        "embedded_articles": embedded_articles,
        "failed_articles": failed_articles,
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


def set_article_embedding_result(article_id: str, chunk_ids: List[str], status: str) -> None:
    """Stores the result of embedding one article's chunks into Pinecone.
    number_of_chunks is derived from len(chunk_ids) rather than taken as a
    separate argument, so the two can never drift out of sync with each
    other. The ingestion_status: "Successful" filter is a hard guard, not
    just belt-and-suspenders: a Failed/Pending/Expired article has no real
    content, so it must never be marked Pending/Embedded — if the caller
    somehow passes one anyway (e.g. a stale candidate list), this silently
    no-ops instead of corrupting that article's state."""
    articles.update_one(
        {"_id": ObjectId(article_id), "ingestion_status": "Successful"},
        {"$set": {
            "chunk_ids": chunk_ids,
            "number_of_chunks": len(chunk_ids),
            "embedding_status": status,
        }},
    )


def set_article_embedding_status(article_id: str, status: str) -> None:
    """Updates just the embedding status for one article, without
    touching chunk_ids/number_of_chunks — used on failure, where nothing
    actually changed in Pinecone, so any existing chunk_ids (e.g. from an
    earlier partial attempt) are deliberately left alone rather than
    wiped or guessed at. Same ingestion_status: "Successful" guard as
    set_article_embedding_result() — see its docstring."""
    articles.update_one(
        {"_id": ObjectId(article_id), "ingestion_status": "Successful"},
        {"$set": {"embedding_status": status}},
    )


def get_articles_pending_embedding() -> List[ArticleOut]:
    """Returns every successfully-extracted article that hasn't been
    embedded yet — the candidate pool for the embedding job. Mirrors
    get_articles_pending_extraction(); embedding_status != "Embedded"
    (rather than == "Not Embedded") means a previously-"Failed" article is
    naturally retried on the next run instead of needing a separate retry
    path."""
    cursor = articles.find({
        "ingestion_status": "Successful",
        "embedding_status": {"$ne": "Embedded"},
    })
    return [ArticleOut(**_serialize_article(doc)) for doc in cursor]


def expire_old_content(cutoff: datetime) -> int:
    """Purges content and marks as 'Expired' every article published before
    `cutoff`, regardless of its current ingestion status (so a stale
    'No Extraction'/'Failed' article is excluded from future extraction
    attempts, not just previously-'Successful' ones). Runs as step 1 of the
    Extract Articles workflow, every time it's clicked — there is no
    separate scheduler for this. Already-'Expired' articles are left alone
    (nothing to change).

    Also deletes each expiring article's vectors from Pinecone (using its
    stored chunk_ids) and resets chunk_ids/number_of_chunks/embedding_status
    back to their pre-embedding defaults, so the Mongo document accurately
    reflects that nothing is in Pinecone anymore once content is cleared.
    Pinecone deletes are batched across all expiring articles in one pass
    (rather than one delete call per article) — cheaper, and there's no
    correctness reason to keep them separate since every expiring article
    gets the same reset regardless of how many chunks it had.

    Returns how many articles were affected.
    """
    candidates = list(articles.find(
        {"published_at": {"$lt": cutoff}, "ingestion_status": {"$ne": "Expired"}},
        {"chunk_ids": 1},
    ))

    if not candidates:
        return 0

    chunk_ids_to_delete = [
        chunk_id
        for doc in candidates
        for chunk_id in doc.get("chunk_ids", [])
    ]

    if chunk_ids_to_delete:
        pc = Pinecone(api_key=settings.pinecone_api_key)
        index = pc.Index(PINECONE_INDEX_NAME)
        for i in range(0, len(chunk_ids_to_delete), PINECONE_DELETE_BATCH_SIZE):
            index.delete(ids=chunk_ids_to_delete[i : i + PINECONE_DELETE_BATCH_SIZE])

    result = articles.update_many(
        {"_id": {"$in": [doc["_id"] for doc in candidates]}},
        {"$set": {
            "content": None,
            "ingestion_status": "Expired",
            "chunk_ids": [],
            "number_of_chunks": 0,
            "embedding_status": "Not Embedded",
        }},
    )
    return result.modified_count


def get_articles_pending_extraction() -> List[ArticleOut]:
    """Returns every article still in its initial 'No Extraction' state —
    the candidate pool for the Extract Articles workflow. Called after
    expire_old_content(), so anything past the cutoff has already been
    flipped to 'Expired' and won't show up here."""
    cursor = articles.find({"ingestion_status": "No Extraction"})
    return [ArticleOut(**_serialize_article(doc)) for doc in cursor]