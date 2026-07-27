"""
Relativity-scoring orchestrator.

This is the "Relativity scoring" counterpart to services/embedder.py's
run_embedding() — same background-job pattern (Mongo-backed in_progress
guard, stop_requested cooperative flag, progress/ETA tracking, per-run
log collection), just calling services/scorer.py's pure score_article()
as the core computation instead of the embedding model.

Workflow, every time it's triggered:
  1. Guard   -> refuse to start if a scoring run is already in progress
                (meta._id="scoring_status".in_progress, Mongo-backed so it
                survives a process restart — same caveat as embedding: no
                staleness timeout, a crash needs the startup-guard-reset).
  2. Gather  -> every article with embedding_status == "Embedded" and
                scored_status != "Scored" (incremental; "Failed" articles
                are naturally retried next run).
  3. Score   -> for each candidate: mark 'Pending', call
                scorer.score_article(chunk_ids), record relativity_score /
                score_explanation / scored_status. A failure on one
                article is logged and does not abort the run.
  4. Log     -> one scoring_runs document for the whole run.
"""

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

from app import repository
from app.services.scorer import score_article

RATE_EMA_ALPHA = 0.3


@dataclass
class ScoringSummary:
    status: str  # "success" | "partial" | "failed" | "skipped" | "stopped"
    message: str
    scored_articles: int = 0
    failed_articles: int = 0
    errors: List[str] = field(default_factory=list)


def run_scoring() -> ScoringSummary:
    now = datetime.now(timezone.utc)
    candidates = repository.get_articles_pending_scoring()

    if not candidates:
        summary = ScoringSummary(status="skipped", message="No articles pending scoring.")
        repository.set_scoring_run_outcome(now, summary.status)
        repository.log_scoring_run(
            status=summary.status,
            message=summary.message,
            scored_articles=0,
            failed_articles=0,
            errors=[],
        )
        return summary

    total = len(candidates)
    scored_count = 0
    failed_count = 0
    errors: List[str] = []
    repository.set_scoring_started(now)
    repository.set_scoring_progress(total, 0)

    # Fetched once for the whole run, not per article — a tree walk reads
    # this for every chunk, and it only changes via feedback clicks, which
    # don't happen mid-run.
    threshold_overrides = repository.get_node_threshold_overrides()

    avg_seconds_per_article: Optional[float] = None
    last_checkpoint = time.monotonic()

    stopped = False
    for article in candidates:
        status = repository.get_scoring_status()
        if status and status.stop_requested:
            stopped = True
            break

        repository.set_article_scoring_status(article.id, "Pending")
        try:
            raw_score, explanation = score_article(article.chunk_ids, threshold_overrides=threshold_overrides)
            # Re-applies any "more"/"less relative" feedback this article
            # already accumulated (see routers/scoring.py) — a bulk run
            # must not silently wipe out prior per-article adjustments.
            final_score = min(100, max(0, raw_score + (article.relativity_score_adjustment or 0)))
            repository.set_article_scoring_result(article.id, final_score, explanation, "Scored")
            scored_count += 1
        except Exception as e:
            repository.set_article_scoring_status(article.id, "Failed")
            failed_count += 1
            errors.append(f"{article.title}: {e}")

        checkpoint = time.monotonic()
        duration = checkpoint - last_checkpoint
        last_checkpoint = checkpoint
        avg_seconds_per_article = (
            duration if avg_seconds_per_article is None
            else RATE_EMA_ALPHA * duration + (1 - RATE_EMA_ALPHA) * avg_seconds_per_article
        )

        repository.set_scoring_progress(total, scored_count + failed_count)
        repository.set_scoring_rate(avg_seconds_per_article)

    if stopped:
        run_status = "stopped"
    elif failed_count == 0:
        run_status = "success"
    elif scored_count == 0:
        run_status = "failed"
    else:
        run_status = "partial"

    message = (
        f"Scoring {run_status}: {scored_count} scored, {failed_count} failed "
        f"(of {len(candidates)} candidates)."
    )

    repository.set_scoring_run_outcome(now, run_status)
    repository.log_scoring_run(
        status=run_status,
        message=message,
        scored_articles=scored_count,
        failed_articles=failed_count,
        errors=errors,
    )

    return ScoringSummary(
        status=run_status,
        message=message,
        scored_articles=scored_count,
        failed_articles=failed_count,
        errors=errors,
    )


def is_scoring_running() -> bool:
    """Returns True if a scoring run is currently in progress, per the
    Mongo-backed flag."""
    status = repository.get_scoring_status()
    return bool(status and status.in_progress)


def request_scoring_stop() -> bool:
    """Asks the current run to stop after the article it's working on
    right now. Returns False if nothing is running to stop."""
    if not is_scoring_running():
        return False
    repository.set_scoring_stop_requested(True)
    return True


def run_scoring_job() -> None:
    """Background-task entrypoint: guards against overlapping runs,
    always clears in_progress/stop_requested and resets any article left
    stuck at "Pending" in a finally block — whether the run succeeded,
    partially failed, was stopped, or raised."""
    if is_scoring_running():
        return

    repository.set_scoring_in_progress(True)
    try:
        summary = run_scoring()
        print(f"[scoring] {summary.message}")
    finally:
        repository.set_scoring_in_progress(False)
        repository.set_scoring_stop_requested(False)
        repository.reset_pending_scoring_articles()
