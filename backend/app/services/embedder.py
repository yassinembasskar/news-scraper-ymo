"""
Embedding orchestrator.

This is the "Embed articles" counterpart to services/scraper.py's
run_scrape() and services/extractor.py's run_extraction(). It ties
together:
  - repository.py         -> finds not-yet-embedded articles, persists
                              per-article and per-run results
  - services/chunking.py  -> line-break chunking (validated separately,
                              reused as-is)
  - sentence-transformers -> embeds each chunk with Qwen3-Embedding-0.6B
  - pinecone               -> upserts vectors into the "yazaki" index

Workflow, every time it's triggered:
  1. Guard   -> refuse to start if an embedding run is already in
                progress. Unlike extraction's in-process threading.Lock,
                this guard is the Mongo document meta._id="embedding_status"
                (in_progress flag) — survives a process restart, at the
                cost of no staleness timeout yet: a run that crashes
                without reaching its finally block would need a manual
                reset.
  2. Gather  -> every article with ingestion_status == "Successful" and
                embedding_status != "Embedded" (incremental; "Failed"
                articles are naturally retried next run, no separate
                retry path needed).
  3. Embed   -> for each candidate: mark 'Pending', chunk its content,
                embed every chunk, upsert into Pinecone, then record
                chunk_ids / number_of_chunks / embedding_status on that
                article. A failure on one article is logged and does not
                abort the run — it's simply picked up again next run.
  4. Log     -> one embedding_runs document for the whole run.
"""

import concurrent.futures
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from pinecone import Pinecone
from sentence_transformers import SentenceTransformer

from app import repository
from app.config import settings
from app.services.chunking import chunk_text

EMBEDDING_MODEL_NAME = "Qwen/Qwen3-Embedding-0.6B"
PINECONE_INDEX_NAME = "yazaki"
UPSERT_BATCH_SIZE = 100
# Weight given to each new per-article duration when updating the
# seconds-per-article moving average — higher reacts faster to changes in
# speed, at the cost of more jitter in the reported ETA.
RATE_EMA_ALPHA = 0.3
# An article with more raw content than this is skipped (marked "Failed")
# rather than chunked/embedded — a circuit breaker on top of
# chunking.py's per-chunk length cap, for whatever chunk_text() still
# turns into an unreasonably large number of chunks for one article.
MAX_ARTICLE_CONTENT_CHARS = 50_000
# An article whose chunking+encoding+upsert takes longer than this is
# abandoned and marked "Failed" rather than letting it block every article
# behind it in the run indefinitely. The abandoned worker thread keeps
# running to completion in the background (Python can't force-kill a
# thread) — its result is simply discarded, and if it does eventually
# finish and upsert into Pinecone, those vectors are just orphaned rather
# than harmful, since this article's Mongo document won't reference them
# and will be retried (overwriting them) on the next embedding run.
EMBEDDING_TIMEOUT_SECONDS = 300


@dataclass
class EmbeddingSummary:
    status: str  # "success" | "partial" | "failed" | "skipped"
    message: str
    embedded_articles: int = 0
    failed_articles: int = 0
    errors: List[str] = field(default_factory=list)


def _embed_one_article(model, index, article) -> List[str]:
    """Chunks, embeds, and upserts one article's content. Returns the
    chunk ids that were upserted. Raises on any failure — the caller is
    responsible for catching this per-article so one bad article doesn't
    abort the whole run."""
    content = article.content or ""
    if len(content) > MAX_ARTICLE_CONTENT_CHARS:
        raise ValueError(
            f"Content too long ({len(content)} chars > {MAX_ARTICLE_CONTENT_CHARS} cap) — skipped"
        )

    chunks = chunk_text(article.id, article.language, content)
    if not chunks:
        raise ValueError("No chunks produced from content")

    contents = [chunk["content"] for chunk in chunks]
    embeddings = model.encode(contents)

    vectors = [
        {
            "id": chunk["chunk_id"],
            "values": embedding.tolist(),
            "metadata": {
                "article_id": chunk["article_id"],
                "url": str(article.url),
                "order": chunk["order"],
                "language": chunk["language"],
                "content": chunk["content"],
            },
        }
        for chunk, embedding in zip(chunks, embeddings)
    ]

    for i in range(0, len(vectors), UPSERT_BATCH_SIZE):
        index.upsert(vectors=vectors[i : i + UPSERT_BATCH_SIZE])

    return [vector["id"] for vector in vectors]


def _embed_one_article_with_timeout(model, index, article) -> List[str]:
    """Runs _embed_one_article() on a worker thread and gives up after
    EMBEDDING_TIMEOUT_SECONDS, raising TimeoutError instead of blocking
    the whole run on one slow article. Uses a fresh single-use executor
    (not a shared pool) and shutdown(wait=False) on timeout so the
    abandoned thread is left to finish on its own time without this call
    blocking on it — see EMBEDDING_TIMEOUT_SECONDS' docstring for what
    that means for the abandoned work."""
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(_embed_one_article, model, index, article)
    try:
        return future.result(timeout=EMBEDDING_TIMEOUT_SECONDS)
    except concurrent.futures.TimeoutError:
        raise TimeoutError(f"Embedding exceeded the {EMBEDDING_TIMEOUT_SECONDS}s timeout")
    finally:
        executor.shutdown(wait=False)


