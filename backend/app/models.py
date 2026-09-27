"""
Pydantic schemas for the things this backend persists:
  - articles (news articles, tagged with matched keywords)
  - the scrape-status and embedding-status documents in `meta`

These are pure data shapes — no Mongo-specific code here. Reading from and
writing to MongoDB (including converting to/from Mongo's `_id`) lives in
`repository.py`.
"""

from datetime import datetime, timezone
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field, HttpUrl

IngestionStatus = Literal["No Extraction", "Pending", "Successful", "Failed", "Archived"]
TitleGateStatus = Literal["Not Gated", "Gated"]
ClusterStatus = Literal["Not Clustered", "Clustered"]
class ArticleBase(BaseModel):
    """Fields common to every article, before it has a database id."""
    url: HttpUrl
    title: str
    description: Optional[str] = None
    source_name: Optional[str] = None
    published_at: datetime
    language: str
    matched_keywords: List[str] = Field(default_factory=list)
    # Whether this article's title has been through the LLM title-gate
    # pre-filter yet (services/title_gate.py) — separate from
    # ingestion_status because passing the gate doesn't change
    # ingestion_status at all (it just clears the article to proceed to
    # extraction as normal); only failing it does, by setting
    # ingestion_status to "Archived". Protected by upsert_article()'s
    # $setOnInsert like the other pipeline-stage fields, so a re-scrape of
    # an already-gated article never resets it back to "Not Gated".
    title_gate_status: TitleGateStatus = "Not Gated"
    # The two independent title-gate decisions this article actually got.
    # As of 2026-08-26, services/title_gate.py gates on the embedding
    # classifier alone (title_gate_llm_decision is None unless the LLM was
    # separately run for comparison — see compare_model_vs_llm_gate.py).
    # Kept side by side so the two mechanisms can be compared after the
    # fact (see export_title_gate_tracking.py) — both None until the
    # article has actually been gated.
    title_gate_llm_decision: Optional[bool] = None
    title_gate_model_decision: Optional[bool] = None
    # The title's own embedding (Qwen3-Embedding-0.6B, same model as the
    # content-chunk embeddings, 1024-dim), computed once by the title-gate
    # classifier and stored here so it never needs recomputing later — for
    # re-evaluating/retraining the classifier, and reused as-is by the
    # preprocessing/clustering stage below (no separate embedding step).
    title_embedding: Optional[List[float]] = None
    # Same-day same-story clustering (services/preprocessing.py) — groups
    # relevant articles' titles by story so downstream stages can act on a
    # cluster instead of every article independently. cluster_id is only
    # unique within its day ("YYYY-MM-DD-<label>"); a story with no
    # sibling articles is still its own singleton cluster, not left
    # unclustered. Both fields stay at their defaults until this article
    # has actually been through the clustering job.
    cluster_status: ClusterStatus = "Not Clustered"
    cluster_id: Optional[str] = None
    ingestion_status: IngestionStatus = "No Extraction"
    content: Optional[str] = None


class ArticleCreate(ArticleBase):
    """Shape the scraper builds before upserting into Mongo."""
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ArticleOut(ArticleBase):
    """Shape returned by the API — includes the Mongo-generated id."""
    id: str
    fetched_at: datetime


class ScrapeStatus(BaseModel):
    """The single `meta` document tracking incremental scrape state."""
    last_scraped: Optional[datetime] = None
    last_run_at: Optional[datetime] = None
    last_run_status: Optional[Literal["success", "partial", "failed"]] = None


class ExtractionRunStatus(BaseModel):
    """The single `meta` document (_id: "extraction_status") tracking the
    extraction job's state — identical shape to TitleGateRunStatus, same
    reasoning throughout (see that model's docstring/comments). Layered on
    top of (not replacing) extractor.py's in-process threading.Lock, which
    remains the hard guard against two Playwright/Chromium instances
    running in the same process — this document exists so progress/ETA
    can be polled and a run can be stopped cooperatively."""
    last_run_at: Optional[datetime] = None
    last_run_status: Optional[Literal["success", "partial", "failed", "stopped", "skipped"]] = None
    in_progress: bool = False
    total: int = 0
    processed: int = 0
    started_at: Optional[datetime] = None
    stop_requested: bool = False
    avg_seconds_per_article: Optional[float] = None


class TitleGateRunStatus(BaseModel):
    """The single `meta` document (_id: "title_gate_status") tracking the
    title-gate job's state — identical shape to ExtractionRunStatus, same
    reasoning throughout (see that model's docstring/comments). No
    per-article "Pending" state to reset on a crash (a title-gate call is a
    single synchronous request, not a multi-step process)."""
    last_run_at: Optional[datetime] = None
    last_run_status: Optional[Literal["success", "partial", "failed", "stopped", "skipped"]] = None
    in_progress: bool = False
    total: int = 0
    processed: int = 0
    started_at: Optional[datetime] = None
    stop_requested: bool = False
    avg_seconds_per_article: Optional[float] = None


