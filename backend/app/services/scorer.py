"""
Relativity-scoring engine.

Pure function, no side effects: given an article's chunk_ids, fetches each
chunk's embedding + language from Pinecone, walks the relevance tree
(yazaki_tree_id_map.json) per chunk, and returns (final_score,
explanation_dict). No Mongo reads/writes here — wiring this into a
background job is a later step. Plain Python + numpy only, no LangChain.

THE ALGORITHM (see the task spec this was written against — reproduced
here so the code and its rationale stay next to each other):

  1. Per-chunk tree walk: for each chunk, walk the tree starting from
     every root. At each node, compute cosine similarity between the
     chunk's embedding and that node's embedding in the chunk's own
     language (never across languages). Below threshold -> prune this
     branch for this chunk, no hit recorded here. At/above threshold ->
     descend to children. A hit (node_id, similarity, is_leaf, chunk_id)
     is recorded at the point the chunk goes no further: either the node
     has no children (a true structural leaf), or none of its children
     cleared their own threshold.
  2. Group all hits, across every chunk, by node_id: max_similarity,
     is_leaf, supporting_chunks (distinct chunk_ids that hit it).
  3. path_score(node) = max_similarity(node)**SCORE_POWER * (LEAF_MULTIPLIER
     if is_leaf else 1.0) — no depth term. The **SCORE_POWER exponent is a
     calibration addition on top of the originally-specified linear
     formula (see the SCORE_POWER constant below for why).
  4. primary_score/primary_node = the triggered node with the highest
     path_score.
  5. breadth_bonus = BREADTH_BONUS_STEP * min(distinct triggered node
     count - 1, BREADTH_BONUS_MAX_EXTRA_NODES).
  6. deep_hit_count = distinct chunks (not hits) that reached a
     structural leaf in at least one branch. volume_bonus =
     min(VOLUME_BONUS_CAP, deep_hit_count * VOLUME_BONUS_PER_CHUNK).
     density_bonus = DENSITY_BONUS_WEIGHT * max(0, deep_hit_count/
     total_chunks - DENSITY_BONUS_FRACTION_THRESHOLD) /
     DENSITY_BONUS_FRACTION_THRESHOLD.
  7. final_score = round(min(1.0, primary_score + breadth_bonus +
     volume_bonus + density_bonus) * 100).
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
from pinecone import Pinecone

from app.config import settings

TREE_PATH = Path(__file__).resolve().parents[1] / "yazaki_tree_id_map.json"
# Must match repository.py's / embedder.py's PINECONE_INDEX_NAME — all three
# have to agree on which index they're reading/writing.
PINECONE_INDEX_NAME = "yazaki"

LEAF_MULTIPLIER = 1.1
BREADTH_BONUS_STEP = 0.02
BREADTH_BONUS_MAX_EXTRA_NODES = 3
VOLUME_BONUS_PER_CHUNK = 0.0025
VOLUME_BONUS_CAP = 0.05
DENSITY_BONUS_WEIGHT = 0.08
DENSITY_BONUS_FRACTION_THRESHOLD = 0.5
# Calibrated 2026-07-23 against a 50-article sample so the score
# distribution matches a target shape (majority <60, ~30% in 60-79, ~10%
# >=80, ~100 rare) instead of the ~95% saturating at 100 that the literal
# spec's linear combination produced with real corpus similarities
# (median triggered similarity ~0.5, p90 ~0.63). SCORE_POWER compresses
# mid-range similarities much more than high ones — a 0.5 similarity
# contributes far less than a 0.9 one, rather than scaling linearly — so
# only genuinely exceptional matches approach the ceiling. Also raised
# every tree node's threshold 0.2 -> 0.4 (see yazaki_tree_id_map.json)
# and shrunk the bonus constants above from their originally-specified
# values. This is a deliberate, requested deviation from the literal
# original spec, not a bug — see conversation history for the calibration
# data (backend/app/calibrate_scoring.py) behind these numbers.
SCORE_POWER = 1.8

# Human feedback ("more relative" / "less relative" buttons in the
# frontend): the base per-click nudge to a triggered node's threshold,
# scaled per-node by that node's share of the article's chunks (see
# apply_relativity_feedback). Clamped so a run of feedback clicks can't
# push a node to always-trigger (0.0) or never-trigger (1.0).
FEEDBACK_STEP = 0.02
FEEDBACK_THRESHOLD_MIN = 0.1
FEEDBACK_THRESHOLD_MAX = 0.9


@lru_cache(maxsize=1)
def _load_tree() -> dict:
    """127 nodes, keyed by node_id, each with parent/depth/labels/
    embeddings (per-language)/threshold. Cached — it's a static file."""
    with open(TREE_PATH, encoding="utf-8") as f:
        return json.load(f)


