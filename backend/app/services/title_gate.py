"""
Title-gate orchestrator.

As of 2026-08-26, the live job gates on the embedding-based nearest-centroid
classifier alone (services/title_gate_model.py), NOT the LLM
(services/llm_insight.gate_title) — the classifier was trained on the LLM
gate's own past decisions and evaluated as a close-enough, far cheaper
substitute (see docs/log.md's 2026-08-25/26 entries: 99.0% recall / 71.1%
precision, ~5-30x faster on CPU depending on measurement). This is a
deliberate, reversible choice, not a removal: llm_insight.gate_title() is
untouched and still fully callable — comparison rounds against fresh
model-only-gated articles happen via the standalone scripts
(compare_model_vs_llm_gate.py, export_title_gate_dataset.py, etc.), not by
calling the LLM on every article here anymore. Revert by swapping the
model call below back for llm_insight.gate_title() (see git history around
that date for the prior OR-ensemble version, which ran both and archived
only if both agreed) once there's enough accumulated comparison data to
decide for good.

Split out from the scraper (see services/scraper.py) so "Update now" stays
a plain, fast API-fetch operation — the classification work happens here
instead, as its own separate, poll-able, stoppable background stage,
exactly like extraction/embedding/scoring.

Workflow, every time it's triggered:
  1. Gather -> every article with ingestion_status == "No Extraction" and
     title_gate_status != "Gated" (see repository.get_articles_pending_title_gate).
     Anything already Successful/Failed/Expired/Archived was either already
     gated, or predates this stage entirely (handled by a one-off backfill)
     — this job only owns the live, ongoing scrape backlog.
  2. Gate   -> for each candidate, embed the title and classify it
     (title_gate_model.embed_title / classify_embedding).
     Relevant: title_gate_status flips to "Gated", nothing else changes —
       it proceeds to extraction as normal on the next run of that stage.
     Not relevant: ingestion_status also flips to "Archived" — it never
       reaches extraction/embedding/scoring, but stays visible on the
       Archived review page (see routers/articles.py) for a human to
       disarchive if the gate got it wrong.
     Error (embedding model failure): fails open the same way the LLM path
       used to — neither field changes, so the article is retried on the
       next run rather than being silently stuck or lost.
     title_gate_llm_decision is left None here (the LLM isn't called live
     anymore); title_gate_model_decision and title_embedding are always
     recorded (see repository.set_title_gate_result).

Progress/stop, mirroring services/extractor.py exactly: a Mongo-backed
meta._id="title_gate_status" document with in_progress/total/processed/
stop_requested/avg_seconds_per_article, plus an in-process lock as the hard
guard against two runs racing in the same process.
"""

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List

from app import repository
from app.services import title_gate_model

RATE_EMA_ALPHA = 0.3

_title_gate_lock = threading.Lock()


@dataclass
class TitleGateSummary:
    status: str  # "success" | "partial" | "failed" | "skipped" | "stopped"
    message: str
    gated_articles: int = 0
    archived_articles: int = 0
    errors: List[str] = field(default_factory=list)


def run_title_gate() -> TitleGateSummary:
    candidates = repository.get_articles_pending_title_gate()

    if not candidates:
        return TitleGateSummary(
            status="skipped",
            message="Every article has already been through the title gate — nothing left to check.",
        )

    total = len(candidates)
    gated_count = 0
    archived_count = 0
    error_count = 0
    errors: List[str] = []
    now = datetime.now(timezone.utc)
    repository.set_title_gate_started(now)
    repository.set_title_gate_progress(total, 0)

    avg_seconds_per_article = None
    last_checkpoint = time.monotonic()
    stopped = False

    for article in candidates:
        status_doc = repository.get_title_gate_status()
        if status_doc and status_doc.stop_requested:
            stopped = True
            break

        try:
            embedding = title_gate_model.embed_title(article.title)
            model_relevant = title_gate_model.classify_embedding(embedding)
        except Exception as e:
            # Embedding/classification itself failed -- skip this article
            # entirely this run (same fail-open policy the LLM path used
            # when it was the live mechanism), so it's retried next run
            # rather than being gated on a guess or silently stuck.
            error_count += 1
            errors.append(f"{article.title}: {e}")
            checkpoint = time.monotonic()
            duration = checkpoint - last_checkpoint
            last_checkpoint = checkpoint
            avg_seconds_per_article = (
                duration if avg_seconds_per_article is None
                else RATE_EMA_ALPHA * duration + (1 - RATE_EMA_ALPHA) * avg_seconds_per_article
            )
            repository.set_title_gate_progress(total, gated_count + error_count)
            repository.set_title_gate_rate(avg_seconds_per_article)
            continue

        relevant = model_relevant
        repository.set_title_gate_result(
            article.id,
            archived=not relevant,
            llm_relevant=None,
            model_relevant=model_relevant,
            title_embedding=embedding.tolist(),
        )
        gated_count += 1
        if not relevant:
            archived_count += 1

        checkpoint = time.monotonic()
        duration = checkpoint - last_checkpoint
        last_checkpoint = checkpoint
        avg_seconds_per_article = (
            duration if avg_seconds_per_article is None
            else RATE_EMA_ALPHA * duration + (1 - RATE_EMA_ALPHA) * avg_seconds_per_article
        )
        repository.set_title_gate_progress(total, gated_count + error_count)
        repository.set_title_gate_rate(avg_seconds_per_article)

    if stopped:
        run_status = "stopped"
    elif error_count and gated_count == 0:
        run_status = "failed"
    elif error_count:
        run_status = "partial"
    else:
        run_status = "success"

    message = (
        f"Title gate {run_status}: {gated_count} checked "
        f"({archived_count} archived), {error_count} error(s) "
        f"(of {total} candidates)."
    )

    repository.set_title_gate_run_outcome(now, run_status)
    repository.log_title_gate_run(
        status=run_status,
        message=message,
        gated_articles=gated_count,
        archived_articles=archived_count,
        errors=errors,
    )

    return TitleGateSummary(
        status=run_status,
        message=message,
        gated_articles=gated_count,
        archived_articles=archived_count,
        errors=errors,
    )


def is_title_gate_running() -> bool:
    """Returns True if a title-gate run is currently in progress, per the
    Mongo-backed flag — mirrors extractor.is_extraction_running()."""
    status = repository.get_title_gate_status()
    return bool(status and status.in_progress)


def request_title_gate_stop() -> bool:
    """Asks the current run to stop after the article it's working on
    right now finishes. Returns False if nothing is running to stop."""
    if not is_title_gate_running():
        return False
    repository.set_title_gate_stop_requested(True)
    return True


def run_title_gate_job() -> None:
    """Background-task entrypoint — mirrors extractor.run_extraction_job()
    exactly: guards against overlapping runs with both the Mongo
    in_progress flag and the in-process lock, refuses to start alongside
    any other heavy stage, and always clears in_progress/stop_requested in
    a finally block whether the run succeeded, partially failed, was
    stopped, or raised."""
    if is_title_gate_running():
        return
    if repository.get_running_pipeline_stage():
        return
    if not _title_gate_lock.acquire(blocking=False):
        return

    repository.set_title_gate_in_progress(True)
    try:
        summary = run_title_gate()
        print(f"[title-gate] {summary.message}")
    finally:
        repository.set_title_gate_in_progress(False)
        repository.set_title_gate_stop_requested(False)
        _title_gate_lock.release()
