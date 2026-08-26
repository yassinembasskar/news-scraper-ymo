"""
FastAPI application entrypoint.

Wires together the database startup checks (connectivity + index creation)
and all routers. Run with:
    uvicorn app.main:app --reload
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import database, repository
from app.routers import articles, embedding, extraction, reports, scoring, stats, title_gate, update


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
    # embedding/scoring in_progress flags (Mongo-backed, survive a restart)
    # can be trusted to reflect reality if this process was killed outright
    # rather than shut down cleanly — their finally blocks wouldn't have
    # run. Reset every job guard and any article stuck at "Pending" from
    # any job so nothing is left permanently stuck or permanently
    # unrunnable — and, for extraction/embedding/scoring alike, an article
    # a run never reached or didn't finish falls back to its pre-run
    # status rather than ending up mislabeled "Failed".
    # Title gating has no per-article "Pending" state to reset (a gate call
    # is a single synchronous request, not a multi-step process, same as
    # report generation) — only the Mongo-backed job guard needs clearing.
    repository.set_title_gate_in_progress(False)
    repository.set_title_gate_stop_requested(False)
    repository.reset_pending_extraction_articles()
    repository.set_extraction_in_progress(False)
    repository.set_extraction_stop_requested(False)
    repository.reset_pending_embedding_articles()
    repository.set_embedding_in_progress(False)
    repository.set_embedding_stop_requested(False)
    repository.reset_pending_scoring_articles()
    repository.set_scoring_in_progress(False)
    repository.set_scoring_stop_requested(False)
    # Report generation has no per-article "Pending" state to reset (it
    # writes one daily_reports document at the end, not per-article fields),
    # so only the Mongo-backed job guard needs clearing after a hard crash.
    repository.set_report_in_progress(False)
    repository.set_report_stop_requested(False)

    yield
    # No explicit shutdown cleanup needed — MongoClient closes its
    # connections automatically when the process exits.


app = FastAPI(
    title="Yazaki News Monitor API",
    lifespan=lifespan,
)

# The Streamlit frontend never needed this — its Python code calls the
# backend server-side, outside the browser entirely. The React frontend
# fetches directly from the browser (see frontend-react/src/api/client.ts),
# so without explicit CORS headers the browser silently blocks every
# request as cross-origin (5174 -> 8000). Scoped to localhost dev ports
# rather than "*" since credentials aren't used and this only needs to
# work from developer machines for now.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(update.router)
app.include_router(articles.router)
app.include_router(stats.router)
app.include_router(title_gate.router)
app.include_router(extraction.router)
app.include_router(embedding.router)
app.include_router(scoring.router)
app.include_router(reports.router)


@app.get("/health")
def health():
    """Basic liveness check — confirms the API process is up and Mongo is
    still reachable right now (not just at startup)."""
    return {"status": "ok", "mongo_connected": database.ping()}