def get_active_thresholds(overrides: Optional[dict] = None) -> dict:
    """{node_id: effective_threshold} for every node — the static baseline
    from yazaki_tree_id_map.json, with any relativity-feedback override
    (from repository.get_node_threshold_overrides()) taking precedence.
    Callers fetch overrides from Mongo (this module stays Mongo-free) and
    pass them in here once per scoring run/re-score, not per node."""
    tree = _load_tree()
    overrides = overrides or {}
    return {node_id: overrides.get(node_id, node["threshold"]) for node_id, node in tree.items()}


def compute_relativity_feedback_updates(explanation: dict, direction: str, active_thresholds: dict) -> dict:
    """Pure — no Mongo/file I/O. User feedback on one article's score:
    computes a nudged threshold for every node that article's scoring run
    triggered. direction="more" means the article is more relevant than
    its score reflects, so triggered nodes should be *easier* to hit next
    time -> threshold decreases. direction="less" does the opposite.

    Each node's nudge is FEEDBACK_STEP scaled by that node's share of the
    article's chunks (supporting_chunks / total_chunks) — a node most of
    the article's content actually hit moves close to the full step, one
    only a single stray chunk grazed barely moves.

    active_thresholds is the current effective threshold per node (see
    get_active_thresholds) — the baseline this nudge moves from. Returns
    {node_id: new_threshold} for changed nodes only; caller persists via
    repository.set_node_threshold_overrides()."""
    sign = -1.0 if direction == "more" else 1.0
    total_chunks = explanation.get("total_chunks") or 0
    triggered_nodes = explanation.get("triggered_nodes") or []
    updates: dict = {}
    if total_chunks == 0:
        return updates

    for node in triggered_nodes:
        node_id = node["node_id"]
        if node_id not in active_thresholds:
            continue
        ratio = len(node.get("supporting_chunks", [])) / total_chunks
        delta = sign * FEEDBACK_STEP * ratio
        old_threshold = active_thresholds[node_id]
        new_threshold = min(FEEDBACK_THRESHOLD_MAX, max(FEEDBACK_THRESHOLD_MIN, old_threshold + delta))
        updates[node_id] = new_threshold

    return updates


@lru_cache(maxsize=1)
def get_tree_nodes_for_display() -> dict:
    """Same tree, stripped of the (large, write-only-for-scoring)
    per-language embedding vectors — just id/parent/depth/labels. For
    the frontend's article detail page to resolve a triggered node_id
    into a readable label and walk its root path, without needing the
    full multi-MB tree file or any numpy/similarity logic itself."""
    tree = _load_tree()
    return {
        node_id: {"parent": node.get("parent"), "depth": node.get("depth"), "labels": node.get("labels")}
        for node_id, node in tree.items()
    }


@lru_cache(maxsize=1)
def _build_children_index() -> dict:
    """node_id -> list of its direct children's node_ids, derived from
    each node's `parent` field (the tree file has no explicit children
    list)."""
    tree = _load_tree()
    children: dict = {node_id: [] for node_id in tree}
    for node_id, node in tree.items():
        parent = node.get("parent")
        if parent is not None:
            children[parent].append(node_id)
    return children


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0.0:
        return 0.0
    return float(np.dot(a, b) / denom)


def _walk(
    node_id: str,
    chunk_vector: np.ndarray,
    chunk_language: str,
    chunk_id: str,
    tree: dict,
    children_index: dict,
    thresholds: dict,
    hits: list,
) -> bool:
    """Returns True if this node cleared its own threshold (so the caller
    — its parent, or the top-level loop over roots — knows this branch
    produced something), regardless of what happened deeper."""
    node = tree[node_id]
    node_embedding = np.asarray(node["embeddings"][chunk_language], dtype=np.float32)
    similarity = _cosine_similarity(chunk_vector, node_embedding)

    if similarity < thresholds[node_id]:
        return False

    children = children_index.get(node_id, [])
    if not children:
        hits.append({"node_id": node_id, "similarity": similarity, "is_leaf": True, "chunk_id": chunk_id})
        return True

    any_child_cleared = False
    for child_id in children:
        if _walk(child_id, chunk_vector, chunk_language, chunk_id, tree, children_index, thresholds, hits):
            any_child_cleared = True

    if not any_child_cleared:
        hits.append({"node_id": node_id, "similarity": similarity, "is_leaf": False, "chunk_id": chunk_id})

    return True


def _fetch_chunks(chunk_ids: List[str]) -> dict:
    """Fetches each chunk's embedding + language from Pinecone. A
    chunk_id missing from the response (shouldn't normally happen, but
    Pinecone is a separate system from Mongo) is simply absent from the
    returned dict — the caller skips it rather than failing the whole
    article."""
    pc = Pinecone(api_key=settings.pinecone_api_key)
    index = pc.Index(PINECONE_INDEX_NAME)
    response = index.fetch(ids=chunk_ids)
    return {
        chunk_id: {
            "embedding": np.asarray(record.values, dtype=np.float32),
            "language": record.metadata["language"],
        }
        for chunk_id, record in response.vectors.items()
    }


