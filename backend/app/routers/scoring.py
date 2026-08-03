"""
Router: relativity-scoring triggering.

Mirrors routers/embedding.py exactly — same trigger/status/stop shape,
same reasoning (a scoring run over hundreds of articles is far too slow
for a single HTTP request, so it runs as a background task and is
polled for progress instead).
"""

from typing import Literal, Optional, Tuple

from bson.errors import InvalidId
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from app import repository
from app.services import scorer, scoring_job

router = APIRouter()


class ScoringTriggerResponse(BaseModel):
    status: str  # "started" | "already_running" | "skipped" | "stopping" | "not_running"
    message: str


class ScoringStatusResponse(BaseModel):
    in_progress: bool
    total: int
    processed: int
    estimated_minutes_remaining: Optional[float] = None


@router.post("/articles/score", response_model=ScoringTriggerResponse)
def trigger_scoring(background_tasks: BackgroundTasks):
    """Starts a scoring run in the background. Safe to call repeatedly —
    if one's already running, this reports that instead of starting a
    second overlapping run. If there's nothing pending scoring right now,
    this says so immediately instead of spinning up a background job just
    to discover the same thing a moment later — that "nothing to do"
    outcome is reported here and nowhere else; it never touches
    last_run_at/last_run_status, which keep reflecting the most recent run
    that actually did something."""
    if scoring_job.is_scoring_running():
        return ScoringTriggerResponse(
            status="already_running",
            message="A scoring run is already in progress. Reload in a bit to check progress.",
        )

    other_stage = repository.get_running_pipeline_stage()
    if other_stage:
        return ScoringTriggerResponse(
            status="already_running",
            message=(
                f"A {other_stage} run is currently in progress — extraction, "
                "embedding, and scoring don't run at the same time (they'd "
                "fight each other for CPU). Try again once it finishes."
            ),
        )

    if not repository.get_articles_pending_scoring():
        return ScoringTriggerResponse(
            status="skipped",
            message="All articles are already scored — nothing left to score.",
        )

    background_tasks.add_task(scoring_job.run_scoring_job)
    return ScoringTriggerResponse(
        status="started",
        message=(
            "Scoring started in the background. Reload the page in a "
            "minute or two to see progress — articles will show as "
            "'Pending' while being processed, then settle into "
            "'Scored'/'Failed'."
        ),
    )


@router.get("/articles/score/status", response_model=ScoringStatusResponse)
def get_scoring_run_status():
    """Live progress for the frontend to poll — same shape and same
    recency-weighted ETA as /articles/embed/status."""
    status = repository.get_scoring_status()
    if status is None:
        return ScoringStatusResponse(in_progress=False, total=0, processed=0)

    eta_minutes = None
    if status.in_progress and status.avg_seconds_per_article is not None:
        remaining_articles = max(status.total - status.processed, 0)
        eta_minutes = round((status.avg_seconds_per_article * remaining_articles) / 60, 1)

    return ScoringStatusResponse(
        in_progress=status.in_progress,
        total=status.total,
        processed=status.processed,
        estimated_minutes_remaining=eta_minutes,
    )


@router.post("/articles/score/stop", response_model=ScoringTriggerResponse)
def stop_scoring():
    """Requests that the current run stop after the article it's on right
    now finishes. See routers/embedding.py's stop_embedding() — identical
    reasoning."""
    if scoring_job.request_scoring_stop():
        return ScoringTriggerResponse(
            status="stopping",
            message=(
                "Stop requested. The current article will finish, then the "
                "run will end. Reload in a bit to confirm it stopped."
            ),
        )
    return ScoringTriggerResponse(
        status="not_running",
        message="No scoring run is currently in progress.",
    )


@router.get("/tree/nodes")
def get_tree_nodes():
    """The relevance tree's id/parent/depth/labels (no embeddings) — lets
    the frontend resolve a score_explanation's triggered node_ids into
    readable labels and root paths for the article detail page."""
    return scorer.get_tree_nodes_for_display()


# Direct, per-article score nudge from a feedback click — separate from
# (and additive on top of) scorer.FEEDBACK_STEP's tree-threshold nudge.
# The threshold nudge shapes future/other articles' scoring but rarely
# moves THIS article's own number (a threshold shift doesn't change a
# similarity value already well clear of the boundary); this constant is
# what guarantees the click visibly changes the article you clicked on.
FEEDBACK_SCORE_STEP = 5


