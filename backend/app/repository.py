"""
Data-access layer.

Every Mongo query in the backend lives here. Routers and services call these
functions instead of importing `articles`/`meta` and writing queries inline —
that way there's exactly one place to change if a query or the dedup
strategy ever needs to change.
"""

import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
from bson import ObjectId
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from pinecone import Pinecone

from app.config import settings
from app.database import articles, meta
from app.models import (
    ArticleCreate,
    ArticleOut,
    Cluster,
    ClusteringRunStatus,
    ClusterInsight,
    Entity,
    ExtractionRunStatus,
    ScrapeStatus,
    SimilarClusterRef,
    TitleGateRunStatus,
)
from app.database import articles, clusters, entities as entities_collection, meta, db

META_DOC_ID = "scrape_status"
META_EXTRACTION_DOC_ID = "extraction_status"
META_TITLE_GATE_DOC_ID = "title_gate_status"
META_CLUSTERING_DOC_ID = "clustering_status"
# Condensation's chunk embeddings (upsert_condensed_chunk_embeddings, below)
# and title-gate/clustering embeddings share this one Pinecone index,
# distinguished by metadata — one index for the whole app, not one per
# producer. (The old per-article embedding stage that used to share it too
# — services/embedder.py — was retired 2026-09-15 along with the
# tree-walk relativity scorer it only ever existed to feed.)
PINECONE_INDEX_NAME = "yazaki"
PINECONE_UPSERT_BATCH_SIZE = 100


def _serialize_article(doc: dict) -> dict:
    """Converts a raw Mongo document into a dict matching ArticleOut's shape."""
    doc = dict(doc)
    doc["id"] = str(doc.pop("_id"))
    return doc


def upsert_article(article: ArticleCreate) -> bool:
    """Inserts a new article, or updates it in place if the url already
    exists (this is the primary dedup mechanism for repeated/overlapping
    scrape runs). Returns True if this was a new article, False if it
    updated an existing one or was skipped as a cross-source duplicate.

    A second, title-based dedup layer sits in front of that: an exact-title
    match under a *different* url is treated as the same story republished/
    syndicated by another outlet (verified live — one wire story appeared
    under 16 distinct domains with an identical title), not a genuinely
    different article. The earliest-published version wins:
      - a candidate the same age or newer than the stored version is a
        later syndicated copy — skipped, never written.
      - a candidate that's genuinely earlier takes over the existing
        document (same _id, so nothing downstream needs to change) only if
        that document hasn't been extracted yet — safe, since there's no
        extracted content tied to a specific page yet.
      - a candidate that's earlier but the existing document has already
        been processed is still skipped, to avoid mismatching already-
        extracted content against a different URL.

    ingestion_status and content are deliberately excluded from any
    overwrite in either path: they belong to the extraction workflow, not
    to scrape data. An article re-fetched by a later scrape (e.g. during a
    catch-up retry that re-covers a date range) must never wipe out
    previously-extracted content or reset its status back to its initial
    value — $setOnInsert only applies these on a genuinely new document.
    """
    data = article.model_dump()
    data["url"] = str(data["url"])  # HttpUrl -> plain string for storage

    set_on_insert = {
        key: data.pop(key)
        for key in ("title_gate_status", "ingestion_status", "content")
    }

    existing_by_title = articles.find_one(
        {"title": data["title"], "url": {"$ne": data["url"]}},
        {"published_at": 1, "ingestion_status": 1},
    )
    if existing_by_title:
        existing_published_at = existing_by_title["published_at"]
        if existing_published_at.tzinfo is None:
            existing_published_at = existing_published_at.replace(tzinfo=timezone.utc)

        if existing_published_at <= data["published_at"]:
            return False  # same age or newer than the stored version — later syndicated copy, skip

        if existing_by_title.get("ingestion_status") != "No Extraction":
            return False  # earlier, but the stored one is already processed — leave it canonical

        # Earlier, and the stored document is still untouched — take it
        # over instead of inserting a second document for the same story.
        # But the new url might already belong to some OTHER document (e.g.
        # this exact story already reached the collection under this url,
        # via a different title variant, on an earlier run) — blindly
        # reassigning url here would collide with that document's unique
        # url and crash the whole scrape (confirmed live: a real
        # DuplicateKeyError on the url index). Skip the takeover instead if
        # that's the case, same as any other duplicate.
        if articles.find_one({"url": data["url"], "_id": {"$ne": existing_by_title["_id"]}}, {"_id": 1}):
            return False
        articles.update_one({"_id": existing_by_title["_id"]}, {"$set": data})
        return False

    result = articles.update_one(
        {"url": data["url"]},
        {
            "$set": data,
            "$setOnInsert": set_on_insert,
        },
        upsert=True,
    )
    return result.upserted_id is not None


def article_exists(url: str) -> bool:
    """Cheap existence check by URL."""
    return articles.find_one({"url": url}, {"_id": 1}) is not None


def get_articles_pending_title_gate() -> List[ArticleOut]:
    """Returns every article still in "No Extraction" that hasn't been
    through the title gate yet — the candidate pool for the title-gate job
    (services/title_gate.py). Scoped to ingestion_status == "No Extraction"
    so this only ever touches the live, ongoing scrape backlog — anything
    already Successful/Failed/Archived was either already gated, or
    predates this stage entirely and is out of scope for the routine job.

    Sorted oldest-published-first (2026-09-08) so gating naturally works
    through earlier days before later ones — preprocessing's clustering
    phase relies on this ordering: it refuses to cluster a day until every
    article published on it (and every earlier day) has been gated (see
    get_earliest_incomplete_title_gate_date())."""
    cursor = articles.find({
        "ingestion_status": "No Extraction",
        "title_gate_status": {"$ne": "Gated"},
    }).sort("published_at", 1)
    return [ArticleOut(**_serialize_article(doc)) for doc in cursor]


def get_earliest_incomplete_title_gate_date() -> Optional[str]:
    """Returns the earliest UTC day ("YYYY-MM-DD") that still has an
    article not yet through the title gate, or None if everything
    scraped so far has been gated. Preprocessing's clustering phase uses
    this to enforce day-ordering: a day is only clustered once every
    article published on it — and every earlier day — has a title-gate
    decision; a day at or after this cutoff is left alone until gating
    catches up, rather than clustering an incomplete picture of that
    day's news.

    Scoped to ingestion_status == "No Extraction", matching
    get_articles_pending_title_gate() exactly — without this, a
    pre-title-gate-era article (scraped before this stage existed, and
    since moved on to Successful/Failed/Archived through the old
    pipeline) would count as "pending" forever, since the routine job
    intentionally never touches it (see that function's docstring). Left
    unscoped, this cutoff would freeze at that article's date permanently
    and clustering would never proceed past it."""
    doc = articles.find(
        {"ingestion_status": "No Extraction", "title_gate_status": {"$ne": "Gated"}},
        {"published_at": 1},
    ).sort("published_at", 1).limit(1)
    doc = next(doc, None)
    if doc is None:
        return None
    return doc["published_at"].date().isoformat()


