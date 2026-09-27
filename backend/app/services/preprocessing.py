"""
Preprocessing orchestrator — Stage 2 of the RAG pipeline redesign
(docs/brainstorm-general-architecture.md). Classification (title-gate) is
its own separate, earlier stage; this one covers everything after it:
clustering, article extraction, condensation, entity extraction, and
contextualization — in that order, as five phases of one job. A later
"storing" stage is not part of this module.

**Article extraction was folded in here from the retired standalone
"Extract articles" stage** (services/extractor.py) — extraction now only
ever happens on clustered articles, triggered from this job, not as an
independent top-level stage. extractor.py's actual page-load/text-pull
logic (_extract_one) and its constants are reused as-is, not duplicated;
only the orchestration (which articles, when) changed. The single-article
"retry extraction" endpoint (extractor.extract_single_article) is
untouched and still works independently for Failed articles.

**Phase 3 non-LLM rewrite (2026-09-09)**: cluster condensation used to be
one Ollama LLM call per cluster (llm_insight.summarize_cluster) producing
an abstractive paragraph — measured at ~100-200s/cluster on this CPU-only
hardware, projected at 9+ hours for a single backlog run. Replaced with
services/cluster_condenser.py: pure embedding-based extractive
condensation (dedup member titles, split+dedup+MMR-select member content
into up to ~100 diverse chunks), a small fraction of a second per cluster.
llm_insight.summarize_cluster is left in place, unused, for the same
"future reconsideration" reason the LLM title gate was kept alongside the
embedding classifier — see services/title_gate.py.

Phase 1 — Cluster: complete-linkage agglomerative clustering (SciPy) over
L2-normalized title embeddings, cosine distance, cut via the
inconsistency-coefficient criterion at t=1.1 (depth=2) — the exact method
and threshold validated in backend/clustering_validation.ipynb against a
400-pair hand-labeled ground truth. Complete-linkage was picked on a
held-out validation set (F1=0.711) over HDBSCAN (F1=0.651), though the two
*reversed* on the final test set (HDBSCAN 0.698 vs. complete-linkage
0.625) — a low-confidence pick given the small sample, not a settled one.
See docs/rag_redesign/03_clustering.md. Scoped per UTC calendar day
(docs/brainstorm-general-architecture.md decision #5) — a story spanning
multiple days is not merged into one cluster; cross-day continuity is a
separate, not-yet-built retrieval step. A day with one candidate is still
its own singleton cluster.

**Day-ordering (2026-09-08)**: title-gating itself now processes articles
oldest-published-first (services/title_gate.py via
repository.get_articles_pending_title_gate()'s sort), and clustering
enforces the other half of that ordering — a day is only clustered once
every article published on it, and every earlier day, has a title-gate
decision (repository.get_earliest_incomplete_title_gate_date()). A day at
or after that cutoff is skipped this run rather than clustered on
whatever partial set happens to be gated so far; it picks up automatically
once gating catches up.

Phase 2 — Extract: every clustered article still in "No Extraction" gets
its full text pulled via extractor._extract_one, exactly like the retired
stage did.

Phase 3 — Condense: once every member of a cluster has reached a terminal
ingestion_status (Successful/Failed), cluster_condenser.py produces the
cluster's `titles` (member titles deduplicated by embedding similarity)
and its full deduped set of content chunks, each flagged with whether
MMR selected it (stopping once nothing left is meaningfully different,
not at a fixed count — see that module's docstring). Every one of those
chunks' embeddings — not just the selected subset — is persisted to
Pinecone (repository.upsert_condensed_chunk_embeddings) for cluster-based
contextualization to search over later; Cluster.chunks (what phase 4
below actually reads) only ever holds the MMR-selected text. A cluster
where every member failed extraction still gets `titles`, just no chunks
at all.

Phase 4 — Entities: a multilingual NER model (entity_extraction.py) runs
over each condensed cluster's `chunks` — not its titles, so entity
extraction sees only actual article content, not headline phrasing. Raw
NER mentions are then canonicalized (2026-09-11) into the persistent
entity database (models.Entity) by embedding similarity, so the same
real-world entity ("Renault" / "Groupe Renault") is tracked as one thing
across every cluster it appears in — see entity_extraction.canonicalize_entities
and docs/completion-plan.md for why this feeds entity-based relativity
scoring and cluster-based contextualization next.

Phase 5 — Contextualize (2026-09-14): contextualization.py links each
condensed, entity-extracted cluster to the earlier clusters most likely
to be a related past event — the entity-to-cluster inverted index built
during phase 4 (models.Entity.cluster_ids) resolves candidates cheaply
(no scanning the whole clusters collection), and each candidate is
scored by (Pinecone chunk-embedding centroid similarity) x (shared real
entity count). Only clusters dated strictly before this one qualify. A
cluster with no similar past cluster gets an empty `similar_clusters`
list — a legitimate "genuinely new story" outcome, not a failure; phase 6
below still proceeds from `chunks` alone in that case. See
services/contextualization.py's docstring for why this succeeds where
"relevance condensing" (an earlier, since-reverted 5th phase) failed:
that attempt had no real query to score chunks against, this one does
(the cluster itself, compared against real past clusters).

Phase 6 — Insights (2026-09-15): llm_insight.assess_cluster() judges each
contextualized cluster's condensed chunks (plus its similar_clusters
context) against the business-logic rules and returns structured
insights, each citing which chunk indices support it — see
models.ClusterInsight. Replaces the earlier per-article, on-demand daily
report stage (report_job.py/routers/reports.py's old generate endpoint,
removed the same day): that flow called the LLM once per article in a
contextualized cluster, once per report-generation click; this calls it
once per cluster, automatically, as a normal backlog phase, and can
actually use similar_clusters (an article-level call never had that
context). Skipped for this run (not an error) if Ollama isn't reachable —
checked once up front, same fail-soft treatment extraction's Playwright
lock contention gets in phase 2.

Progress/stop: a single Mongo-backed meta._id="clustering_status"
document, shared across all six phases — total/processed are combined
work units (days + articles + clusters + clusters + clusters + clusters),
so the total actually *grows* as later phases' candidate counts become
known (phase 2's candidates aren't knowable until phase 1 has run), not a
fixed number computed up front. stop_requested is checked between every
individual item in every phase, same granularity as every other stage in
this app.
"""

