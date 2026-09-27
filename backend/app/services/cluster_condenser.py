"""
Non-LLM cluster condensation — replaces the old LLM-generated abstractive
summary (llm_insight.summarize_cluster, kept in that module unused, same
"keep it for future reconsideration" treatment as the retired LLM title
gate in services/title_gate.py) as the third phase of preprocessing.

The LLM summary took ~100-200s per cluster (Ollama, CPU-only hardware) —
too slow to run over a large backlog. This module produces a cluster's
content representation from embeddings alone (the same Qwen3-Embedding
model already loaded for title-gating, via title_gate_model.embed_texts):

  1. Deduplicate member titles by embedding similarity (title_embedding is
     already stored per-article from title-gating, so no re-embedding
     needed here).
  2. Split every successfully-extracted member's content into sentence
     chunks (naive '.'-splitting — see _split_sentences for the known
     abbreviation caveat this accepts).
  3. Deduplicate those chunks by embedding similarity — same-story
     articles routinely repeat whole sentences of wire copy verbatim or
     near-verbatim.
  4. Select a subset of the surviving chunks via MMR (Maximal Marginal
     Relevance): greedily pick chunks that are close to the cluster's own
     content centroid (relevant) while staying far from chunks already
     picked (diverse), stopping once nothing left is different enough
     from what's already selected (2026-09-11) — not at a fixed count
     (CONDENSE_MAX_CHUNKS is now only a safety cap, see its docstring).

Result is extractive, not abstractive: `titles` and the MMR-selected
chunks are verbatim fragments, not a written-from-scratch narrative.
Entity extraction (phase 4) runs directly on the selected chunks.

Every chunk that survives dedup — not just the MMR-selected subset — is
still genuinely non-redundant information (MMR is a coverage/redundancy
heuristic, not a noise filter; a chunk it passes over lost narrowly to
something more diverse at that greedy step, it isn't garbage). So
condense_content() returns the full deduped set, each one flagged
selected=True/False, and preprocessing.py persists every one of their
embeddings to Pinecone (not just the selected ones) for cluster-based
contextualization to search over later — while Cluster.chunks (the
entity-extraction input) still only ever holds the selected text.
"""

from dataclasses import dataclass
from typing import List, Tuple

import numpy as np

from app.services import title_gate_model

# Cosine-similarity above this is treated as "the same sentence/title in
# different words" and dropped. Not empirically validated against a
# labeled set (unlike LINKAGE_THRESHOLD in preprocessing.py) — a
# reasonable default for near-duplicate wire-copy text, tunable later.
TITLE_DEDUP_THRESHOLD = 0.90
CHUNK_DEDUP_THRESHOLD = 0.92

# How similar the best remaining MMR candidate can be to something
# already selected before its being "too similar" stops selection
# (_mmr_select) — deliberately lower/looser than CHUNK_DEDUP_THRESHOLD,
# not the same value. Measured live (2026-09-11) on a real 4-article
# cluster: reusing 0.92 for both jobs caught only 4.3% as duplicates and
# then selected 100% of what survived (MMR never found anything "too
# similar" by that strict a bar) — four outlets rarely restate a fact in
# near-identical wording, so a near-identical-sentence threshold can't
# catch "same fact, reworded" redundancy. 0.85 is looser on purpose, to
# actually catch that case.
MMR_NOVELTY_THRESHOLD = 0.85

# A safety cap, not the primary stopping rule (see _mmr_select) — a
# pathological cluster of hundreds of genuinely diverse chunks (very
# large member articles) shouldn't select an unbounded number just
# because none of them ever look similar enough to trigger the novelty
# stop on their own.
CONDENSE_MAX_CHUNKS = 100

# Weight given to relevance (similarity to the cluster's content centroid)
# vs. diversity (distance from chunks already selected) in MMR. 0.5 weighs
# them equally; higher favors relevance, lower favors spread.
MMR_LAMBDA = 0.5

# A '.'-split fragment shorter than this is almost always an abbreviation
# artifact (e.g. "U.S" from "U.S. Steel") rather than a real sentence, so
# it's dropped rather than kept as a noisy chunk. This is a cheap guard,
# not a real sentence tokenizer — period-splitting was requested here
# specifically for sentence-level (not paragraph-level) granularity, so
# this length filter is the accepted trade-off instead.
MIN_CHUNK_CHARS = 30


@dataclass
class CondensedChunk:
    """One chunk that survived dedup. `selected` marks whether MMR also
    picked it as part of the cluster's displayed/entity-extraction text
    (Cluster.chunks) — every chunk here, selected or not, gets its
    embedding persisted to Pinecone (see preprocessing.py's phase 3)."""
    text: str
    language: str
    embedding: List[float]
    selected: bool