def get_title_gate_day_progress() -> dict:
    """Day-level view of the title-gate backlog, for the dashboard —
    {days_gated, days_total, current_day, gated_total, archived_total}.
    The job itself works one article at a time, oldest-published-first
    (not day by day); this aggregates that into whole-day terms:
    days_total is every distinct day in the same "No Extraction" scope
    get_articles_pending_title_gate() uses, days_gated is how many of
    those days are now fully gated (every article on that day has a
    decision), and current_day is the earliest day that isn't (same value
    get_earliest_incomplete_title_gate_date() would return, computed here
    in the same aggregation instead of a second query).

    gated_total/archived_total/articles_total are unscoped (every article
    that exists, gated or not — not just the live "No Extraction"
    backlog) — an archived article's ingestion_status is "Archived", not
    "No Extraction", so it can never appear in the day-level aggregation
    above; these are separate, cheap indexed counts instead.
    archived_total is shown against articles_total (the whole corpus),
    not gated_total, so the figure reads as "how much of everything has
    been archived so far" and grows steadily as gating progresses, rather
    than against the tiny, early-run gated_total denominator."""
    pipeline = [
        {"$match": {"ingestion_status": "No Extraction"}},
        {"$group": {
            "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$published_at"}},
            "total": {"$sum": 1},
            "gated": {"$sum": {"$cond": [{"$eq": ["$title_gate_status", "Gated"]}, 1, 0]}},
        }},
    ]
    by_day = list(articles.aggregate(pipeline))
    days_total = len(by_day)
    days_gated = sum(1 for d in by_day if d["gated"] == d["total"])
    incomplete_days = sorted(d["_id"] for d in by_day if d["gated"] < d["total"])
    archived_total = articles.count_documents({"ingestion_status": "Archived"})
    gated_total = articles.count_documents({"title_gate_status": "Gated"})
    articles_total = articles.count_documents({})
    return {
        "days_gated": days_gated,
        "days_total": days_total,
        "current_day": incomplete_days[0] if incomplete_days else None,
        "gated_total": gated_total,
        "archived_total": archived_total,
        "articles_total": articles_total,
    }


def get_clustering_day_progress() -> dict:
    """Day-level view of the clustering backlog, for the dashboard —
    {days_clustered, days_total}. days_total is every distinct day among
    relevant (non-archived) articles regardless of gating status yet (the
    full universe clustering will eventually need to cover, growing as
    scraping/gating continues); days_clustered is how many of those days
    already have at least one cluster in the `clusters` collection."""
    total_days_pipeline = [
        {"$match": {"ingestion_status": {"$ne": "Archived"}}},
        {"$group": {"_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$published_at"}}}},
    ]
    days_total = len(list(articles.aggregate(total_days_pipeline)))
    days_clustered = len(clusters.distinct("date"))
    return {"days_clustered": days_clustered, "days_total": days_total}


def get_preprocessing_phase_totals() -> dict:
    """Live, always-correct backlog totals for every preprocessing phase —
    computed straight from `articles`/`clusters`, the same way
    get_clustering_day_progress()'s days_clustered/days_total already are.

    This exists because the *_done counters on the clustering_status meta
    document (ClusteringRunStatus) are a per-run snapshot, not a backlog
    total — they reset to 0 at the start of every run and only accumulate
    while that run is active. Displaying them whenever no run is active
    (or after that document is cleared/reset) makes a fully-processed
    backlog look untouched, which is misleading rather than merely stale:
    real work already done disappears from view for no actual reason.
    Called on every status poll instead, so the Preprocessing stage card
    always reflects reality regardless of run/meta-document state."""
    articles_extracted_done = articles.count_documents({
        "cluster_id": {"$ne": None}, "ingestion_status": "Successful",
    })
    clusters_condensed_done = clusters.count_documents({"condensed": True})
    clusters_entities_done = clusters.count_documents({"entities_extracted": True})
    clusters_contextualized_done = clusters.count_documents({"contextualized": True})
    clusters_insights_done = clusters.count_documents({"insights_generated": True})
    return {
        "articles_extracted_done": articles_extracted_done,
        "clusters_condensed_done": clusters_condensed_done,
        "clusters_entities_done": clusters_entities_done,
        "clusters_contextualized_done": clusters_contextualized_done,
        "clusters_insights_done": clusters_insights_done,
    }


def get_preprocessing_quality_metrics() -> dict:
    """Aggregate quality signals for each preprocessing phase, computed
    fresh from `clusters`/`entities` every call — meant to be watched
    *while* a large backlog run is in progress, not just at the end, so a
    badly-mistuned threshold (e.g. a contextualization hit rate near 0%
    or near 100%, an insight yield rate that looks implausible) surfaces
    early rather than after the whole backlog has already been spent on
    it. Each phase's numbers are only over clusters that have actually
    reached that phase — a day 1 of the backlog run, when only condensing
    has processed anything yet, correctly reports zero-count (not
    zero-rate) for every later phase, distinguishable via `clusters_processed`."""
    condensing_agg = list(clusters.aggregate([
        {"$match": {"condensed": True}},
        {"$group": {
            "_id": None,
            "count": {"$sum": 1},
            "avg_chunks_per_cluster": {"$avg": {"$size": "$chunks"}},
            "avg_titles_per_cluster": {"$avg": {"$size": "$titles"}},
            "zero_chunk_clusters": {"$sum": {"$cond": [{"$eq": [{"$size": "$chunks"}, 0]}, 1, 0]}},
        }},
    ]))
    condensing = condensing_agg[0] if condensing_agg else {}

    entities_agg = list(clusters.aggregate([
        {"$match": {"entities_extracted": True}},
        {"$group": {
            "_id": None,
            "count": {"$sum": 1},
            "avg_entities_per_cluster": {"$avg": {"$size": "$entities"}},
        }},
    ]))
    entities_summary = entities_agg[0] if entities_agg else {}
    total_canonical_entities = entities_collection.count_documents({"entity_group": {"$ne": "TOPIC"}})

    context_agg = list(clusters.aggregate([
        {"$match": {"contextualized": True}},
        {"$group": {
            "_id": None,
            "count": {"$sum": 1},
            "with_similar": {"$sum": {"$cond": [{"$gt": [{"$size": "$similar_clusters"}, 0]}, 1, 0]}},
            # similar_clusters is stored highest-score-first (see
            # contextualization.find_similar_clusters), so index 0 is
            # each cluster's own best match.
            "avg_top_score": {"$avg": {"$arrayElemAt": ["$similar_clusters.score", 0]}},
        }},
    ]))
    contextualization = context_agg[0] if context_agg else {}

    insights_summary_agg = list(clusters.aggregate([
        {"$match": {"insights_generated": True}},
        {"$group": {
            "_id": None,
            "count": {"$sum": 1},
            "with_insights": {"$sum": {"$cond": [{"$gt": [{"$size": "$insights"}, 0]}, 1, 0]}},
            "total_insights": {"$sum": {"$size": "$insights"}},
        }},
    ]))
    insights_summary = insights_summary_agg[0] if insights_summary_agg else {}

    insight_detail_agg = list(clusters.aggregate([
        {"$match": {"insights_generated": True}},
        {"$unwind": "$insights"},
        {"$group": {
            "_id": None,
            "avg_confidence": {"$avg": "$insights.confidence"},
            "avg_chunk_citations": {"$avg": {"$size": "$insights.chunk_indices"}},
        }},
    ]))
    insight_detail = insight_detail_agg[0] if insight_detail_agg else {}

    def pct(numerator: int, denominator: int) -> Optional[float]:
        return round(100 * numerator / denominator, 1) if denominator else None

    def rounded(value, digits=1):
        return round(value, digits) if value is not None else None

    return {
        "condensing": {
            "clusters_processed": condensing.get("count", 0),
            "avg_chunks_per_cluster": rounded(condensing.get("avg_chunks_per_cluster")),
            "avg_titles_per_cluster": rounded(condensing.get("avg_titles_per_cluster")),
            "zero_chunk_clusters": condensing.get("zero_chunk_clusters", 0),
        },
        "entities": {
            "clusters_processed": entities_summary.get("count", 0),
            "avg_entities_per_cluster": rounded(entities_summary.get("avg_entities_per_cluster")),
            "total_canonical_entities": total_canonical_entities,
        },
        "contextualization": {
            "clusters_processed": contextualization.get("count", 0),
            "hit_rate_pct": pct(contextualization.get("with_similar", 0), contextualization.get("count", 0)),
            "avg_top_score": rounded(contextualization.get("avg_top_score"), 2),
        },
        "insights": {
            "clusters_processed": insights_summary.get("count", 0),
            "yield_rate_pct": pct(insights_summary.get("with_insights", 0), insights_summary.get("count", 0)),
            "total_insights": insights_summary.get("total_insights", 0),
            "avg_confidence": rounded(insight_detail.get("avg_confidence")),
            "avg_chunk_citations": rounded(insight_detail.get("avg_chunk_citations")),
        },
    }