class ClusteringRunStatus(BaseModel):
    """The single `meta` document (_id: "clustering_status") tracking the
    preprocessing job's state. The job has six phases (cluster -> extract
    -> condense -> extract entities -> contextualize -> generate insights,
    see services/preprocessing.py) — unlike the other stages, this status
    is phase-aware rather than one blended counter: `phase` names whichever
    phase is currently running (or just finished), and `phase_total`/
    `phase_processed` are scoped to that phase alone (reset to 0 at the
    start of each phase), so an ETA computed from them means something
    concrete instead of blending a near-instant clustering step with a
    slow LLM call.

    The `*_done` counters are cumulative across the whole run (not reset
    per phase) so the frontend can show every phase's progress at once —
    including phases that already finished earlier in this same run — not
    just whichever one is currently active.

    total/processed/avg_seconds_per_article are kept for backward
    compatibility with the generic stage-status shape other stages use
    (StageStatusResponse) — they mirror phase_total/phase_processed."""
    last_run_at: Optional[datetime] = None
    last_run_status: Optional[Literal["success", "partial", "failed", "stopped", "skipped"]] = None
    in_progress: bool = False
    total: int = 0
    processed: int = 0
    started_at: Optional[datetime] = None
    stop_requested: bool = False
    avg_seconds_per_article: Optional[float] = None
    phase: Optional[Literal["clustering", "extraction", "condensing", "entities", "contextualization", "insights"]] = None
    phase_total: int = 0
    phase_processed: int = 0
    days_done: int = 0
    articles_extracted_done: int = 0
    clusters_condensed_done: int = 0
    clusters_entities_done: int = 0
    clusters_contextualized_done: int = 0
    clusters_insights_done: int = 0


class SimilarClusterRef(BaseModel):
    """One past cluster linked to a cluster during contextualization
    (phase 5, services/contextualization.py). score = (cosine similarity
    between the two clusters' chunk-embedding centroids, fetched from
    Pinecone by chunk_vector_ids) x (count of shared real canonicalized
    entities — NER tokenization fragments like "K"/"AM"/"##wid" excluded,
    see contextualization.MIN_ENTITY_NAME_CHARS) — the same SlimRAG-style
    multiply this project already tried inside one cluster's own chunks
    (relevance condensing, reverted 2026-09-14) applied cluster-to-cluster
    instead, where there's an actual "query" (the cluster itself) to
    compare against real candidates rather than no query at all.
    representative_title is captured once at scoring time — a cluster's
    article_ids never change after it's first formed, so this can never
    go stale, unlike caching most other computed-on-read fields would
    risk (see repository.get_cluster_display_info)."""
    cluster_id: str
    date: str
    representative_title: str
    score: float
    shared_entities: List[str] = Field(default_factory=list)


class ClusterInsight(BaseModel):
    """One relevant insight the LLM Insight stage (phase 6, see
    services/preprocessing.py and services/llm_insight.assess_cluster)
    found in a cluster — the structured assessment of a single concrete
    operational event, plus which of the cluster's own `chunks` actually
    support it.

    chunk_indices point into this same cluster's `chunks` list (by
    position, 0-based) rather than repeating the chunk text or trying to
    re-match it back to the source later — an index is exact and cheap;
    re-finding a quoted sentence by fuzzy text search is neither. Indices
    are stable because `chunks` itself never changes after condensing
    (phase 3) produces it — condensing never re-runs once `condensed` is
    True, so a cluster's chunk list is fixed by the time insights (the
    last phase) ever sees it."""
    title: str
    confidence: int
    concerned_param: str
    effect: Literal["positive", "negative", "neutral"]
    recommended_action: str
    chunk_indices: List[int] = Field(default_factory=list)


