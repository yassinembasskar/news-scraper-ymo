# Project completion plan

Take the pipeline from "condensing exists" to "the system produces
cluster-level decisions over the full 2-month backlog, running daily on
Azure, with a report to show for it." Originally scoped at 11 days
(2026-09-11); this revision reflects real progress made since — several
items landed the same day they were planned, and one new stage
(relevance condensing) got added mid-flight once testing exposed a real
gap the original plan didn't anticipate.

## Done

- **Condensing → Pinecone persistence.** Every chunk that survives dedup
  (not just the MMR-selected subset) gets its embedding upserted into
  the `yazaki` Pinecone index, tagged `source: "condensation"` and
  `mmr_selected` in metadata — confirmed live against a real cluster
  (`2026-07-03-3`, 4 Renault Kwid articles). Resolves the open Pinecone
  question from the original plan: shared index, not a separate one.
- **MMR stopping rule.** Fixed `CONDENSE_MAX_CHUNKS = 100` cap replaced
  with a diversity-threshold stop (`MMR_NOVELTY_THRESHOLD`), tuned by
  hand against real dropped/kept chunks on that same cluster —
  0.92 (reusing the dedup threshold) selected 100% of everything, too
  loose; 0.80 cut real title-relevant facts (trim removals, cash-benefit
  pricing); **0.85 is the locked value**, separate from
  `CHUNK_DEDUP_THRESHOLD` (still 0.92, unchanged, for the earlier
  near-duplicate pass).
- **Entity canonicalization.** Embedding-similarity based (not
  LLM-based, deliberately — avoids reintroducing Ollama's 100-200s/call
  cost), scoped by NER type (PER/ORG/LOC/MISC) so a person and an
  organization sharing a name never merge. Verified live: "Groupe
  Renault" and "Renault SA" correctly merged into one `Renault` entity
  across two different clusters; different people with the same NER type
  correctly stayed separate.
- **Entity database.** New `entities` Mongo collection, one canonical
  entity per document, persisted embedding, entity-to-cluster inverted
  index (SlimRAG-style).
- **Old relevance tree migrated into the entity database.** All 155
  nodes from `yazaki_tree_id_map.json` (formerly `scorer.py`'s
  embedding-threshold walk) now live in `entities` as `entity_group ==
  "TOPIC"` — hierarchy (parent/depth), per-language embeddings, and each
  node's calibrated threshold all preserved, reusing the tree's own
  precomputed embeddings (zero re-embedding cost). Deliberately a
  disjoint group from NER-derived entities, so this migration is purely
  additive — it doesn't touch `canonicalize_entities()`'s matching at
  all. Idempotent migration script
  (`backend/migrate_tree_to_entities.py`), safe to re-run.

## Newly identified — not in the original plan

- **A 5th preprocessing phase: relevance condensing.** Testing MMR's
  output by hand surfaced a real limit: similarity (to the cluster
  centroid, or to the title) can tell you a chunk is *on-topic* or
  *non-redundant*, but not whether it's *beneficial* — a car's boot
  capacity and a CNG-kit price are both genuinely distinct, on-topic
  facts that add little for a business decision. That's a judgment about
  purpose, not topic, and no similarity threshold makes it. Rather than
  try to force MMR to solve this, a new phase runs after entity
  extraction (so it can use what phase 4 found) and keeps a chunk in a
  new `relevant_chunks` field only if it clears a real TOPIC entity's own
  calibrated threshold, or it textually mentions one of the cluster's own
  canonicalized entities. `chunks` itself is untouched — entity
  extraction already ran on the broader set, and this is a narrower,
  additional filter, not a replacement. Schema for this
  (`Cluster.relevant_chunks`, `relevance_condensed`, the phase-5 status
  fields) is drafted in `models.py` but the actual repository/
  orchestration/frontend wiring is not built yet — that's the next
  concrete task.
- Even this doesn't fully solve "beneficial vs. filler" — that's
  ultimately a reasoning judgment properly left to the LLM insights
  stage, which sees the full contextualized picture. Relevance
  condensing is a cheap, non-LLM pre-filter, not a final answer.

## Remaining work

