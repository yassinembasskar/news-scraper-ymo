"""
FastAPI application entrypoint.

Wires together the database startup checks (connectivity + index creation)
and all routers. Run with:
    uvicorn app.main:app --reload
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import database, repository
from app.routers import articles, embedding, extraction, scoring, stats, update


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail loudly and immediately if Mongo isn't reachable, rather than
    # starting "successfully" and only discovering it on the first real
    # request.
    if not database.ping():
        raise RuntimeError(
            "Could not connect to MongoDB. Check MONGO_URI in your .env file "
            "and make sure the database is running."
        )
    database.init_indexes()

    # Neither the extraction lock (in-process, so already gone) nor the
    # embedding in_progress flag (Mongo-backed, survives a restart) can be
    # trusted to reflect reality if this process was killed outright rather
    # than shut down cleanly — their finally blocks wouldn't have run. Reset
    # both job guards and any article stuck at "Pending" from either job so
    # nothing is left permanently stuck or permanently unrunnable.
    repository.reset_pending_extraction_articles()
    repository.reset_pending_embedding_articles()
    repository.set_embedding_in_progress(False)
    repository.set_embedding_stop_requested(False)
    repository.reset_pending_scoring_articles()
    repository.set_scoring_in_progress(False)
    repository.set_scoring_stop_requested(False)

    yield
    # No explicit shutdown cleanup needed — MongoClient closes its
    # connections automatically when the process exits.


app = FastAPI(
    title="Yazaki News Monitor API",
    lifespan=lifespan,
)

app.include_router(update.router)
app.include_router(articles.router)
app.include_router(stats.router)
app.include_router(extraction.router)
app.include_router(embedding.router)
app.include_router(scoring.router)


@app.get("/health")
def health():
    """Basic liveness check — confirms the API process is up and Mongo is
    still reachable right now (not just at startup)."""
    return {"status": "ok", "mongo_connected": database.ping()}