def _normalize_rows(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


def _dedup_by_embedding(texts: List[str], embeddings: np.ndarray, threshold: float) -> List[int]:
    """Greedy sequential dedup: keeps a text unless it's within `threshold`
    cosine similarity of one already kept. Returns the indices kept, in
    original order. O(n * kept) — fine at per-cluster scale."""
    normed = _normalize_rows(embeddings)
    kept_idx: List[int] = []
    for i in range(len(texts)):
        if not texts[i]:
            continue
        is_dup = any(float(normed[i] @ normed[j]) >= threshold for j in kept_idx)
        if not is_dup:
            kept_idx.append(i)
    return kept_idx


def _mmr_select(embeddings: np.ndarray, novelty_threshold: float, lambda_param: float = MMR_LAMBDA) -> List[int]:
    """Greedily selects chunks via Maximal Marginal Relevance against the
    set's own centroid — "relevant to the cluster's content overall, but
    not similar to what's already been picked" — stopping once the best
    remaining candidate is at least `novelty_threshold` similar to
    something already selected (nothing left is meaningfully different),
    rather than at a fixed count. CONDENSE_MAX_CHUNKS still applies as a
    safety cap on top of that."""
    n = len(embeddings)
    if n == 0:
        return []
    if n == 1:
        return [0]

    normed = _normalize_rows(embeddings)
    centroid = normed.mean(axis=0)
    centroid_norm = np.linalg.norm(centroid)
    if centroid_norm > 0:
        centroid = centroid / centroid_norm
    relevance = normed @ centroid

    selected = [int(np.argmax(relevance))]
    remaining = set(range(n)) - {selected[0]}

    while remaining and len(selected) < CONDENSE_MAX_CHUNKS:
        selected_matrix = normed[selected]
        best_idx, best_score, best_max_sim = None, -np.inf, 0.0
        for idx in remaining:
            max_sim_to_selected = float(np.max(selected_matrix @ normed[idx]))
            score = lambda_param * relevance[idx] - (1 - lambda_param) * max_sim_to_selected
            if score > best_score:
                best_score, best_idx, best_max_sim = score, idx, max_sim_to_selected

        if best_max_sim >= novelty_threshold:
            break  # nothing left adds meaningfully different information
        selected.append(best_idx)
        remaining.discard(best_idx)

    return selected


def _split_sentences(text: str) -> List[str]:
    """Naive '.'-based sentence split with a minimum-length filter — see
    MIN_CHUNK_CHARS for why. Collapses whitespace within each fragment."""
    fragments = (" ".join(part.split()) for part in text.split("."))
    return [f for f in fragments if len(f) >= MIN_CHUNK_CHARS]


def dedup_titles(titles_with_embeddings: List[Tuple[str, List[float]]]) -> List[str]:
    """Deduplicates a cluster's member titles by embedding similarity.
    Titles already have a stored embedding from title-gating
    (models.ArticleBase.title_embedding), so this never re-embeds
    anything. Order-preserving; a title with no stored embedding is kept
    as-is (nothing to compare it against) rather than dropped."""
    with_vec = [(t, e) for t, e in titles_with_embeddings if e]
    without_vec = [t for t, e in titles_with_embeddings if not e]

    if not with_vec:
        return without_vec

    texts = [t for t, _ in with_vec]
    embeddings = np.array([e for _, e in with_vec], dtype=np.float32)
    kept_idx = _dedup_by_embedding(texts, embeddings, TITLE_DEDUP_THRESHOLD)
    return [texts[i] for i in kept_idx] + without_vec


def condense_content(members: List[Tuple[str, str, str]]) -> List[CondensedChunk]:
    """Turns a cluster's successfully-extracted members' content into its
    full deduplicated set of sentence chunks, each flagged with whether
    MMR selected it. `members` is [(title, content, language), ...] —
    title isn't used here (see dedup_titles for that); each chunk split
    out of a member's content inherits that member's language, needed
    for the Pinecone metadata this feeds. Returns [] if no member has any
    usable content after splitting (e.g. every content field was empty)."""
    all_chunks: List[str] = []
    chunk_languages: List[str] = []
    for _, content, language in members:
        for chunk in _split_sentences(content or ""):
            all_chunks.append(chunk)
            chunk_languages.append(language)

    if not all_chunks:
        return []

    embeddings = title_gate_model.embed_texts(all_chunks)
    dedup_idx = _dedup_by_embedding(all_chunks, embeddings, CHUNK_DEDUP_THRESHOLD)
    deduped_chunks = [all_chunks[i] for i in dedup_idx]
    deduped_languages = [chunk_languages[i] for i in dedup_idx]
    deduped_embeddings = embeddings[dedup_idx]

    selected_idx = set(_mmr_select(deduped_embeddings, MMR_NOVELTY_THRESHOLD))

    return [
        CondensedChunk(
            text=text,
            language=language,
            embedding=embedding.tolist(),
            selected=(i in selected_idx),
        )
        for i, (text, language, embedding) in enumerate(zip(deduped_chunks, deduped_languages, deduped_embeddings))
    ]
