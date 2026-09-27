"""
Embedding-based title-relevance classifier — the second half of the
title-gate OR-ensemble (see services/title_gate.py). Trained offline
against the LLM gate's own past decisions (research/title_gate_classifier.ipynb,
see docs/log.md's 2026-08-26 entry for the full writeup and metrics: 99.0%
recall / 71.1% precision on a held-out test set, optimized for recall since
a false negative here is the unrecoverable failure — the same philosophy
services/llm_insight.gate_title() already uses).

A nearest-centroid classifier: one reference vector for titles the LLM
gate judged relevant, one for titles it archived, both precomputed
(app/title_gate_centroids.npz, extracted from the trained model — see
backend/extract_centroids.py). Classifying a new title is just "which
centroid is it closer to" (cosine similarity) — no sklearn/pickle
dependency needed in production, just the embedding model (Qwen3-
Embedding-0.6B, the same one condensation/entity-canonicalization/
contextualization all reuse) plus two vectors.
"""

from functools import lru_cache
from pathlib import Path

import numpy as np

EMBEDDING_MODEL_NAME = "Qwen/Qwen3-Embedding-0.6B"
CENTROIDS_PATH = Path(__file__).resolve().parents[1] / "title_gate_centroids.npz"


@lru_cache(maxsize=1)
def _model():
    """Loaded once per process and reused for every title."""
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(EMBEDDING_MODEL_NAME)


@lru_cache(maxsize=1)
def _centroids():
    data = np.load(CENTROIDS_PATH)
    return data["centroid_relevant"], data["centroid_archived"]


def _normalize(v: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(v)
    return v if norm == 0.0 else v / norm


def embed_title(title: str) -> np.ndarray:
    """Returns the raw (unnormalized) title embedding. Split out from
    classify_embedding() so the caller (services/title_gate.py) can store
    this on the article in Mongo — it's the same vector used for the
    classification decision, so persisting it means never recomputing it
    later for analysis, retraining, or the entity/clustering work planned
    for the next pipeline stage."""
    embedding = _model().encode([title])[0]
    return np.asarray(embedding, dtype=np.float32)


def embed_texts(texts: list) -> np.ndarray:
    """Batched raw (unnormalized) embeddings for arbitrary text, not just
    titles — shared with services/cluster_condenser.py so the content-chunk
    dedup/diversity-selection step reuses this same already-loaded model
    instance instead of spinning up a second copy of it in-process."""
    embeddings = _model().encode(texts)
    return np.asarray(embeddings, dtype=np.float32)


def classify_embedding(embedding: np.ndarray) -> bool:
    """Returns True (relevant) or False (archived) for an already-computed
    title embedding."""
    v = _normalize(embedding)
    centroid_relevant, centroid_archived = _centroids()
    sim_relevant = float(v @ centroid_relevant)
    sim_archived = float(v @ centroid_archived)
    return sim_relevant > sim_archived


def classify_title(title: str) -> bool:
    """Convenience wrapper: embeds and classifies in one call. Raises on
    embedding failure — the caller decides how to handle that, same as
    llm_insight.gate_title(). Prefer embed_title() + classify_embedding()
    directly when the embedding also needs to be persisted, to avoid
    encoding the same title twice."""
    return classify_embedding(embed_title(title))