class RelativityFeedbackRequest(BaseModel):
    direction: Literal["more", "less"]


class RelativityFeedbackResponse(BaseModel):
    relativity_score: int
    score_explanation: dict


def _fetch_article_or_404(article_id: str):
    try:
        found = repository.get_articles_by_ids([article_id])
    except InvalidId:
        raise HTTPException(status_code=404, detail="Article not found")
    if not found:
        raise HTTPException(status_code=404, detail="Article not found")
    return found[0]


def _rescore_article(article, overrides: Optional[dict] = None, score_adjustment: Optional[int] = None) -> Tuple[int, dict]:
    """Re-runs scoring for one article against the current relevance tree
    (baseline thresholds + any accumulated feedback overrides), adds that
    article's accumulated direct score adjustment (clamped to [0, 100]),
    and persists the result. Shared by the feedback endpoint (which
    computes fresh overrides/adjustment first) and the plain rescore
    endpoint (which just wants the latest tree state + existing
    adjustment, neither changed)."""
    if overrides is None:
        overrides = repository.get_node_threshold_overrides()
    if score_adjustment is None:
        score_adjustment = article.relativity_score_adjustment or 0

    raw_score, explanation = scorer.score_article(article.chunk_ids, threshold_overrides=overrides)
    final_score = min(100, max(0, raw_score + score_adjustment))
    repository.set_article_scoring_result(article.id, final_score, explanation, "Scored")
    return final_score, explanation


@router.post("/articles/{article_id}/relativity-feedback", response_model=RelativityFeedbackResponse)
def submit_relativity_feedback(article_id: str, payload: RelativityFeedbackRequest):
    """"More relative" / "less relative" feedback buttons. Two effects,
    both persisted:
      1. Nudges the threshold of every node this article's last scoring
         run triggered (see scorer.compute_relativity_feedback_updates),
         stored as overrides in the node_threshold_overrides collection
         (rather than rewriting the multi-MB tree file on every click) —
         shapes every future scoring run, but rarely moves THIS article's
         own score (a threshold shift doesn't change a similarity value
         already well clear of the boundary).
      2. Adds/subtracts FEEDBACK_SCORE_STEP directly to/from this
         article's own relativity_score_adjustment — guarantees the
         click visibly changes the number you're looking at, independent
         of whether any threshold actually flipped.
    Then immediately re-scores this one article with both effects
    applied."""
    article = _fetch_article_or_404(article_id)
    if article.scored_status != "Scored" or not article.score_explanation:
        raise HTTPException(status_code=400, detail="Article has not been scored yet")

    overrides = repository.get_node_threshold_overrides()
    active_thresholds = scorer.get_active_thresholds(overrides)
    updates = scorer.compute_relativity_feedback_updates(article.score_explanation, payload.direction, active_thresholds)
    if updates:
        repository.set_node_threshold_overrides(updates)
        overrides.update(updates)

    sign = 1 if payload.direction == "more" else -1
    new_adjustment = (article.relativity_score_adjustment or 0) + sign * FEEDBACK_SCORE_STEP
    repository.set_article_score_adjustment(article_id, new_adjustment)

    score, explanation = _rescore_article(article, overrides, new_adjustment)
    return RelativityFeedbackResponse(relativity_score=score, score_explanation=explanation)


@router.post("/articles/{article_id}/rescore", response_model=RelativityFeedbackResponse)
def rescore_article(article_id: str):
    """Manually re-scores one already-scored article against the relevance
    tree as it currently stands (including any threshold overrides other
    articles' feedback has accumulated), without itself giving feedback
    or changing any threshold — for when the tree has moved since this
    article was last scored and you just want its score refreshed."""
    article = _fetch_article_or_404(article_id)
    if article.embedding_status != "Embedded" or not article.chunk_ids:
        raise HTTPException(status_code=400, detail="Article has not been embedded yet")

    score, explanation = _rescore_article(article)
    return RelativityFeedbackResponse(relativity_score=score, score_explanation=explanation)
