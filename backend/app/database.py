"""
MongoDB connection and index setup.

This module owns the single MongoClient instance for the whole backend and
exposes the two collections (`articles`, `meta`) that routers and services
read/write. Call `init_indexes()` once at FastAPI startup so a fresh database
always ends up correctly configured, with no manual mongosh step required.
"""

from pymongo import MongoClient, ASCENDING
from pymongo.collection import Collection
from pymongo.database import Database

from app.config import settings

# Single shared client for the lifetime of the app.
client: MongoClient = MongoClient(settings.mongo_uri)
db: Database = client[settings.mongo_db_name]

articles: Collection = db["articles"]
meta: Collection = db["meta"]
clusters: Collection = db["clusters"]
entities: Collection = db["entities"]


def init_indexes() -> None:
    """Creates all required indexes if they don't already exist.
    Safe to call on every startup — create_index() is a no-op if an index
    with the same key/options already exists.
    """
    # Unique index on url: this IS the dedup mechanism for repeated/
    # overlapping scrape runs (upsert on this field).
    articles.create_index([("url", ASCENDING)], unique=True)

    # Not unique (deliberately) — this is a cross-source story-dedup
    # accelerator, not a hard constraint. Same title, different URL happens
    # legitimately (wire-service syndication across outlets — verified live:
    # one story appeared under 16 distinct domains with an identical title),
    # and repository.upsert_article() decides in application code whether a
    # same-titled candidate should be skipped or take over the existing
    # document, rather than the database rejecting the write outright.
    articles.create_index([("title", ASCENDING)])

    # Supports date-range queries and the daily chart.
    articles.create_index([("published_at", ASCENDING)])

    # Supports the language filter.
    articles.create_index([("language", ASCENDING)])

    # Multikey index: supports the keyword filter and the
    # "most recurrent keyword per day" aggregation.
    articles.create_index([("matched_keywords", ASCENDING)])

    # Supports listing/filtering clusters by day (services/preprocessing.py).
    # cluster_id (the collection's _id) is already indexed/unique by default.
    clusters.create_index([("date", ASCENDING)])

    # Supports entity_extraction.canonicalize_entities()'s per-mention
    # lookup of "every existing entity of this NER type" — the composite
    # _id ("GROUP:name") is already indexed/unique by default, but that
    # doesn't help a query scoped to entity_group alone.
    entities.create_index([("entity_group", ASCENDING)])


def ping() -> bool:
    """Quick connectivity check — returns True if Mongo is reachable."""
    try:
        client.admin.command("ping")
        return True
    except Exception:
        return False