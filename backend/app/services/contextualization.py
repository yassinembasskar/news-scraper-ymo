"""
Cluster-based contextualization — the fifth phase of preprocessing
(services/preprocessing.py), run after entity extraction on clusters
that have entities_extracted=True.

Links a cluster to the earlier clusters most likely to be a related past
event — metadata the later per-cluster insights stage uses to reason
about a story's history instead of judging it in isolation. SlimRAG-
inspired (docs/completion-plan.md), the same formula this project
already tried applying inside one cluster's own chunks and then reverted
("relevance condensing", 2026-09-14): (embedding similarity) x (entity
overlap count). That attempt failed because there was no real "query" to
compare a chunk against — here there is one: the cluster itself, compared
against real past clusters, so the same formula actually has something
to measure.

Candidates are found via the entity-to-cluster inverted index already
built by entity_extraction.canonicalize_entities (models.Entity.cluster_ids)
— union the cluster_ids of every real entity this cluster has, rather
than scanning the whole clusters collection. "Real" excludes NER
tokenization fragments (single/double-letter tokens, "##"-prefixed
wordpiece continuations — see MIN_ENTITY_NAME_CHARS), the same class of
bug relevance condensing found and fixed before that phase was reverted:
naive entity matching treats "K" or "AM" as meaningful, spuriously
linking unrelated clusters that merely happen to share a stray fragment.

Only clusters dated strictly before this one are eligible — "past"
context should mean past. A cluster whose real entities never appear in
any earlier cluster gets an empty similar_clusters list, which is a
legitimate outcome (a genuinely new story), not a failure; the insights
stage then reasons from this cluster's own chunks alone.
"""

from typing import List, Optional

import numpy as np
from pinecone import Pinecone

from app import repository
from app.config import settings
from app.models import Cluster, SimilarClusterRef

# Must match repository.py's PINECONE_INDEX_NAME — both have to agree on
# which index they're reading/writing.
PINECONE_INDEX_NAME = "yazaki"

# A canonical entity name shorter than this, or starting with "##" (a
# BERT wordpiece continuation marker), is excluded from entity-overlap
# matching — measured live (2026-09-14) on real data: garbage fragments
# like "K"/"AM"/"##wid" (bad tokenization of "Kwid") ended up as their
# own canonicalized entities, and would otherwise make two unrelated
# clusters look connected just for sharing a stray one-letter "entity".
MIN_ENTITY_NAME_CHARS = 3

# How many of the highest-scoring past clusters to keep — a natural
# top-N over an already-computed score, not a threshold gate that could
# misfire the way relevance condensing's safety cap once did.
TOP_K_SIMILAR_CLUSTERS = 10


def real_entity_names(entity_names: List[str]) -> List[str]:
    """Filters a cluster's raw `entities` list down to names usable for
    overlap matching — see MIN_ENTITY_NAME_CHARS."""
    return [n for n in entity_names if len(n) >= MIN_ENTITY_NAME_CHARS and not n.startswith("##")]


def _centroid(chunk_vector_ids: List[str], index) -> Optional[np.ndarray]:
    """Mean of a cluster's chunk embeddings, fetched from Pinecone by id
    (no re-embedding) and L2-normalized. None if the ids list is empty or
    nothing resolves (Pinecone is a separate system from Mongo, so a
    mismatch is handled rather than assumed impossible)."""
    if not chunk_vector_ids:
        return None
    response = index.fetch(ids=chunk_vector_ids)
    vectors = [np.asarray(record.values, dtype=np.float32) for record in response.vectors.values()]
    if not vectors:
        return None
    centroid = np.mean(vectors, axis=0)
    norm = np.linalg.norm(centroid)
    return centroid / norm if norm > 0 else centroid


def find_similar_clusters(cluster: Cluster, candidate_clusters: List[Cluster]) -> List[SimilarClusterRef]:
    """Scores `candidate_clusters` against `cluster` and returns the top
    TOP_K_SIMILAR_CLUSTERS, highest score first. `candidate_clusters` is
    expected to be a superset of what actually qualifies — typically
    every cluster resolved from this cluster's own entities' cluster_ids
    (preprocessing.py's job); this function does its own date and
    entity-overlap filtering, so passing in extras (including `cluster`
    itself) is safe. Returns [] immediately if this cluster has no real
    (non-fragment) entities at all, or if no candidate survives
    filtering — a legitimate "nothing similar yet" outcome, not an
    error."""
    this_entities = set(real_entity_names(cluster.entities))
    if not this_entities:
        return []

    eligible = [c for c in candidate_clusters if c.cluster_id != cluster.cluster_id and c.date < cluster.date]
    if not eligible:
        return []

    pc = Pinecone(api_key=settings.pinecone_api_key)
    index = pc.Index(PINECONE_INDEX_NAME)

    this_centroid = _centroid(cluster.chunk_vector_ids, index)
    if this_centroid is None:
        return []

    scored: List[SimilarClusterRef] = []
    for candidate in eligible:
        shared = sorted(this_entities & set(real_entity_names(candidate.entities)))
        if not shared:
            continue
        candidate_centroid = _centroid(candidate.chunk_vector_ids, index)
        if candidate_centroid is None:
            continue
        similarity = float(np.dot(this_centroid, candidate_centroid))
        score = similarity * len(shared)
        display = repository.get_cluster_display_info(candidate)
        scored.append(SimilarClusterRef(
            cluster_id=candidate.cluster_id,
            date=candidate.date,
            representative_title=display["representative_title"],
            score=score,
            shared_entities=shared,
        ))

    scored.sort(key=lambda s: -s.score)
    return scored[:TOP_K_SIMILAR_CLUSTERS]
