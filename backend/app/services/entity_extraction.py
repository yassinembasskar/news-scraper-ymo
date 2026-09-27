"""
Named-entity extraction — the fourth phase of preprocessing
(services/preprocessing.py). Runs a multilingual transformer NER model
over a cluster's condensed content chunks (services/cluster_condenser.py's
extractive output, not the raw titles — see preprocessing.py's docstring),
then canonicalizes the raw mentions found into the persistent entity
database (models.Entity, the `entities` collection) so the same
real-world entity is tracked as one thing across every cluster it ever
appears in, not once per surface-form variant.

Model: a multilingual NER fine-tune (Davlan/bert-base-multilingual-cased-ner-hrl)
covering English, French, and Arabic among its supported languages —
chosen specifically to avoid adding a second ML framework (e.g. spaCy) on
top of the transformers/sentence-transformers stack this app already
depends on for embeddings; downloaded from HuggingFace on first use, same
as the embedding model (see the README's note on corporate-network SSL
inspection blocking that download, if it ever comes up here too).

Canonicalization (2026-09-11, see docs/completion-plan.md) is embedding-
similarity-based, not LLM-based — an LLM alias-resolution step (the
approach the SlimRAG paper this was inspired by actually uses) would cost
another 100-200s-per-call Ollama round trip per distinct entity mention,
reintroducing exactly the kind of slowness cluster_condenser.py's
non-LLM rewrite just eliminated from the condensing phase. Reuses the
same Qwen3-Embedding model already loaded for title-gating/condensing
(title_gate_model.embed_texts) instead.
"""

from datetime import datetime, timezone
from functools import lru_cache
from typing import List, Tuple

import numpy as np

from app import repository
from app.models import Entity
from app.services import title_gate_model

NER_MODEL_NAME = "Davlan/bert-base-multilingual-cased-ner-hrl"

# Cosine-similarity above this collapses a newly-seen entity mention into
# an existing canonical entity as an alias, rather than minting a new
# one. Not empirically validated yet — a starting point in the same
# spirit as cluster_condenser.py's dedup thresholds (TITLE_DEDUP_THRESHOLD/
# CHUNK_DEDUP_THRESHOLD), to be tuned against a small hand-labeled set
# once there's real entity-history data (see docs/completion-plan.md's
# open items — the same discipline the clustering method study used).
CANONICALIZATION_THRESHOLD = 0.85


@lru_cache(maxsize=1)
def _pipeline():
    from transformers import pipeline
    return pipeline("ner", model=NER_MODEL_NAME, aggregation_strategy="simple")


def _normalize(v: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(v)
    return v if norm == 0.0 else v / norm


def extract_entities(text: str) -> List[str]:
    """Returns the distinct entity strings found in `text`, in the order
    first seen. Raises on model/pipeline failure — the caller decides how
    to handle that, same as every other per-item step in this pipeline.
    Not canonicalized — a standalone convenience function, unlike
    extract_entities_from_chunks() below (the one preprocessing.py
    actually calls)."""
    if not text or not text.strip():
        return []

    results = _pipeline()(text)
    seen: List[str] = []
    for entity in results:
        word = entity.get("word", "").strip()
        if word and word not in seen:
            seen.append(word)
    return seen


def extract_entities_from_chunks(chunks: List[str]) -> List[Tuple[str, str]]:
    """Returns the distinct (word, entity_group) pairs found across every
    chunk, merged in the order first seen. entity_group is the NER
    model's type tag (PER/ORG/LOC/MISC) — kept alongside the word so
    canonicalize_entities() below never merges two different-typed
    entities that happen to share a surface form (a person and a company
    both named "Ford", say). A cluster's condensed chunks can number up
    to cluster_condenser.CONDENSE_MAX_CHUNKS, so this runs one batched
    pipeline call over all of them (the HF pipeline accepts a list and
    returns one result list per input) rather than one call per chunk —
    far fewer Python-level round trips than looping per chunk."""
    non_empty = [c for c in chunks if c and c.strip()]
    if not non_empty:
        return []

    per_chunk_results = _pipeline()(non_empty)
    seen: List[Tuple[str, str]] = []
    seen_set = set()
    for chunk_entities in per_chunk_results:
        for entity in chunk_entities:
            word = entity.get("word", "").strip()
            group = entity.get("entity_group", "MISC")
            key = (word, group)
            if word and key not in seen_set:
                seen_set.add(key)
                seen.append(key)
    return seen


def canonicalize_entities(raw_entities: List[Tuple[str, str]], cluster_id: str) -> List[str]:
    """Collapses a cluster's raw (word, entity_group) NER mentions into
    canonical entities in the persistent entity database, merging
    aliases by embedding similarity within the same entity_group —
    "Renault" and "Groupe Renault" (both ORG) collapse into one entity; a
    person and an organization sharing a name never merge, since they're
    only ever compared against candidates of their own group.

    For each raw mention: embed it, compare against every existing
    entity of the same group already known this call (an in-memory cache
    per group, refreshed with newly-created ones as we go, rather than a
    fresh Mongo round trip per mention — see repository.get_entities_by_group
    for why this is a full scan, not an ANN lookup, and the plan for
    revisiting that). Above CANONICALIZATION_THRESHOLD, merge as an alias
    of the closest match; otherwise mint a new canonical entity seeded by
    this mention. Either way, this cluster's id lands on the entity (a
    SlimRAG-style entity-to-cluster inverted index) — idempotent, calling
    this twice for the same cluster never duplicates the id.

    Returns the cluster's distinct canonical entity names, in the order
    their first mention was processed — this is what preprocessing.py
    stores on Cluster.entities, not the raw mentions."""
    if not raw_entities:
        return []

    words = [word for word, _ in raw_entities]
    embeddings = title_gate_model.embed_texts(words)

    groups_seen = {group for _, group in raw_entities}
    candidates_by_group = {g: list(repository.get_entities_by_group(g)) for g in groups_seen}

    canonical_names: List[str] = []
    for (word, group), raw_embedding in zip(raw_entities, embeddings):
        v = _normalize(raw_embedding)

        best_name, best_sim = None, -1.0
        for entity in candidates_by_group[group]:
            sim = float(v @ _normalize(np.asarray(entity.embedding, dtype=np.float32)))
            if sim > best_sim:
                best_sim, best_name = sim, entity.canonical_name

        if best_name is not None and best_sim >= CANONICALIZATION_THRESHOLD:
            repository.merge_entity_alias(best_name, group, word, cluster_id)
            canonical_name = best_name
        else:
            repository.create_entity(word, group, raw_embedding.tolist(), cluster_id)
            canonical_name = word
            # So a later mention in this same cluster/group (or an exact
            # repeat of this one) matches against it immediately, without
            # waiting for a fresh Mongo round trip to see it.
            now = datetime.now(timezone.utc)
            candidates_by_group[group].append(
                Entity(
                    canonical_name=word,
                    entity_group=group,
                    embedding=raw_embedding.tolist(),
                    first_seen=now,
                    last_seen=now,
                )
            )

        if canonical_name not in canonical_names:
            canonical_names.append(canonical_name)

    return canonical_names