class Cluster(BaseModel):
    """One same-day, same-story cluster (`clusters` collection, _id ==
    cluster_id, "YYYY-MM-DD-<label>" — see services/preprocessing.py).
    A cluster's member articles carry this same cluster_id on their own
    documents (models.ArticleBase.cluster_id); this document holds the
    cluster-level results that don't belong to any single article: the
    member list, and the condensed titles/chunks/entities produced once
    every member has been through extraction.

    titles/chunks are extractive, not an LLM-written summary (see
    services/cluster_condenser.py) — titles is the cluster's member
    titles deduplicated by embedding similarity; chunks is the subset of
    the deduped content chunks MMR actually selected (stopping once
    nothing left is different enough from what's already picked, not at
    a fixed count — see cluster_condenser.CONDENSE_MAX_CHUNKS's docstring
    for the safety cap that still applies), for topical coverage
    (relevant + diverse), not a proportional sample. Both stay empty and
    `condensed` stays False until every member article has reached a
    terminal ingestion_status (Successful/Failed) — a cluster with any
    member still pending extraction isn't condensed yet. `condensed` is
    explicit (rather than inferring "done" from chunks being non-empty)
    because a cluster where every member's extraction failed legitimately
    ends up with titles but zero chunks, and must not be re-processed
    forever.

    chunk_vector_ids covers more ground than `chunks` text does: it's
    every chunk that survived dedup (2026-09-11), not just the
    MMR-selected subset — including the ones MMR passed over because
    something more diverse won at that step, which are still genuinely
    non-redundant information, not noise. Their embeddings live in
    Pinecone (index "yazaki", metadata tagged mmr_selected so the two
    subsets stay distinguishable there); this is the id list
    services/contextualization.py fetches them by (2026-09-14) to build
    each cluster's content centroid for cross-cluster similarity."""
    cluster_id: str
    date: str  # "YYYY-MM-DD", the UTC day this cluster was formed on
    article_ids: List[str]
    titles: List[str] = Field(default_factory=list)
    chunks: List[str] = Field(default_factory=list)
    chunk_vector_ids: List[str] = Field(default_factory=list)
    condensed: bool = False
    entities: List[str] = Field(default_factory=list)
    # Explicit flag rather than inferring "done" from entities being
    # non-empty — a cluster where NER genuinely finds zero entities must
    # not be re-processed forever.
    entities_extracted: bool = False
    # Phase 5, contextualization (2026-09-14) — links this cluster to the
    # earlier clusters most likely to be a related past event (see
    # SimilarClusterRef and services/contextualization.py), the input the
    # later per-cluster insights stage uses alongside `chunks` to reason
    # about a story's history instead of judging it in isolation. Empty
    # when no earlier cluster shares any of this cluster's real
    # entities — a legitimate outcome (a genuinely new story), not a
    # failure; insights then proceeds straight from `chunks` alone.
    # `contextualized` is explicit for the same reason condensed/
    # entities_extracted are: a cluster with zero matches must not be
    # re-processed forever.
    similar_clusters: List[SimilarClusterRef] = Field(default_factory=list)
    contextualized: bool = False
    # Phase 6, LLM insights (see services/llm_insight.assess_cluster) — the
    # structured, per-event assessments this cluster's chunks (plus its
    # similar_clusters context) produced. Replaces the earlier per-article
    # daily-report flow (DailyReport/ReportInsight, retired the same day
    # this was added): one LLM pass per story instead of one per article
    # covering that story, and it can actually use similar_clusters, which
    # a per-article call never had access to. Empty is a legitimate
    # "nothing operationally relevant in this story" outcome, not a
    # failure — same "explicit done flag, not inferred from non-empty"
    # reasoning as condensed/entities_extracted/contextualized above.
    insights: List[ClusterInsight] = Field(default_factory=list)
    insights_generated: bool = False
    created_at: datetime


class Entity(BaseModel):
    """One canonical entity in the entity database (`entities` collection,
    Mongo _id == "{entity_group}:{canonical_name}") — accumulated across
    every cluster ever processed, for entity-based relativity scoring and
    cluster-based contextualization (a SlimRAG-style entity-to-cluster
    inverted index; see docs/completion-plan.md). A raw NER mention
    becomes an alias of whichever existing entity it's closest to by
    embedding similarity (services/entity_extraction.canonicalize_entities),
    or seeds a brand-new entity if nothing is close enough.

    entity_group is the NER model's type tag (PER/ORG/LOC/MISC) for the
    canonical form — matching only ever compares a new mention against
    entities of the same group, so a person and an organization that
    happen to share a surface form are never merged, and never collide
    on the composite _id either (the bare name alone isn't unique across
    groups, hence the "{group}:{name}" key rather than just canonical_name).

    entity_group == "TOPIC" is a second, separate kind of entry: the old
    hand-curated relevance tree (yazaki_tree_id_map.json, formerly
    services/scorer.py's per-node embedding-threshold walk), migrated in
    wholesale by migrate_tree_to_entities.py so there's one entity
    database instead of a JSON file plus a Mongo collection. TOPIC
    entities never participate in NER-mention canonicalization (a
    different, disjoint entity_group from PER/ORG/LOC/MISC, so they're
    never fetched as match candidates for a fresh NER mention) — they
    exist to be compared directly against cluster content by the
    entity-based relativity scoring this migration is a prerequisite
    for. parent/depth/threshold are populated only for TOPIC entities,
    preserving the tree's hierarchy and its calibrated per-node
    thresholds; embeddings holds the tree's original per-language
    vectors (available for a future language-aware match), while
    `embedding` still holds one single vector (English) so this shape
    stays compatible with the same-signature match every other entity
    already uses."""
    canonical_name: str
    entity_group: str
    aliases: List[str] = Field(default_factory=list)
    embedding: List[float]
    # SlimRAG-style entity-to-cluster inverted index — every cluster this
    # entity (under any alias) has appeared in.
    cluster_ids: List[str] = Field(default_factory=list)
    first_seen: datetime
    last_seen: datetime
    # TOPIC-only, from the migrated relevance tree — None for every
    # NER-canonicalized entity.
    parent: Optional[str] = None
    depth: Optional[int] = None
    threshold: Optional[float] = None
    embeddings: Optional[Dict[str, List[float]]] = None