import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np
from playwright.sync_api import sync_playwright
from scipy.cluster.hierarchy import fcluster, linkage

from app import repository
from app.services import cluster_condenser, contextualization, entity_extraction, extractor, llm_insight

RATE_EMA_ALPHA = 0.3

# Validated in backend/clustering_validation.ipynb — selected on TRAIN via
# grid search, confirmed as the better VAL-set choice between the two
# candidate algorithms (see docs/rag_redesign/03_clustering.md for the
# full validation, including the test-set reversal caveat above).
LINKAGE_THRESHOLD = 1.1
LINKAGE_METHOD = "complete"
LINKAGE_METRIC = "cosine"
FCLUSTER_CRITERION = "inconsistent"
FCLUSTER_DEPTH = 2

_preprocessing_lock = threading.Lock()


@dataclass
class PreprocessingSummary:
    status: str  # "success" | "partial" | "failed" | "skipped" | "stopped"
    message: str
    days_processed: int = 0
    articles_clustered: int = 0
    articles_extracted: int = 0
    clusters_condensed: int = 0
    clusters_entities_extracted: int = 0
    clusters_contextualized: int = 0
    clusters_insights_generated: int = 0
    errors: List[str] = field(default_factory=list)


def _normalize(x: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(x, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return x / norms


def _cluster_day(date_key: str, day_articles: list, existing_count: int) -> Dict[str, str]:
    """Returns {article_id: cluster_id} for one day's candidates. Labels
    are offset by `existing_count` (how many clusters this day already
    has from an earlier run) so a second run on a day that was already
    partly clustered can never mint a cluster_id that collides with an
    existing one — scipy's fcluster always restarts its own labels at 1
    for a fresh batch.

    A single article is its own singleton cluster — scipy's linkage()
    needs at least 2 observations, and a lone story doesn't need
    clustering machinery to know it's alone. Everything else goes through
    complete-linkage exactly as validated in the notebook: L2-normalize,
    then linkage(method="complete", metric="cosine") cut at
    fcluster(t=1.1, criterion="inconsistent", depth=2)."""
    if len(day_articles) == 1:
        return {day_articles[0].id: f"{date_key}-{existing_count + 1}"}

    embeddings = np.array([a.title_embedding for a in day_articles], dtype=np.float64)
    embeddings_norm = _normalize(embeddings)
    linkage_matrix = linkage(embeddings_norm, method=LINKAGE_METHOD, metric=LINKAGE_METRIC)
    labels = fcluster(
        linkage_matrix, t=LINKAGE_THRESHOLD, criterion=FCLUSTER_CRITERION, depth=FCLUSTER_DEPTH
    )

    return {
        article.id: f"{date_key}-{existing_count + label}"
        for article, label in zip(day_articles, labels)
    }


def run_preprocessing() -> PreprocessingSummary:
    clustering_candidates = repository.get_articles_pending_clustering()
    extraction_peek = repository.get_articles_pending_cluster_extraction()
    condensing_peek = repository.get_clusters_ready_for_condensing()
    entity_peek = repository.get_clusters_ready_for_entities()
    contextualization_peek = repository.get_clusters_ready_for_contextualization()
    insights_peek = repository.get_clusters_ready_for_insights()

    if not (
        clustering_candidates or extraction_peek or condensing_peek
        or entity_peek or contextualization_peek or insights_peek
    ):
        return PreprocessingSummary(
            status="skipped",
            message="Nothing pending in any preprocessing phase — nothing to do.",
        )

    now = datetime.now(timezone.utc)
    repository.set_clustering_started(now)

    error_count = 0
    errors: List[str] = []
    avg_seconds_per_unit: Optional[float] = None
    last_checkpoint = time.monotonic()
    stopped = False

    days_processed = 0
    articles_clustered = 0
    articles_extracted = 0
    clusters_condensed = 0
    clusters_entities_extracted = 0
    clusters_contextualized = 0
    clusters_insights_generated = 0
    # Wall-clock time actually spent in each phase this run, for
    # log_clustering_run's phase_durations — separate from tick()'s
    # avg_seconds_per_unit (a live per-item rate for the ETA), this is the
    # total elapsed time per phase, the "full picture" breakdown shown in
    # the Logs page.
    phase_durations: Dict[str, float] = {}

    def stop_requested() -> bool:
        status_doc = repository.get_clustering_status()
        return bool(status_doc and status_doc.stop_requested)

    def tick(phase: str, phase_total: int, phase_processed: int):
        nonlocal avg_seconds_per_unit, last_checkpoint
        checkpoint = time.monotonic()
        duration = checkpoint - last_checkpoint
        last_checkpoint = checkpoint
        avg_seconds_per_unit = (
            duration if avg_seconds_per_unit is None
            else RATE_EMA_ALPHA * duration + (1 - RATE_EMA_ALPHA) * avg_seconds_per_unit
        )
        repository.set_clustering_phase_progress(
            phase, phase_total, phase_processed,
            days_processed, articles_extracted, clusters_condensed, clusters_entities_extracted,
            clusters_contextualized, clusters_insights_generated,
        )
        repository.set_clustering_rate(avg_seconds_per_unit)

    # ── Phase 1: cluster ──────────────────────────────────────────────
    # A day is only clustered once every article published on it (and
    # every earlier day) has a title-gate decision — clustering an
    # incomplete day would miss whatever's still sitting ungated, and
    # there'd be no clean way to fold a late-arriving article into a
    # cluster that's already been through extraction/condensing.
    earliest_incomplete = repository.get_earliest_incomplete_title_gate_date()
    by_day: Dict[str, list] = defaultdict(list)
    for article in clustering_candidates:
        date_key = article.published_at.date().isoformat()
        if earliest_incomplete is not None and date_key >= earliest_incomplete:
            continue
        by_day[date_key].append(article)
    days = sorted(by_day.keys())
    total_days = len(days)
    repository.set_clustering_phase_progress("clustering", total_days, 0, 0, 0, 0, 0, 0)

    phase_start = time.monotonic()
    for i, date_key in enumerate(days):
        if stop_requested():
            stopped = True
            break
        day_articles = by_day[date_key]
        try:
            existing_count = repository.count_clusters_for_date(date_key)
            assignments = _cluster_day(date_key, day_articles, existing_count)
        except Exception as e:
            error_count += 1
            errors.append(f"cluster {date_key} ({len(day_articles)} articles): {e}")
        else:
            repository.set_cluster_assignments(date_key, assignments)
            days_processed += 1
            articles_clustered += len(assignments)
        tick("clustering", total_days, i + 1)
    phase_durations["clustering"] = time.monotonic() - phase_start

    # ── Phase 2: extract ──────────────────────────────────────────────
    # Reuses extractor._extraction_lock so the per-article "retry
    # extraction" endpoint (extractor.extract_single_article) still
    # correctly detects a bulk extraction in progress and refuses to
    # launch a second, competing Chromium instance.
    phase_start = time.monotonic()
    if not stopped:
        extraction_candidates = repository.get_articles_pending_cluster_extraction()
        total_extraction = len(extraction_candidates)
        repository.set_clustering_phase_progress(
            "extraction", total_extraction, 0,
            days_processed, articles_extracted, clusters_condensed, clusters_entities_extracted,
            clusters_contextualized,
        )

        if extraction_candidates:
            if not extractor._extraction_lock.acquire(blocking=False):
                errors.append(
                    "extraction phase skipped this run: a single-article retry "
                    "extraction was in progress; will pick up next run"
                )
            else:
                try:
                    extractor.OUTPUT_DIR.mkdir(exist_ok=True)
                    with sync_playwright() as playwright:
                        browser = playwright.chromium.launch(headless=True)
                        context = browser.new_context(
                            user_agent=extractor.USER_AGENT, viewport={"width": 1280, "height": 800}
                        )
                        page = context.new_page()

                        for i, article in enumerate(extraction_candidates):
                            if stop_requested():
                                stopped = True
                                break
                            repository.set_ingestion_status(article.id, "Pending")
                            status, text = extractor._extract_one(page, str(article.url))
                            if status == "Successful":
                                repository.set_article_content(article.id, text, status)
                                filename = extractor._safe_filename(article.title) + ".txt"
                                (extractor.OUTPUT_DIR / filename).write_text(
                                    f"URL: {article.url}\nSTATUS: {status}\n\n{text}",
                                    encoding="utf-8",
                                )
                                articles_extracted += 1
                            else:
                                repository.set_ingestion_status(article.id, "Failed")
                                error_count += 1
                                errors.append(f"extract {article.title}: {text}")
                            tick("extraction", total_extraction, i + 1)

                        browser.close()
                finally:
                    repository.reset_pending_extraction_articles()
                    extractor._extraction_lock.release()
    phase_durations["extraction"] = time.monotonic() - phase_start

    # ── Phase 3: condense ─────────────────────────────────────────────
    phase_start = time.monotonic()
    if not stopped:
        condensing_candidates = repository.get_clusters_ready_for_condensing()
        total_condensing = len(condensing_candidates)
        repository.set_clustering_phase_progress(
            "condensing", total_condensing, 0,
            days_processed, articles_extracted, clusters_condensed, clusters_entities_extracted,
            clusters_contextualized,
        )

        for i, cluster in enumerate(condensing_candidates):
            if stop_requested():
                stopped = True
                break
            try:
                member_articles = repository.get_articles_by_ids(cluster.article_ids)
                titles = cluster_condenser.dedup_titles(
                    [(a.title, a.title_embedding) for a in member_articles]
                )
                members_with_content = [
                    (a.title, a.content, a.language) for a in member_articles
                    if a.ingestion_status == "Successful" and a.content
                ]
                condensed_chunks = (
                    cluster_condenser.condense_content(members_with_content) if members_with_content else []
                )
                # Every deduped chunk's embedding is persisted (not just
                # the ones selected below) — see CondensedChunk's
                # docstring for why the rest still matter later.
                vector_ids = repository.upsert_condensed_chunk_embeddings(
                    cluster.cluster_id, cluster.date, condensed_chunks
                )
                selected_chunks = [c.text for c in condensed_chunks if c.selected]
            except Exception as e:
                error_count += 1
                errors.append(f"condense {cluster.cluster_id}: {e}")
            else:
                repository.set_cluster_condensed(cluster.cluster_id, titles, selected_chunks, vector_ids)
                clusters_condensed += 1
            tick("condensing", total_condensing, i + 1)
    phase_durations["condensing"] = time.monotonic() - phase_start

    # ── Phase 4: entities ─────────────────────────────────────────────
    phase_start = time.monotonic()
    if not stopped:
        entity_candidates = repository.get_clusters_ready_for_entities()
        total_entities = len(entity_candidates)
        repository.set_clustering_phase_progress(
            "entities", total_entities, 0,
            days_processed, articles_extracted, clusters_condensed, clusters_entities_extracted,
            clusters_contextualized,
        )

        for i, cluster in enumerate(entity_candidates):
            if stop_requested():
                stopped = True
                break
            try:
                raw_entities = entity_extraction.extract_entities_from_chunks(cluster.chunks)
                canonical_entities = entity_extraction.canonicalize_entities(raw_entities, cluster.cluster_id)
            except Exception as e:
                error_count += 1
                errors.append(f"entities {cluster.cluster_id}: {e}")
            else:
                repository.set_cluster_entities(cluster.cluster_id, canonical_entities)
                clusters_entities_extracted += 1
            tick("entities", total_entities, i + 1)
    phase_durations["entities"] = time.monotonic() - phase_start

    # ── Phase 5: contextualize ────────────────────────────────────────
    phase_start = time.monotonic()
    if not stopped:
        contextualization_candidates = repository.get_clusters_ready_for_contextualization()
        total_contextualization = len(contextualization_candidates)
        repository.set_clustering_phase_progress(
            "contextualization", total_contextualization, 0,
            days_processed, articles_extracted, clusters_condensed, clusters_entities_extracted,
            clusters_contextualized,
        )

        for i, cluster in enumerate(contextualization_candidates):
            if stop_requested():
                stopped = True
                break
            try:
                entity_docs = repository.get_entities_by_names(cluster.entities)
                candidate_ids = set()
                for entity in entity_docs:
                    candidate_ids.update(entity.cluster_ids)
                candidate_ids.discard(cluster.cluster_id)
                candidate_clusters = repository.get_clusters_by_ids(list(candidate_ids))
                similar_clusters = contextualization.find_similar_clusters(cluster, candidate_clusters)
            except Exception as e:
                error_count += 1
                errors.append(f"contextualize {cluster.cluster_id}: {e}")
            else:
                repository.set_cluster_contextualized(
                    cluster.cluster_id, [s.model_dump() for s in similar_clusters]
                )
                clusters_contextualized += 1
            tick("contextualization", total_contextualization, i + 1)
    phase_durations["contextualization"] = time.monotonic() - phase_start

    # ── Phase 6: insights ─────────────────────────────────────────────
    phase_start = time.monotonic()
    if not stopped:
        insight_candidates = repository.get_clusters_ready_for_insights()
        total_insights = len(insight_candidates)
        repository.set_clustering_phase_progress(
            "insights", total_insights, 0,
            days_processed, articles_extracted, clusters_condensed, clusters_entities_extracted,
            clusters_contextualized, clusters_insights_generated,
        )

        if insight_candidates and not llm_insight.ollama_available():
            errors.append(
                "insights phase skipped this run: Ollama isn't reachable; "
                "will pick up next run"
            )
        else:
            for i, cluster in enumerate(insight_candidates):
                if stop_requested():
                    stopped = True
                    break
                try:
                    results = llm_insight.assess_cluster(
                        cluster.chunks, [s.model_dump() for s in cluster.similar_clusters]
                    )
                except Exception as e:
                    error_count += 1
                    errors.append(f"insights {cluster.cluster_id}: {e}")
                else:
                    repository.set_cluster_insights(cluster.cluster_id, results)
                    clusters_insights_generated += 1
                tick("insights", total_insights, i + 1)
    phase_durations["insights"] = time.monotonic() - phase_start

    total_successes = (
        days_processed + articles_extracted + clusters_condensed
        + clusters_entities_extracted + clusters_contextualized + clusters_insights_generated
    )
    if stopped:
        run_status = "stopped"
    elif error_count and total_successes == 0:
        run_status = "failed"
    elif error_count:
        run_status = "partial"
    else:
        run_status = "success"

    message = (
        f"Preprocessing {run_status}: {days_processed} day(s) clustered "
        f"({articles_clustered} articles), {articles_extracted} extracted, "
        f"{clusters_condensed} condensed, {clusters_entities_extracted} "
        f"entity-extracted, {clusters_contextualized} contextualized, "
        f"{clusters_insights_generated} insight-generated, "
        f"{error_count} error(s)."
    )

    repository.set_clustering_run_outcome(now, run_status)
    repository.log_clustering_run(
        status=run_status,
        message=message,
        days_processed=days_processed,
        articles_clustered=articles_clustered,
        articles_extracted=articles_extracted,
        clusters_condensed=clusters_condensed,
        clusters_entities_extracted=clusters_entities_extracted,
        errors=errors,
        duration_seconds=sum(phase_durations.values()),
        phase_durations=phase_durations,
        clusters_contextualized=clusters_contextualized,
        clusters_insights_generated=clusters_insights_generated,
    )

    return PreprocessingSummary(
        status=run_status,
        message=message,
        days_processed=days_processed,
        articles_clustered=articles_clustered,
        articles_extracted=articles_extracted,
        clusters_condensed=clusters_condensed,
        clusters_entities_extracted=clusters_entities_extracted,
        clusters_contextualized=clusters_contextualized,
        clusters_insights_generated=clusters_insights_generated,
        errors=errors,
    )


def is_preprocessing_running() -> bool:
    """Returns True if a preprocessing run is currently in progress, per
    the Mongo-backed flag — mirrors title_gate.is_title_gate_running()."""
    status = repository.get_clustering_status()
    return bool(status and status.in_progress)


def request_preprocessing_stop() -> bool:
    """Asks the current run to stop after the item it's working on right
    now finishes. Returns False if nothing is running to stop."""
    if not is_preprocessing_running():
        return False
    repository.set_clustering_stop_requested(True)
    return True


def run_preprocessing_job() -> None:
    """Background-task entrypoint — mirrors
    title_gate.run_title_gate_job() exactly: guards against overlapping
    runs with both the Mongo in_progress flag and the in-process lock,
    refuses to start alongside any other heavy stage, and always clears
    in_progress/stop_requested in a finally block whether the run
    succeeded, partially failed, was stopped, or raised."""
    if is_preprocessing_running():
        return
    if repository.get_running_pipeline_stage():
        return
    if not _preprocessing_lock.acquire(blocking=False):
        return

    repository.set_clustering_in_progress(True)
    try:
        summary = run_preprocessing()
        print(f"[preprocessing] {summary.message}")
    finally:
        repository.set_clustering_in_progress(False)
        repository.set_clustering_stop_requested(False)
        _preprocessing_lock.release()