def get_day_cluster_overview() -> List[dict]:
    """One row per UTC calendar day that has at least one article, oldest
    first — the day-by-day browsing view (routers/preprocessing.py's GET
    /days-overview), replacing an article-level day browser with a
    cluster-level one. Per day: how many articles total, how many have a
    title-gate decision yet, how many of those were archived (failed the
    gate) vs passed, and — once clustering has run for that day — how
    many clusters the passed articles resolved into.

    `gated`/`archived`/extraction counts come from one aggregation over
    `articles`; cluster counts come from a separate one over `clusters`
    grouped by its own `date` field (already scoped to non-archived,
    gated articles by construction — see services/preprocessing.py's
    phase 1). Two aggregations, not a $lookup join, since both are cheap
    at this scale and the merge in Python is trivial.

    extraction_success_rate is successful / (successful + failed) among
    that day's passed (non-archived) articles — None (not 0) when nothing
    has been attempted yet, so the frontend can distinguish "no data"
    from "0% success"."""
    by_day_pipeline = [
        {"$group": {
            "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$published_at"}},
            "total": {"$sum": 1},
            "gated": {"$sum": {"$cond": [{"$eq": ["$title_gate_status", "Gated"]}, 1, 0]}},
            "archived": {"$sum": {"$cond": [{"$eq": ["$ingestion_status", "Archived"]}, 1, 0]}},
            "extracted_successful": {"$sum": {"$cond": [{"$eq": ["$ingestion_status", "Successful"]}, 1, 0]}},
            "extraction_attempted": {"$sum": {
                "$cond": [{"$in": ["$ingestion_status", ["Successful", "Failed"]]}, 1, 0]
            }},
        }},
    ]
    by_day = {row["_id"]: row for row in articles.aggregate(by_day_pipeline)}

    cluster_counts = {
        row["_id"]: row["count"]
        for row in clusters.aggregate([{"$group": {"_id": "$date", "count": {"$sum": 1}}}])
    }

    overview = []
    for date_key in sorted(by_day.keys()):
        row = by_day[date_key]
        gated = row["gated"]
        archived = row["archived"]
        attempted = row["extraction_attempted"]
        overview.append({
            "date": date_key,
            "total": row["total"],
            "gated": gated,
            "archived": archived,
            "passed": gated - archived,
            "is_gate_complete": gated == row["total"],
            "extracted_successful": row["extracted_successful"],
            "extraction_attempted": attempted,
            "extraction_success_rate": (row["extracted_successful"] / attempted) if attempted else None,
            "cluster_count": cluster_counts.get(date_key, 0),
            "is_clustered": date_key in cluster_counts,
        })
    return overview


def get_articles(
    language: Optional[str] = None,
    keyword: Optional[str] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    include_archived: bool = False,
) -> List[ArticleOut]:
    """Returns articles matching the given filters, newest first.
    Any filter left as None is simply not applied.

    keyword is a case-insensitive substring search against the article's
    title and, when it has one, its extracted content — not a lookup
    against matched_keywords (the tag recorded at scrape time for whichever
    search term originally found the article). This means the filter finds
    the keyword anywhere it actually appears in the article, not just
    articles that happen to have been tagged with it as an exact keyword.

    include_archived=False (the default) excludes ingestion_status ==
    "Archived" articles — the title-gate LLM decided at scrape time these
    couldn't plausibly affect the tracked business, so they never reach
    extraction and stay out of the default list. Pass True to audit them
    (e.g. checking for false negatives)."""
    query: dict = {}

    if not include_archived:
        query["ingestion_status"] = {"$ne": "Archived"}

    if language:
        query["language"] = language

    if keyword:
        pattern = re.escape(keyword)
        query["$or"] = [
            {"title": {"$regex": pattern, "$options": "i"}},
            {"content": {"$regex": pattern, "$options": "i"}},
        ]

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
    archived_articles: int = 0,
    duration_seconds: Optional[float] = None,
) -> None:
    """Appends a permanent record of one scrape run to the scrape_runs
    collection. Unlike the `meta` document (overwritten every run), this is
    an insert — every run gets its own record, enabling a run history.
    duration_seconds (wall-clock, start to finish of run_scrape()) feeds
    the Logs page's time-per-phase breakdown — see routers/logs.py."""
    db["scrape_runs"].insert_one({
        "status": status,
        "message": message,
        "date_from": date_from,
        "date_to": date_to,
        "new_articles": new_articles,
        "duplicate_articles": duplicate_articles,
        "excluded_articles": excluded_articles,
        "malformed_articles": malformed_articles,
        "archived_articles": archived_articles,
        "errors": errors,
        "duration_seconds": duration_seconds,
        "logged_at": datetime.now(timezone.utc),
    })


