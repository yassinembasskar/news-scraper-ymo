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


def init_indexes() -> None:
    """Creates all required indexes if they don't already exist.
    Safe to call on every startup — create_index() is a no-op if an index
    with the same key/options already exists.
    """
    # Unique index on url: this IS the dedup mechanism for repeated/
    # overlapping scrape runs (upsert on this field).
    articles.create_index([("url", ASCENDING)], unique=True)

    # Supports date-range queries and the daily chart.
    articles.create_index([("published_at", ASCENDING)])

    # Supports the language filter.
    articles.create_index([("language", ASCENDING)])

    # Multikey index: supports the keyword filter and the
    # "most recurrent keyword per day" aggregation.
    articles.create_index([("matched_keywords", ASCENDING)])


def ping() -> bool:
    """Quick connectivity check — returns True if Mongo is reachable."""
    try:
        client.admin.command("ping")
        return True
    except Exception:
        return False