def _empty_explanation(total_chunks: int) -> dict:
    return {
        "score": 0,
        "primary_node": None,
        "triggered_nodes": [],
        "breadth_count": 0,
        "breadth_bonus": 0.0,
        "total_chunks": total_chunks,
        "deep_hit_count": 0,
        "deep_hit_fraction": 0.0,
        "coverage_bonus": {"volume_bonus": 0.0, "density_bonus": 0.0, "total": 0.0},
    }


def score_article(chunk_ids: List[str], threshold_overrides: Optional[dict] = None) -> Tuple[int, dict]:
    """Scores one article's chunks against the relevance tree. Pure
    function — no Mongo access, no writes; threshold_overrides (if any)
    must be fetched by the caller via
    repository.get_node_threshold_overrides() and passed in — see
    get_active_thresholds(). Returns (final_score, explanation_dict);
    final_score is always an int 0-100 (an article with zero tree matches
    scores 0, same as one with zero chunks)."""
    tree = _load_tree()
    children_index = _build_children_index()
    thresholds = get_active_thresholds(threshold_overrides)
    roots = [node_id for node_id, node in tree.items() if node.get("parent") is None]

    total_chunks = len(chunk_ids)
    if total_chunks == 0:
        return 0, _empty_explanation(0)

    fetched = _fetch_chunks(chunk_ids)

    all_hits: list = []
    for chunk_id in chunk_ids:
        chunk = fetched.get(chunk_id)
        if chunk is None:
            continue
        for root_id in roots:
            _walk(root_id, chunk["embedding"], chunk["language"], chunk_id, tree, children_index, thresholds, all_hits)

    if not all_hits:
        return 0, _empty_explanation(total_chunks)

    # Step 2: group hits by node_id.
    grouped: dict = {}
    for hit in all_hits:
        group = grouped.setdefault(
            hit["node_id"],
            {"max_similarity": hit["similarity"], "is_leaf": hit["is_leaf"], "supporting_chunks": []},
        )
        group["max_similarity"] = max(group["max_similarity"], hit["similarity"])
        if hit["chunk_id"] not in group["supporting_chunks"]:
            group["supporting_chunks"].append(hit["chunk_id"])

    # Step 3-4: path scores, primary node.
    triggered_nodes = []
    for node_id, group in grouped.items():
        path_score = (group["max_similarity"] ** SCORE_POWER) * (LEAF_MULTIPLIER if group["is_leaf"] else 1.0)
        triggered_nodes.append({
            "node_id": node_id,
            "max_similarity": group["max_similarity"],
            "is_leaf": group["is_leaf"],
            "supporting_chunks": group["supporting_chunks"],
            "path_score": path_score,
        })

    primary = max(triggered_nodes, key=lambda n: n["path_score"])
    primary_score = primary["path_score"]

    # Step 5: breadth bonus.
    breadth_count = len(grouped)
    breadth_bonus = BREADTH_BONUS_STEP * min(breadth_count - 1, BREADTH_BONUS_MAX_EXTRA_NODES)

    # Step 6: coverage bonuses.
    deep_hit_chunk_ids = {hit["chunk_id"] for hit in all_hits if hit["is_leaf"]}
    deep_hit_count = len(deep_hit_chunk_ids)
    deep_hit_fraction = deep_hit_count / total_chunks

    volume_bonus = min(VOLUME_BONUS_CAP, deep_hit_count * VOLUME_BONUS_PER_CHUNK)
    density_bonus = (
        DENSITY_BONUS_WEIGHT
        * max(0.0, deep_hit_fraction - DENSITY_BONUS_FRACTION_THRESHOLD)
        / DENSITY_BONUS_FRACTION_THRESHOLD
    )

    # Step 7: final score.
    final_score = round(min(1.0, primary_score + breadth_bonus + volume_bonus + density_bonus) * 100)

    # Step 8: explanation object.
    explanation = {
        "score": final_score,
        "primary_node": {
            "node_id": primary["node_id"],
            "similarity": primary["max_similarity"],
            "is_leaf": primary["is_leaf"],
            "path_score": primary_score,
        },
        "triggered_nodes": [
            {
                "node_id": n["node_id"],
                "max_similarity": n["max_similarity"],
                "is_leaf": n["is_leaf"],
                "supporting_chunks": n["supporting_chunks"],
            }
            for n in triggered_nodes
        ],
        "breadth_count": breadth_count,
        "breadth_bonus": breadth_bonus,
        "total_chunks": total_chunks,
        "deep_hit_count": deep_hit_count,
        "deep_hit_fraction": deep_hit_fraction,
        "coverage_bonus": {
            "volume_bonus": volume_bonus,
            "density_bonus": density_bonus,
            "total": volume_bonus + density_bonus,
        },
    }

    return final_score, explanation