def log_extraction_run(
    status: str,
    message: str,
    expired_articles: int,
    extracted_articles: int,
    failed_articles: int,
    errors: List[str],
) -> None:
    """Appends a permanent record of one extraction run to the
    extraction_runs collection — the extraction-job counterpart to
    log_scrape_run()."""
    db["extraction_runs"].insert_one({
        "status": status,
        "message": message,
        "expired_articles": expired_articles,
        "extracted_articles": extracted_articles,
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


def reset_pending_extraction_articles() -> int:
    """Resets any article stuck at ingestion_status "Pending" back to "No
    Extraction" — called whenever an extraction run stops (naturally or
    after a crash) so no article is left in a state that only makes sense
    while a run is actively working on it. Without this, such an article
    would never be picked up again, since get_articles_pending_extraction()
    only looks for "No Extraction". Returns the count reset."""
    result = articles.update_many(
        {"ingestion_status": "Pending"},
        {"$set": {"ingestion_status": "No Extraction"}},
    )
    return result.modified_count


def get_status_field_counts(field: str, query: Optional[dict] = None) -> dict:
    """Returns {status_value: count} for the given article field (e.g.
    "ingestion_status"), restricted to `query` if given. The raw counts
    behind the overview bar's per-stage stats — one call per stage, each
    with its own query scoping it to that stage's actual candidate pool."""
    pipeline = []
    if query:
        pipeline.append({"$match": query})
    pipeline.append({"$group": {"_id": f"${field}", "count": {"$sum": 1}}})
    return {doc["_id"]: doc["count"] for doc in articles.aggregate(pipeline)}


def get_running_pipeline_stage() -> Optional[str]:
    """Returns which pipeline stage — 'title gating', 'preprocessing', or
    'extraction' — currently has its in_progress flag set, or None if none
    do. Used to enforce that only one stage runs at a time: title gating
    and preprocessing's own insights phase (6) both hit the same single
    Ollama instance, the rest of preprocessing (clustering/condensing/
    entities/contextualization) is CPU work over already-computed
    embeddings, and extraction drives Playwright/Chromium — all heavy, and
    each already guards against overlapping with itself; this is the same
    guard extended across stages, so a click on one stage's trigger while
    another is still running waits instead of fighting it for CPU."""
    for stage, doc_id in (
        ("title gating", META_TITLE_GATE_DOC_ID),
        ("preprocessing", META_CLUSTERING_DOC_ID),
        ("extraction", META_EXTRACTION_DOC_ID),
    ):
        if meta.find_one({"_id": doc_id, "in_progress": True}):
            return stage
    return None


def get_extraction_status() -> Optional[ExtractionRunStatus]:
    """Returns the current extraction-job status, or None if the
    extraction job has never run yet."""
    doc = meta.find_one({"_id": META_EXTRACTION_DOC_ID})
    if doc is None:
        return None
    doc = dict(doc)
    doc.pop("_id")
    return ExtractionRunStatus(**doc)


def set_extraction_in_progress(value: bool) -> None:
    """Flips just the in_progress flag on the extraction-status document —
    Mongo-backed so it survives a process restart. extractor.py's
    in-process threading.Lock is still the hard guard against two
    Playwright/Chromium instances racing in the same process; this flag is
    what the frontend polls for progress and what a restart resets (see
    main.py's lifespan)."""
    meta.update_one(
        {"_id": META_EXTRACTION_DOC_ID},
        {"$set": {"in_progress": value}},
        upsert=True,
    )


def set_extraction_progress(total: int, processed: int) -> None:
    """Updates the live progress counters on the extraction-status document
    — called once at the start of a run (processed=0) and again after each
    article finishes, so the frontend can poll and show "X/Y done"."""
    meta.update_one(
        {"_id": META_EXTRACTION_DOC_ID},
        {"$set": {"total": total, "processed": processed}},
        upsert=True,
    )


def set_extraction_stop_requested(value: bool) -> None:
    """Flips the stop_requested flag — set True by the stop endpoint, read
    by the run loop between articles, reset back to False once a run
    actually stops (whether via the flag or finishing naturally). An
    article the loop never reaches after a stop stays at whatever status
    it already had ("No Extraction" for one never touched this run) — it
    is never marked "Failed" just because the run stopped before reaching
    it."""
    meta.update_one(
        {"_id": META_EXTRACTION_DOC_ID},
        {"$set": {"stop_requested": value}},
        upsert=True,
    )


def set_extraction_rate(avg_seconds_per_article: float) -> None:
    """Updates the exponential moving average of seconds-per-article on the
    extraction-status document — see ExtractionRunStatus.avg_seconds_per_article
    for why this is recency-weighted rather than a plain run-long average."""
    meta.update_one(
        {"_id": META_EXTRACTION_DOC_ID},
        {"$set": {"avg_seconds_per_article": avg_seconds_per_article}},
        upsert=True,
    )


def set_extraction_started(started_at: datetime) -> None:
    """Records when the current run began — used to estimate time
    remaining once progress starts coming in. Called once, right before
    the per-article loop starts."""
    meta.update_one(
        {"_id": META_EXTRACTION_DOC_ID},
        {"$set": {"started_at": started_at}},
        upsert=True,
    )


def set_extraction_run_outcome(last_run_at: datetime, last_run_status: str) -> None:
    """Records the outcome of the most recent extraction run without
    touching in_progress — kept separate from set_extraction_in_progress()
    so the two can be called at different points in the job without either
    clobbering the other."""
    meta.update_one(
        {"_id": META_EXTRACTION_DOC_ID},
        {"$set": {"last_run_at": last_run_at, "last_run_status": last_run_status}},
        upsert=True,
    )


# ── Title-gate job status ────────────────────────────────────────────────
# Mirrors the extraction-status helpers above one-for-one, on the
# title_gate_status meta document.

def get_title_gate_status() -> Optional[TitleGateRunStatus]:
    """Returns the current title-gate-job status, or None if it has never
    run yet."""
    doc = meta.find_one({"_id": META_TITLE_GATE_DOC_ID})
    if doc is None:
        return None
    doc = dict(doc)
    doc.pop("_id")
    return TitleGateRunStatus(**doc)


def set_title_gate_in_progress(value: bool) -> None:
    """Flips just the in_progress flag on the title-gate-status document —
    Mongo-backed overlap guard, same as set_extraction_in_progress()."""
    meta.update_one(
        {"_id": META_TITLE_GATE_DOC_ID},
        {"$set": {"in_progress": value}},
        upsert=True,
    )


def set_title_gate_progress(total: int, processed: int) -> None:
    """Updates the live progress counters on the title-gate-status document."""
    meta.update_one(
        {"_id": META_TITLE_GATE_DOC_ID},
        {"$set": {"total": total, "processed": processed}},
        upsert=True,
    )


def set_title_gate_stop_requested(value: bool) -> None:
    """Flips the stop_requested flag — same cooperative-stop pattern as the
    other stages."""
    meta.update_one(
        {"_id": META_TITLE_GATE_DOC_ID},
        {"$set": {"stop_requested": value}},
        upsert=True,
    )


def set_title_gate_rate(avg_seconds_per_article: float) -> None:
    """Updates the exponential moving average of seconds-per-article on the
    title-gate-status document."""
    meta.update_one(
        {"_id": META_TITLE_GATE_DOC_ID},
        {"$set": {"avg_seconds_per_article": avg_seconds_per_article}},
        upsert=True,
    )


def set_title_gate_started(started_at: datetime) -> None:
    """Records when the current title-gate run began."""
    meta.update_one(
        {"_id": META_TITLE_GATE_DOC_ID},
        {"$set": {"started_at": started_at}},
        upsert=True,
    )


def set_title_gate_run_outcome(last_run_at: datetime, last_run_status: str) -> None:
    """Records the outcome of the most recent title-gate run without
    touching in_progress — see set_extraction_run_outcome()."""
    meta.update_one(
        {"_id": META_TITLE_GATE_DOC_ID},
        {"$set": {"last_run_at": last_run_at, "last_run_status": last_run_status}},
        upsert=True,
    )


def set_title_gate_result(
    article_id: str,
    archived: bool,
    llm_relevant: Optional[bool],
    model_relevant: bool,
    title_embedding: Optional[List[float]],
) -> None:
    """Records one article's title-gate outcome: title_gate_status always
    flips to "Gated" (so it's never re-picked-up by the job), and if the
    decision (see services/title_gate.py — the model alone as of
    2026-08-26) judged it irrelevant, ingestion_status also flips to
    "Archived" in the same update — the two must always change together,
    since an archived article's story is entirely "this was gated and
    rejected".

    Also persists whichever decisions were actually made and the title's
    own embedding, so the LLM gate and the embedding classifier can still
    be compared after the fact (see export_title_gate_tracking.py /
    compare_model_vs_llm_gate.py) and the embedding never needs
    recomputing later. llm_relevant is None when the LLM wasn't run for
    this article (the current live default)."""
    update = {
        "title_gate_status": "Gated",
        "title_gate_llm_decision": llm_relevant,
        "title_gate_model_decision": model_relevant,
        "title_embedding": title_embedding,
    }
    if archived:
        update["ingestion_status"] = "Archived"
    articles.update_one({"_id": ObjectId(article_id)}, {"$set": update})


def log_title_gate_run(
    status: str,
    message: str,
    gated_articles: int,
    archived_articles: int,
    errors: List[str],
    duration_seconds: Optional[float] = None,
) -> None:
    """Appends a permanent record of one title-gate run to the
    title_gate_runs collection — the title-gate job's counterpart to
    log_extraction_run()/log_scrape_run()."""
    db["title_gate_runs"].insert_one({
        "status": status,
        "message": message,
        "gated_articles": gated_articles,
        "archived_articles": archived_articles,
        "duration_seconds": duration_seconds,
        "errors": errors,
        "logged_at": datetime.now(timezone.utc),
    })


# ── Preprocessing (clustering) job status ───────────────────────────────
# Mirrors the title-gate-status helpers above one-for-one, on the
# clustering_status meta document. See services/preprocessing.py.

def get_articles_pending_clustering() -> List[ArticleOut]:
    """Returns every relevant, already-embedded article that hasn't been
    through same-day clustering yet — the candidate pool for the
    preprocessing job. Archived articles are excluded (nothing to cluster
    them into is useful downstream); a missing title_embedding would
    crash the linkage computation, so it's excluded defensively too,
    though every article gated since 2026-08-26 always has one."""
    cursor = articles.find({
        "ingestion_status": {"$ne": "Archived"},
        "title_embedding": {"$ne": None},
        "cluster_status": {"$ne": "Clustered"},
    })
    return [ArticleOut(**_serialize_article(doc)) for doc in cursor]


def get_clustering_status() -> Optional[ClusteringRunStatus]:
    """Returns the current preprocessing-job status, or None if it has
    never run yet."""
    doc = meta.find_one({"_id": META_CLUSTERING_DOC_ID})
    if doc is None:
        return None
    doc = dict(doc)
    doc.pop("_id")
    return ClusteringRunStatus(**doc)


def set_clustering_in_progress(value: bool) -> None:
    """Flips just the in_progress flag on the clustering-status document —
    Mongo-backed overlap guard, same as set_title_gate_in_progress()."""
    meta.update_one(
        {"_id": META_CLUSTERING_DOC_ID},
        {"$set": {"in_progress": value}},
        upsert=True,
    )


def set_clustering_phase_progress(
    phase: str,
    phase_total: int,
    phase_processed: int,
    days_done: int,
    articles_extracted_done: int,
    clusters_condensed_done: int,
    clusters_entities_done: int,
    clusters_contextualized_done: int = 0,
    clusters_insights_done: int = 0,
) -> None:
    """Updates the live, phase-aware progress on the clustering-status
    document — see ClusteringRunStatus's docstring for why this replaced
    a single blended total/processed. total/processed are also set here
    (mirroring phase_total/phase_processed) purely so this document still
    satisfies the generic StageStatusResponse shape other stages use."""
    meta.update_one(
        {"_id": META_CLUSTERING_DOC_ID},
        {"$set": {
            "total": phase_total,
            "processed": phase_processed,
            "phase": phase,
            "phase_total": phase_total,
            "phase_processed": phase_processed,
            "days_done": days_done,
            "articles_extracted_done": articles_extracted_done,
            "clusters_condensed_done": clusters_condensed_done,
            "clusters_entities_done": clusters_entities_done,
            "clusters_contextualized_done": clusters_contextualized_done,
            "clusters_insights_done": clusters_insights_done,
        }},
        upsert=True,
    )


def set_clustering_stop_requested(value: bool) -> None:
    """Flips the stop_requested flag — same cooperative-stop pattern as
    the other stages."""
    meta.update_one(
        {"_id": META_CLUSTERING_DOC_ID},
        {"$set": {"stop_requested": value}},
        upsert=True,
    )


def set_clustering_rate(avg_seconds_per_day: float) -> None:
    """Updates the exponential moving average of seconds-per-day on the
    clustering-status document."""
    meta.update_one(
        {"_id": META_CLUSTERING_DOC_ID},
        {"$set": {"avg_seconds_per_article": avg_seconds_per_day}},
        upsert=True,
    )


def set_clustering_started(started_at: datetime) -> None:
    """Records when the current preprocessing run began."""
    meta.update_one(
        {"_id": META_CLUSTERING_DOC_ID},
        {"$set": {"started_at": started_at}},
        upsert=True,
    )


def set_clustering_run_outcome(last_run_at: datetime, last_run_status: str) -> None:
    """Records the outcome of the most recent preprocessing run without
    touching in_progress — see set_title_gate_run_outcome()."""
    meta.update_one(
        {"_id": META_CLUSTERING_DOC_ID},
        {"$set": {"last_run_at": last_run_at, "last_run_status": last_run_status}},
        upsert=True,
    )


def count_clusters_for_date(date_key: str) -> int:
    """How many clusters already exist for this UTC day — used to offset
    a new clustering batch's fcluster labels so cluster_ids never collide
    with an earlier run's clusters for the same day (fcluster's labels
    always restart at 1 for a fresh batch; without this offset, a second
    run on a day that already has clusters "2026-08-27-1"/"-2" would mint
    a colliding "2026-08-27-1" for a completely different story)."""
    return clusters.count_documents({"date": date_key})


def set_cluster_assignments(date_key: str, article_id_to_cluster_id: dict) -> None:
    """Records one clustering batch's result: every article gets its
    cluster_id set and cluster_status flipped to "Clustered" (so it's
    never re-picked-up by the job), and one `clusters` document is
    created per distinct cluster_id in the batch, holding its member
    article_ids. One update per article/cluster — the same per-item-loop
    style the rest of this module uses, not a bulk write, since a day's
    batch is small and this isn't on any LLM-cost-bound hot path."""
    by_cluster: dict = defaultdict(list)
    for article_id, cluster_id in article_id_to_cluster_id.items():
        by_cluster[cluster_id].append(article_id)
        articles.update_one(
            {"_id": ObjectId(article_id)},
            {"$set": {"cluster_status": "Clustered", "cluster_id": cluster_id}},
        )

    now = datetime.now(timezone.utc)
    for cluster_id, member_ids in by_cluster.items():
        clusters.update_one(
            {"_id": cluster_id},
            {
                "$set": {"date": date_key, "article_ids": member_ids},
                "$setOnInsert": {
                    "created_at": now,
                    "titles": [],
                    "chunks": [],
                    "chunk_vector_ids": [],
                    "condensed": False,
                    "entities": [],
                    "entities_extracted": False,
                    "similar_clusters": [],
                    "contextualized": False,
                    "insights": [],
                    "insights_generated": False,
                },
            },
            upsert=True,
        )


def _cluster_from_doc(doc: dict) -> Cluster:
    return Cluster(
        cluster_id=doc["_id"],
        date=doc["date"],
        article_ids=doc["article_ids"],
        titles=doc.get("titles", []),
        chunks=doc.get("chunks", []),
        chunk_vector_ids=doc.get("chunk_vector_ids", []),
        condensed=doc.get("condensed", False),
        entities=doc.get("entities", []),
        entities_extracted=doc.get("entities_extracted", False),
        similar_clusters=[SimilarClusterRef(**s) for s in doc.get("similar_clusters", [])],
        contextualized=doc.get("contextualized", False),
        insights=[ClusterInsight(**i) for i in doc.get("insights", [])],
        insights_generated=doc.get("insights_generated", False),
        created_at=doc["created_at"],
    )


def get_articles_pending_cluster_extraction() -> List[ArticleOut]:
    """Every clustered article still in "No Extraction" — the candidate
    pool for preprocessing's extraction phase (folded in from the retired
    standalone extraction stage, see services/extractor.py's _extract_one,
    reused as-is by services/preprocessing.py)."""
    cursor = articles.find({
        "cluster_id": {"$ne": None},
        "ingestion_status": "No Extraction",
    })
    return [ArticleOut(**_serialize_article(doc)) for doc in cursor]


def get_clusters_ready_for_condensing() -> List[Cluster]:
    """Clusters where every member article has reached a terminal
    ingestion_status (Successful/Failed — extraction has been attempted
    for all of them, one way or another) and condensation
    (services/cluster_condenser.py) hasn't run yet. Checked in Python, not
    a single Mongo query, since it requires cross-referencing each
    cluster's article_ids against those articles' current
    ingestion_status. Matches on {"$ne": True} rather than
    {"condensed": False} so pre-existing cluster documents from before
    this field existed (no "condensed" key at all) are picked up too,
    instead of being permanently skipped."""
    pending = list(clusters.find({"condensed": {"$ne": True}}))
    ready = []
    for doc in pending:
        member_ids = [ObjectId(aid) for aid in doc["article_ids"]]
        member_docs = list(articles.find({"_id": {"$in": member_ids}}, {"ingestion_status": 1}))
        all_terminal = len(member_docs) == len(doc["article_ids"]) and all(
            m["ingestion_status"] in ("Successful", "Failed") for m in member_docs
        )
        if all_terminal:
            ready.append(_cluster_from_doc(doc))
    return ready


def upsert_condensed_chunk_embeddings(cluster_id: str, date: str, condensed_chunks: list) -> List[str]:
    """Upserts every deduped chunk's embedding from one cluster's
    condensation (services/cluster_condenser.CondensedChunk — the full
    deduped set, not just the MMR-selected subset; see that module's
    docstring for why the non-selected ones are still worth keeping) into
    Pinecone, tagged (metadata "source": "condensation") so they're
    distinguishable from other producers sharing the same index. Returns
    the vector ids, in the same order as `condensed_chunks`, for the
    caller to store on Cluster.chunk_vector_ids.

    Vector id scheme: "{cluster_id}_{index}" — index is this chunk's
    position in `condensed_chunks`, not its position among only the
    selected ones."""
    if not condensed_chunks:
        return []

    vector_ids = [f"{cluster_id}_{i}" for i in range(len(condensed_chunks))]
    vectors = [
        {
            "id": vector_id,
            "values": chunk.embedding,
            "metadata": {
                "source": "condensation",
                "cluster_id": cluster_id,
                "date": date,
                "order": i,
                "language": chunk.language,
                "content": chunk.text,
                "mmr_selected": chunk.selected,
            },
        }
        for i, (vector_id, chunk) in enumerate(zip(vector_ids, condensed_chunks))
    ]

    pc = Pinecone(api_key=settings.pinecone_api_key)
    index = pc.Index(PINECONE_INDEX_NAME)
    for i in range(0, len(vectors), PINECONE_UPSERT_BATCH_SIZE):
        index.upsert(vectors=vectors[i : i + PINECONE_UPSERT_BATCH_SIZE])

    return vector_ids


def set_cluster_condensed(cluster_id: str, titles: List[str], chunks: List[str], chunk_vector_ids: List[str]) -> None:
    clusters.update_one(
        {"_id": cluster_id},
        {"$set": {
            "titles": titles,
            "chunks": chunks,
            "chunk_vector_ids": chunk_vector_ids,
            "condensed": True,
        }},
    )


def get_clusters(date: Optional[str] = None) -> List[Cluster]:
    """Lists clusters, newest first, optionally filtered to one UTC day
    ("YYYY-MM-DD") — the read side for actually showing clustering/
    condensation/entity results (routers/preprocessing.py's GET
    /clusters), separate from the job-progress endpoints above."""
    query = {"date": date} if date else {}
    cursor = clusters.find(query).sort("created_at", -1)
    return [_cluster_from_doc(doc) for doc in cursor]


def get_clusters_by_ids(cluster_ids: List[str]) -> List[Cluster]:
    """Batch-fetches clusters by cluster_id, unordered — contextualization's
    candidate lookup, resolving the entity-to-cluster inverted index's
    cluster_ids (models.Entity.cluster_ids) into full Cluster documents
    to score against."""
    if not cluster_ids:
        return []
    return [_cluster_from_doc(doc) for doc in clusters.find({"_id": {"$in": cluster_ids}})]


def get_cluster_display_info(cluster: Cluster) -> dict:
    """Computed, read-only per-cluster fields for the day/cluster browsing
    UI (not stored on the Cluster document itself, since both are cheap
    to derive on read and would otherwise need invalidating every time a
    member article's ingestion_status changes):

    - representative_title: the member title whose title_embedding is
      closest (cosine) to the cluster's own title-embedding centroid —
      the "most typical" framing of the story, not just whichever article
      happened to be scraped/clustered first. Falls back to the first
      member's title if embeddings aren't available (e.g. before the
      cluster's articles have been through title-gating again after a
      reset).
    - extraction_status: "not_extracted" (no member has been attempted
      yet), "extracting" (some but not all members have reached a
      terminal ingestion_status), or "extracted" (every member has,
      Successful/Failed) — same terminal-status definition
      get_clusters_ready_for_condensing() uses."""
    member_ids = [ObjectId(aid) for aid in cluster.article_ids]
    member_docs = list(articles.find(
        {"_id": {"$in": member_ids}},
        {"title": 1, "title_embedding": 1, "ingestion_status": 1},
    ))

    terminal = {"Successful", "Failed"}
    statuses = [m["ingestion_status"] for m in member_docs]
    if statuses and all(s in terminal for s in statuses):
        extraction_status = "extracted"
    elif any(s in terminal for s in statuses):
        extraction_status = "extracting"
    else:
        extraction_status = "not_extracted"

    with_embedding = [m for m in member_docs if m.get("title_embedding")]
    if len(with_embedding) <= 1:
        representative_title = (with_embedding or member_docs or [{"title": ""}])[0]["title"]
    else:
        vectors = np.array([m["title_embedding"] for m in with_embedding], dtype=np.float64)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        normed = vectors / norms
        centroid = normed.mean(axis=0)
        centroid_norm = np.linalg.norm(centroid)
        if centroid_norm > 0:
            centroid = centroid / centroid_norm
        best_idx = int(np.argmax(normed @ centroid))
        representative_title = with_embedding[best_idx]["title"]

    return {"representative_title": representative_title, "extraction_status": extraction_status}


def get_clusters_ready_for_entities() -> List[Cluster]:
    """Clusters that have been condensed but not yet run through NER."""
    cursor = clusters.find({"condensed": True, "entities_extracted": False})
    return [_cluster_from_doc(doc) for doc in cursor]


def set_cluster_entities(cluster_id: str, entities: List[str]) -> None:
    clusters.update_one(
        {"_id": cluster_id},
        {"$set": {"entities": entities, "entities_extracted": True}},
    )


def get_clusters_ready_for_contextualization() -> List[Cluster]:
    """Clusters that have had entities extracted but not yet been through
    phase 5 (services/contextualization.py). Matches on {"$ne": True}
    rather than {"contextualized": False} so a cluster document that
    predates this field (no "contextualized" key at all) is picked up
    too, instead of being permanently skipped — see
    get_clusters_ready_for_condensing()'s identical reasoning."""
    cursor = clusters.find({"entities_extracted": True, "contextualized": {"$ne": True}})
    return [_cluster_from_doc(doc) for doc in cursor]


def set_cluster_contextualized(cluster_id: str, similar_clusters: List[dict]) -> None:
    clusters.update_one(
        {"_id": cluster_id},
        {"$set": {"similar_clusters": similar_clusters, "contextualized": True}},
    )


def get_clusters_ready_for_insights() -> List[Cluster]:
    """Clusters that have been through contextualization (phase 5) but
    not yet phase 6 (services/llm_insight.assess_cluster). Matches on
    {"$ne": True}, same reasoning as get_clusters_ready_for_contextualization
    — a cluster document that predates this field is still picked up."""
    cursor = clusters.find({"contextualized": True, "insights_generated": {"$ne": True}})
    return [_cluster_from_doc(doc) for doc in cursor]


def set_cluster_insights(cluster_id: str, insights: List[dict]) -> None:
    clusters.update_one(
        {"_id": cluster_id},
        {"$set": {"insights": insights, "insights_generated": True}},
    )


def get_cluster_insight_month_summary(year: int, month: int) -> Dict[str, dict]:
    """{ 'YYYY-MM-DD': {total, insights_generated, relevant_count} } for
    every UTC day in one month that has at least one cluster — the report
    calendar's per-day markers (routers/reports.py), computed straight
    from the clusters collection now that insight generation is an
    automatic preprocessing phase (6) rather than an on-demand per-day
    job: `total` is how many clusters exist that day, `insights_generated`
    how many of those have already been through phase 6, and
    `relevant_count` the total insights found across them so far."""
    start = f"{year:04d}-{month:02d}-01"
    end = f"{year + 1:04d}-01-01" if month == 12 else f"{year:04d}-{month + 1:02d}-01"
    pipeline = [
        {"$match": {"date": {"$gte": start, "$lt": end}}},
        {
            "$group": {
                "_id": "$date",
                "total": {"$sum": 1},
                "insights_generated": {"$sum": {"$cond": ["$insights_generated", 1, 0]}},
                "relevant_count": {"$sum": {"$size": {"$ifNull": ["$insights", []]}}},
            }
        },
    ]
    return {
        doc["_id"]: {
            "total": doc["total"],
            "insights_generated": doc["insights_generated"],
            "relevant_count": doc["relevant_count"],
        }
        for doc in clusters.aggregate(pipeline)
    }


# ── Entity database (canonicalization, entity-to-cluster inverted index) ──
# See models.Entity's docstring and docs/completion-plan.md — a SlimRAG-
# style entity index, accumulated across every cluster ever processed.

def _entity_doc_id(entity_group: str, canonical_name: str) -> str:
    """Composite _id ("GROUP:name"), not the bare name — a person and an
    organization can legitimately share a surface form, and Mongo's _id
    must be unique across the whole collection regardless of group."""
    return f"{entity_group}:{canonical_name}"


def _entity_from_doc(doc: dict) -> Entity:
    return Entity(
        canonical_name=doc["canonical_name"],
        entity_group=doc["entity_group"],
        aliases=doc.get("aliases", []),
        embedding=doc["embedding"],
        cluster_ids=doc.get("cluster_ids", []),
        first_seen=doc["first_seen"],
        last_seen=doc["last_seen"],
        parent=doc.get("parent"),
        depth=doc.get("depth"),
        threshold=doc.get("threshold"),
        embeddings=doc.get("embeddings"),
    )


def get_entities_by_group(entity_group: str) -> List[Entity]:
    """Every canonical entity of one NER type (PER/ORG/LOC/MISC) —
    entity_extraction.canonicalize_entities()'s candidate pool for
    matching a newly-seen mention. Scoped by group so a person and an
    organization sharing a surface form are never compared against each
    other. A full fetch + in-memory scan, not an ANN lookup — fine at
    today's entity-database size; revisit (e.g. a Pinecone-backed lookup,
    matching how cluster-based contextualization already uses Pinecone)
    if this ever becomes the bottleneck as the database grows across the
    full backlog."""
    return [_entity_from_doc(doc) for doc in entities_collection.find({"entity_group": entity_group})]


def get_entities_by_names(canonical_names: List[str]) -> List[Entity]:
    """Every entity doc matching one of these canonical names, regardless
    of group — services/contextualization.py's candidate lookup, walking
    a cluster's own `entities` list to resolve each one's cluster_ids
    (the entity-to-cluster inverted index) into the set of past clusters
    that share it. A cluster's own entities are already known-distinct by
    construction, so the NER-group scoping canonicalize_entities() needs
    doesn't apply here."""
    if not canonical_names:
        return []
    return [_entity_from_doc(doc) for doc in entities_collection.find({"canonical_name": {"$in": canonical_names}})]


def create_entity(canonical_name: str, entity_group: str, embedding: List[float], cluster_id: str) -> None:
    """Seeds a brand-new canonical entity — no existing entity in its
    group was a close enough embedding match (see
    entity_extraction.CANONICALIZATION_THRESHOLD). Keyed by
    canonical_name as literally first seen; there's no LLM step yet to
    pick a "best" display form among aliases (docs/completion-plan.md
    flags this as a possible future refinement, not done here)."""
    now = datetime.now(timezone.utc)
    entities_collection.insert_one({
        "_id": _entity_doc_id(entity_group, canonical_name),
        "canonical_name": canonical_name,
        "entity_group": entity_group,
        "aliases": [],
        "embedding": embedding,
        "cluster_ids": [cluster_id],
        "first_seen": now,
        "last_seen": now,
    })


def merge_entity_alias(canonical_name: str, entity_group: str, alias: str, cluster_id: str) -> None:
    """Records a new mention of an already-known entity: adds `alias` to
    its alias list (via $addToSet, so a repeated alias is a no-op — and
    skipped entirely when the "alias" is just the canonical name's own
    mention again) and `cluster_id` to its entity-to-cluster inverted
    index. Updates last_seen; the entity's own embedding is left as
    first computed, not averaged/drifted with every new mention — a
    deliberate simplicity choice, not yet revisited."""
    update: dict = {
        "$set": {"last_seen": datetime.now(timezone.utc)},
        "$addToSet": {"cluster_ids": cluster_id},
    }
    if alias != canonical_name:
        update["$addToSet"]["aliases"] = alias
    entities_collection.update_one({"_id": _entity_doc_id(entity_group, canonical_name)}, update)


def upsert_topic_entity(
    node_id: str,
    canonical_name: str,
    aliases: List[str],
    embedding: List[float],
    embeddings_by_language: dict,
    parent_node_id: Optional[str],
    depth: int,
    threshold: float,
) -> None:
    """Upserts one TOPIC entity migrated from the old relevance tree
    (migrate_tree_to_entities.py) — unlike create_entity()'s plain
    insert (used for freshly-seen NER mentions, which can never collide),
    this is idempotent by design: re-running the migration against the
    same tree file just re-writes the same documents rather than
    duplicate-key-erroring on the second run. cluster_ids/first_seen
    aren't touched on an existing document ($setOnInsert), since a TOPIC
    entity migrated a second time hasn't newly "appeared" in any cluster
    just by being re-migrated.

    _id is keyed off node_id, not canonical_name — stable across
    re-migration even if a label changes, and what parent_node_id
    resolves against below."""
    doc_id = _entity_doc_id("TOPIC", node_id)
    parent_id = _entity_doc_id("TOPIC", parent_node_id) if parent_node_id else None
    now = datetime.now(timezone.utc)
    entities_collection.update_one(
        {"_id": doc_id},
        {
            "$set": {
                "canonical_name": canonical_name,
                "entity_group": "TOPIC",
                "aliases": aliases,
                "embedding": embedding,
                "embeddings": embeddings_by_language,
                "parent": parent_id,
                "depth": depth,
                "threshold": threshold,
                "last_seen": now,
            },
            "$setOnInsert": {"cluster_ids": [], "first_seen": now},
        },
        upsert=True,
    )


def log_clustering_run(
    status: str,
    message: str,
    days_processed: int,
    articles_clustered: int,
    articles_extracted: int,
    clusters_condensed: int,
    clusters_entities_extracted: int,
    errors: List[str],
    duration_seconds: Optional[float] = None,
    phase_durations: Optional[dict] = None,
    clusters_contextualized: int = 0,
    clusters_insights_generated: int = 0,
) -> None:
    """Appends a permanent record of one preprocessing run (all six
    phases — cluster, extract, condense, entities, contextualize,
    insights) to the clustering_runs collection — the preprocessing job's
    counterpart to log_title_gate_run(). phase_durations is
    {"clustering": secs, "extraction": secs, "condensing": secs,
    "entities": secs, "contextualization": secs, "insights": secs} —
    wall-clock time actually spent in each phase this run
    (services/preprocessing.py's phase_durations), the per-phase
    breakdown the Logs page shows for this stage specifically."""
    db["clustering_runs"].insert_one({
        "status": status,
        "message": message,
        "days_processed": days_processed,
        "articles_clustered": articles_clustered,
        "articles_extracted": articles_extracted,
        "clusters_condensed": clusters_condensed,
        "clusters_entities_extracted": clusters_entities_extracted,
        "clusters_contextualized": clusters_contextualized,
        "clusters_insights_generated": clusters_insights_generated,
        "errors": errors,
        "duration_seconds": duration_seconds,
        "phase_durations": phase_durations,
        "logged_at": datetime.now(timezone.utc),
    })


def get_recent_run_logs(limit: int = 100) -> List[dict]:
    """Merges every stage's run-history collection into one time-ordered
    list — routers/logs.py's data source. Each stage already logs one
    document per run to its own collection; this normalizes them to a
    common shape (stage/status/message/duration_seconds/logged_at) and
    returns the most recent `limit` across all of them, newest first.
    phase_durations is only ever populated for "preprocessing" entries
    (see log_clustering_run) — every other stage is single-phase, so its
    own duration_seconds already says it all. There is no separate
    "report" stage anymore — insight generation is preprocessing's own
    phase 6, so its history is already inside the "preprocessing" entries'
    phase_durations, not a separate collection.

    Queries each collection independently (capped at `limit` each, so a
    quiet stage can't be starved out by a chatty one) then merges and
    truncates in Python — simpler than a $unionWith aggregation across
    several differently-shaped collections, and cheap at this scale."""
    entries: List[dict] = []

    for doc in db["scrape_runs"].find().sort("logged_at", -1).limit(limit):
        entries.append({
            "stage": "scrape",
            "status": doc["status"],
            "message": doc["message"],
            "duration_seconds": doc.get("duration_seconds"),
            "phase_durations": None,
            "logged_at": doc["logged_at"],
        })

    for doc in db["title_gate_runs"].find().sort("logged_at", -1).limit(limit):
        entries.append({
            "stage": "title_gate",
            "status": doc["status"],
            "message": doc["message"],
            "duration_seconds": doc.get("duration_seconds"),
            "phase_durations": None,
            "logged_at": doc["logged_at"],
        })

    for doc in db["clustering_runs"].find().sort("logged_at", -1).limit(limit):
        entries.append({
            "stage": "preprocessing",
            "status": doc["status"],
            "message": doc["message"],
            "duration_seconds": doc.get("duration_seconds"),
            "phase_durations": doc.get("phase_durations"),
            "logged_at": doc["logged_at"],
        })

    entries.sort(key=lambda e: e["logged_at"], reverse=True)
    return entries[:limit]


# ── Archived-articles management (the title-gate LLM's rejects) ─────────

def get_archived_articles() -> List[ArticleOut]:
    """Returns every article the title gate archived, newest-published
    first — the candidate pool for the Archived review page."""
    cursor = articles.find({"ingestion_status": "Archived"}).sort("published_at", -1)
    return [ArticleOut(**_serialize_article(doc)) for doc in cursor]


def disarchive_article(article_id: str) -> bool:
    """Reverses one title-gate decision a human judged wrong: flips
    ingestion_status back to "No Extraction" so it re-enters the normal
    pipeline. title_gate_status is left at "Gated" (it already is, from
    having been archived) rather than reset to "Not Gated" — otherwise the
    next title-gate run would just re-check and potentially re-archive it,
    silently undoing the human's override. Only takes effect on an
    article that's actually currently Archived. Returns True if a document
    was changed."""
    result = articles.update_one(
        {"_id": ObjectId(article_id), "ingestion_status": "Archived"},
        {"$set": {"ingestion_status": "No Extraction", "title_gate_status": "Gated"}},
    )
    return result.modified_count > 0


def flush_archived_articles() -> int:
    """Permanently deletes every Archived article from Mongo — the
    "flush" button on the Archived review page. Archived articles are
    never extracted, so no Pinecone vectors exist for them (no per-article
    embedding stage exists anymore — see PINECONE_INDEX_NAME's comment).
    Returns how many articles were deleted."""
    result = articles.delete_many({"ingestion_status": "Archived"})
    return result.deleted_count


def set_ingestion_status_bulk(article_ids: List[str], status: str) -> None:
    """Bulk version of set_ingestion_status — e.g. marking a whole batch as
    'Pending' at once, when the user selects several articles and some/all
    fall in the same bucket."""
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


def get_articles_pending_extraction() -> List[ArticleOut]:
    """Returns every article still in its initial 'No Extraction' state.
    Kept for reference/possible reuse — the bulk 'Extract Articles' stage
    this was originally the candidate pool for was retired in favor of
    services/preprocessing.py's per-cluster extraction phase, which uses
    get_articles_pending_cluster_extraction() instead."""
    cursor = articles.find({"ingestion_status": "No Extraction"})
    return [ArticleOut(**_serialize_article(doc)) for doc in cursor]