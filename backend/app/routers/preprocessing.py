"""
Router: preprocessing (clustering, extraction, condensing, entities,
contextualization, insights) triggering, status, and stop.

Kicks off one preprocessing pass as a background task and returns
immediately — mirrors routers/title_gate.py exactly (see that file's
docstring for the general shape). Progress is visible by polling
GET /articles/preprocessing/status, which is phase-aware (see
models.ClusteringRunStatus) rather than one blended counter — the
frontend can show all six phases' status at once, not just whichever is
currently running.
"""

from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel

from app import repository
from app.models import ClusterInsight, SimilarClusterRef
from app.services import preprocessing

router = APIRouter()


class PreprocessingTriggerResponse(BaseModel):
    status: str  # "started" | "already_running" | "skipped" | "stopping" | "not_running"
    message: str


class PreprocessingStatusResponse(BaseModel):
    in_progress: bool
    total: int
    processed: int
    estimated_minutes_remaining: Optional[float] = None
    phase: Optional[str] = None
    phase_total: int = 0
    phase_processed: int = 0
    days_done: int = 0
    articles_extracted_done: int = 0
    clusters_condensed_done: int = 0
    clusters_entities_done: int = 0
    clusters_contextualized_done: int = 0
    clusters_insights_done: int = 0
    # All-time day coverage (see repository.get_clustering_day_progress) —
    # distinct from days_done above, which counts only days clustered
    # *in this run*. Always freshly computed from current DB state,
    # independent of whether a run is active.
    days_clustered: int = 0
    days_total: int = 0


def _has_pending_work() -> bool:
    """True if any of the four phases has something to do right now —
    mirrors run_preprocessing()'s own upfront check, since "nothing
    pending" can't be answered by looking at clustering alone (a run can
    still have extraction/summary/entity work queued from an earlier
    clustering pass)."""
    return bool(
        repository.get_articles_pending_clustering()
        or repository.get_articles_pending_cluster_extraction()
        or repository.get_clusters_ready_for_condensing()
        or repository.get_clusters_ready_for_entities()
        or repository.get_clusters_ready_for_contextualization()
        or repository.get_clusters_ready_for_insights()
    )


@router.post("/articles/preprocessing", response_model=PreprocessingTriggerResponse)
def trigger_preprocessing(background_tasks: BackgroundTasks):
    """Starts a preprocessing run in the background. Safe to call
    repeatedly — if one's already running, or nothing is pending in any
    of the four phases, this reports that instead of starting a
    redundant run."""
    if preprocessing.is_preprocessing_running():
        return PreprocessingTriggerResponse(
            status="already_running",
            message="A preprocessing run is already in progress. Reload in a bit to check progress.",
        )

    other_stage = repository.get_running_pipeline_stage()
    if other_stage:
        return PreprocessingTriggerResponse(
            status="already_running",
            message=(
                f"A {other_stage} run is currently in progress — preprocessing "
                "doesn't run at the same time as the other stages (they'd "
                "fight each other for CPU). Try again once it finishes."
            ),
        )

    if not _has_pending_work():
        return PreprocessingTriggerResponse(
            status="skipped",
            message="Nothing pending in any preprocessing phase — nothing to do.",
        )

    background_tasks.add_task(preprocessing.run_preprocessing_job)
    return PreprocessingTriggerResponse(
        status="started",
        message="Preprocessing started in the background. Reload the page in a bit to see progress.",
    )


