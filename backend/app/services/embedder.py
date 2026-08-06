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

The actual chunk/encode/upsert work for each article runs in a persistent
*subprocess* (services/embedding_worker.py), not in this process directly
— see _EmbeddingWorker below for why: a subprocess is the only thing that
can be forcibly killed to reclaim CPU/memory on a timeout, which matters a
lot for this specific job (see EMBEDDING_TIMEOUT_SECONDS).
"""

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from app import repository
from app.services.embedding_worker import EmbeddingWorker

EMBEDDING_MODEL_NAME = "Qwen/Qwen3-Embedding-0.6B"
PINECONE_INDEX_NAME = "yazaki"
# Weight given to each new per-article duration when updating the
# seconds-per-article moving average — higher reacts faster to changes in
# speed, at the cost of more jitter in the reported ETA.
RATE_EMA_ALPHA = 0.3
# An article whose chunking+encoding+upsert takes longer than this is
# abandoned and marked "Failed" rather than letting it block every article
# behind it in the run indefinitely. Enforced by forcibly killing the
# worker subprocess handling it (see embedding_worker.py) — the only way
# to actually reclaim its CPU and memory, since Python can't force-kill a
# thread. The next article gets a freshly spawned worker (paying the
# model-reload cost again, but only on this rare path, not on every
# article).
EMBEDDING_TIMEOUT_SECONDS = 300


@dataclass
class EmbeddingSummary:
    status: str  # "success" | "partial" | "failed" | "skipped"
    message: str
    embedded_articles: int = 0
    failed_articles: int = 0
    errors: List[str] = field(default_factory=list)


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

    total = len(candidates)
    embedded_count = 0
    failed_count = 0
    errors: List[str] = []
    repository.set_embedding_started(now)
    repository.set_embedding_progress(total, 0)

    avg_seconds_per_article: Optional[float] = None
    last_checkpoint = time.monotonic()

    stopped = False
    worker = EmbeddingWorker(EMBEDDING_MODEL_NAME, PINECONE_INDEX_NAME, EMBEDDING_TIMEOUT_SECONDS)
    try:
        for article in candidates:
            status = repository.get_embedding_status()
            if status and status.stop_requested:
                stopped = True
                break

            repository.set_article_embedding_status(article.id, "Pending")
            try:
                chunk_ids = worker.embed(article.id, article.language, article.content, str(article.url))
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
    finally:
        # Always tears down the worker subprocess — whether the run
        # finished, was stopped, or raised — so nothing is left running
        # after run_embedding() returns.
        worker.shutdown()

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
