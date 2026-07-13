"""
FastAPI application entrypoint.

Wires together the database startup checks (connectivity + index creation)
and all routers. Run with:
    uvicorn app.main:app --reload
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import database
from app.routers import articles, extraction, stats, update


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


@app.get("/health")
def health():
    """Basic liveness check — confirms the API process is up and Mongo is
    still reachable right now (not just at startup)."""
    return {"status": "ok", "mongo_connected": database.ping()}