@router.get("/articles/preprocessing/status", response_model=PreprocessingStatusResponse)
def get_preprocessing_run_status():
    """Live progress for the frontend to poll. `phase` names whichever
    phase is currently running (or just finished); `phase_total`/
    `phase_processed` are scoped to that phase alone, so the ETA below
    means something concrete instead of blending a near-instant
    clustering step with a slow LLM call.

    The `*_done` counters are **not** read from the clustering_status meta
    document — that document's own counters are a per-run snapshot (reset
    to 0 at the start of every run), so displaying them whenever no run is
    active, or right after that document is cleared, makes a fully-worked
    backlog look untouched. They're computed live from `articles`/
    `clusters` instead (repository.get_preprocessing_phase_totals(),
    days_done from the same day_progress every other view already uses),
    so this endpoint always reflects the real backlog state regardless of
    run/meta-document state."""
    day_progress = repository.get_clustering_day_progress()
    phase_totals = repository.get_preprocessing_phase_totals()
    status = repository.get_clustering_status()
    if status is None:
        return PreprocessingStatusResponse(
            in_progress=False, total=0, processed=0,
            days_done=day_progress["days_clustered"], **phase_totals, **day_progress,
        )

    eta_minutes = None
    if status.in_progress and status.avg_seconds_per_article is not None:
        remaining = max(status.phase_total - status.phase_processed, 0)
        eta_minutes = round((status.avg_seconds_per_article * remaining) / 60, 1)

    return PreprocessingStatusResponse(
        in_progress=status.in_progress,
        total=status.total,
        processed=status.processed,
        estimated_minutes_remaining=eta_minutes,
        phase=status.phase,
        phase_total=status.phase_total,
        phase_processed=status.phase_processed,
        days_done=day_progress["days_clustered"],
        **phase_totals,
        **day_progress,
    )


@router.post("/articles/preprocessing/stop", response_model=PreprocessingTriggerResponse)
def stop_preprocessing():
    """Requests that the current run stop after the item it's on right
    now finishes. See routers/title_gate.py's stop_title_gate() —
    identical reasoning."""
    if preprocessing.request_preprocessing_stop():
        return PreprocessingTriggerResponse(
            status="stopping",
            message=(
                "Stop requested. The current item will finish, then the "
                "run will end. Reload in a bit to confirm it stopped."
            ),
        )
    return PreprocessingTriggerResponse(
        status="not_running",
        message="No preprocessing run is currently in progress.",
    )


class ClusterResponse(BaseModel):
    cluster_id: str
    date: str
    article_ids: List[str]
    titles: List[str] = []
    chunks: List[str] = []
    condensed: bool = False
    entities: List[str] = []
    entities_extracted: bool = False
    similar_clusters: List[SimilarClusterRef] = []
    contextualized: bool = False
    insights: List[ClusterInsight] = []
    insights_generated: bool = False
    # Computed on read, not stored — see repository.get_cluster_display_info.
    representative_title: str
    extraction_status: str  # "not_extracted" | "extracting" | "extracted"


@router.get("/clusters", response_model=List[ClusterResponse])
def list_clusters(date: Optional[str] = None):
    """Lists clusters, optionally filtered to one UTC day ("YYYY-MM-DD")
    — the read side the frontend needs to actually show clustering/
    condensation/entity results, not just the job's own progress."""
    result = []
    for cluster in repository.get_clusters(date):
        display = repository.get_cluster_display_info(cluster)
        result.append(ClusterResponse(
            cluster_id=cluster.cluster_id,
            date=cluster.date,
            article_ids=cluster.article_ids,
            titles=cluster.titles,
            chunks=cluster.chunks,
            condensed=cluster.condensed,
            entities=cluster.entities,
            entities_extracted=cluster.entities_extracted,
            similar_clusters=cluster.similar_clusters,
            contextualized=cluster.contextualized,
            insights=cluster.insights,
            insights_generated=cluster.insights_generated,
            representative_title=display["representative_title"],
            extraction_status=display["extraction_status"],
        ))
    return result


class DayOverviewResponse(BaseModel):
    date: str
    total: int
    gated: int
    archived: int
    passed: int
    is_gate_complete: bool
    extracted_successful: int
    extraction_attempted: int
    extraction_success_rate: Optional[float] = None
    cluster_count: int
    is_clustered: bool


@router.get("/days-overview", response_model=List[DayOverviewResponse])
def get_days_overview():
    """One row per UTC calendar day with at least one article, oldest
    first — the day-by-day cluster browser's data source (see
    repository.get_day_cluster_overview for the per-field meaning)."""
    return [DayOverviewResponse(**row) for row in repository.get_day_cluster_overview()]