### Finish relevance condensing (phase 5)
- Repository: fetch a cluster's selected-chunk embeddings back from
  Pinecone by id (no re-embedding — they're already there from
  condensing), fetch the 155 TOPIC entities once per run (not per
  cluster), the actual scoring function in `cluster_condenser.py`.
- Wire into `preprocessing.py` as phase 5, mirroring phases 1-4's
  progress/tick/stop pattern exactly.
- Frontend: 5th phase row in `PreprocessingStageCard`, `relevant_chunks`
  surfaced in `ClusterDetail`.

### Re-run the backlog under the new condensing logic
- Clusters condensed before the Pinecone/threshold rework (and before
  relevance condensing exists) need redoing — same reset-and-redo
  pattern already used once this session for a smaller batch.

### Azure deployment
- VM exists already, CPU-only (same hardware profile as local — no
  timing numbers change). Deploy scrape/title-gate/preprocessing so the
  backlog keeps grinding 24/7 instead of tying up the local machine.
  Each later stage deploys incrementally as it's finished.

### Entity-based relativity scoring
- Replaces `scorer.py`'s tree-similarity mechanism entirely. Now has
  more to work with than originally planned: both a cluster's
  NER-derived entities' track record *and* direct similarity against the
  migrated TOPIC entities are available as signals, not just one.
- Needs the canonicalization-threshold validation below before the
  entity track-record part of this can be trusted.

### Contextualization
- Time-based (a story's entities' timeline) and cluster-based (SlimRAG's
  mechanism at cluster granularity: entity index shortlists candidates,
  `(Pinecone similarity) × (entity overlap count)` ranks them) — this is
  what the Pinecone-persisted condensation embeddings actually get used
  for.

### Insights / decisions
- Reworks `llm_insight.py` / `report_job.py` to run per-cluster instead
  of per-article, fed condensed chunks + contextualization output.
  Approval loop: proposed decision → user approves/rejects → approved
  ones persisted, tied back to the source cluster, feeding future
  entity/contextualization signal.

### Full run + fixes
- End to end across the full 2-month backlog. Buffer for hardware
  surprises — the pattern all along (LLM summarization, embedding
  throughput, thread-count dead ends, the MMR threshold reuse bug).

### Report
- Architecture, the RAG-redesign decisions made along the way (title-gate
  model swap, non-LLM condensing, SlimRAG-style entity indexing,
  relevance condensing), the metrics below, and the actual insights the
  system produced from 2 months of articles.

## Metrics & evaluation (cross-cutting)

- **Title gate**: recall/precision (99.0% / 71.1% on the held-out set) —
  track real archive rate over the full backlog run.
- **Clustering**: F1 vs. hand-labeled pairs (already validated) — track
  cluster-size distribution and singleton rate.
- **Condensation**: measured directly on a real cluster —
  94 raw chunks → 90 after dedup (4.3%) → 65 after MMR at 0.85
  (a further 27.8%, ~31% total compression). Spot-check on more clusters
  once the backlog re-run happens.
- **Relevance condensing**: once built, spot-check that TOPIC-threshold
  hits and entity-mention hits are actually catching "beneficial", not
  just "on-topic" — the same kind of manual read that caught MMR's
  blind spot in the first place.
- **Entity canonicalization**: still needs a small hand-labeled
  validation set (same discipline as the clustering notebook) before
  0.85 is trusted at scale — spot-checks so far (Renault/Groupe Renault
  merging correctly, different same-type people staying separate) are
  promising but not systematic.
- **Entity-based relativity scoring**: correlation between a cluster's
  score and whether it later got an approved decision.
- **Contextualization**: qualitative — a manual review sample, no
  automatic ground truth exists for "is this the right historical link."
- **Insights/decisions**: approval rate — what fraction of LLM-proposed
  decisions actually get approved, tracked over time.
- **Whole pipeline**: end-to-end runtime and compute cost per cluster
  (tracked via the Logs page), plus final coverage — what fraction of
  the backlog produced an approved insight.

## Open decisions

1. Relevance condensing's exact scoring rule — TOPIC-threshold OR
   entity-mention (an OR-gate, matching this project's preference for
   simple/explainable rules over weighted composite scores) is the
   working assumption; not yet tested against real clusters the way the
   MMR threshold was.
2. Entity canonicalization threshold (0.85) — needs the same kind of
   small validation set as clustering before it's trusted at scale.