def run_embedding() -> EmbeddingSummary:
    now = datetime.now(timezone.utc)
    candidates = repository.get_articles_pending_embedding()

    if not candidates:
        # Nothing was actually run — no model loaded, no article touched —
        # so this must not overwrite last_run_at/last_run_status (that
        # stays showing the most recent run that actually did something)
        # or get an entry in embedding_runs. The caller
        # (trigger_embedding()) already checks for this same "nothing
        # pending" case up front and responds immediately, so in practice
        # this branch is only a rare fallback.
        return EmbeddingSummary(
            status="skipped",
            message="All articles are already embedded — nothing left to embed.",
        )

    model = SentenceTransformer(EMBEDDING_MODEL_NAME)
    pc = Pinecone(api_key=settings.pinecone_api_key)
    index = pc.Index(PINECONE_INDEX_NAME)

    total = len(candidates)
    embedded_count = 0
    failed_count = 0
    errors: List[str] = []
    repository.set_embedding_started(now)
    repository.set_embedding_progress(total, 0)

    avg_seconds_per_article: Optional[float] = None
    last_checkpoint = time.monotonic()

    stopped = False
    for article in candidates:
        status = repository.get_embedding_status()
        if status and status.stop_requested:
            stopped = True
            break

        repository.set_article_embedding_status(article.id, "Pending")
        try:
            chunk_ids = _embed_one_article_with_timeout(model, index, article)
            repository.set_article_embedding_result(article.id, chunk_ids, "Embedded")
            embedded_count += 1
        except Exception as e:
            repository.set_article_embedding_status(article.id, "Failed")
            failed_count += 1
            errors.append(f"{article.title}: {e}")

        checkpoint = time.monotonic()
        duration = checkpoint - last_checkpoint
        last_checkpoint = checkpoint
        avg_seconds_per_article = (
            duration if avg_seconds_per_article is None
            else RATE_EMA_ALPHA * duration + (1 - RATE_EMA_ALPHA) * avg_seconds_per_article
        )

        repository.set_embedding_progress(total, embedded_count + failed_count)
        repository.set_embedding_rate(avg_seconds_per_article)

    if stopped:
        run_status = "stopped"
    elif failed_count == 0:
        run_status = "success"
    elif embedded_count == 0:
        run_status = "failed"
    else:
        run_status = "partial"

    message = (
        f"Embedding {run_status}: {embedded_count} embedded, {failed_count} failed "
        f"(of {len(candidates)} candidates)."
    )

    repository.set_embedding_run_outcome(now, run_status)
    repository.log_embedding_run(
        status=run_status,
        message=message,
        embedded_articles=embedded_count,
        failed_articles=failed_count,
        errors=errors,
    )

    return EmbeddingSummary(
        status=run_status,
        message=message,
        embedded_articles=embedded_count,
        failed_articles=failed_count,
        errors=errors,
    )


def is_embedding_running() -> bool:
    """Returns True if an embedding run is currently in progress, per the
    Mongo-backed flag. Used by the router both to give the user immediate
    feedback ("already running") and as the actual guard against
    overlapping runs before scheduling a new one."""
    status = repository.get_embedding_status()
    return bool(status and status.in_progress)


def request_embedding_stop() -> bool:
    """Asks the current run to stop after the article it's working on
    right now — there's no safe way to interrupt a Python thread mid
    encode()/upsert(). Returns False if nothing is running to stop."""
    if not is_embedding_running():
        return False
    repository.set_embedding_stop_requested(True)
    return True


def run_embedding_job() -> None:
    """Background-task entrypoint: guards against overlapping runs via
    the Mongo in_progress flag, runs run_embedding(), and always clears
    the flag (and stop_requested, and any article left stuck at
    "Pending") in a finally block — whether the run succeeded, partially
    failed, was stopped, or raised. Nothing is waiting on a return value
    here — the endpoint that scheduled this already responded. Progress
    is only visible by reloading the article list and checking each
    article's embedding_status; the final summary is logged server-side."""
    if is_embedding_running():
        return
    # Re-checked here (not just in the router) to close the race where two
    # trigger requests for different stages land close enough together
    # that both pass the router's check before either flag is actually
    # set — see repository.get_running_pipeline_stage().
    if repository.get_running_pipeline_stage():
        return

    repository.set_embedding_in_progress(True)
    try:
        summary = run_embedding()
        print(f"[embedding] {summary.message}")
    finally:
        repository.set_embedding_in_progress(False)
        repository.set_embedding_stop_requested(False)
        repository.reset_pending_embedding_articles()
