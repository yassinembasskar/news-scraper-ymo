# Brainstorm Log — General Architecture (Round 1)

*Captures what we agreed on for the overall pipeline redesign, before moving to stage-by-stage detail. Read alongside [pipeline-analysis.md](pipeline-analysis.md), which this round builds directly on. Open items at the bottom are for the next rounds, not decided yet.*

## Objective function (restated, agreed)

Surface the news that actually requires a SAP parameter change in the supply chain — today the reference config names four parameters (`MARC-PLIFZ` transit delay, plus three more under the "Known gap" in the README/analysis doc that exist in the business-logic doc but are missing from the allowlist file). A user checks the app daily, sees a notification like "blockade in the Red Sea," and the system has already recommended which parameter to adjust and in which direction. Everything upstream of the report exists to get the right articles, and only the right articles, in front of that final recommendation step, as fast and as cheaply as possible.

## Measured context that shaped this round's decisions

- **Volume**: ~500 articles/day from the APIs, ~250 (50%) survive today's LLM title gate.
- **Aggregate time, today's pipeline, at measured per-article rates**:
  | Stage | Applies to | Aggregate time/day |
  |---|---|---|
  | Title gate (LLM) | all ~500 scraped | ~75 min |
  | Extraction | ~250 gate survivors | ~14 min |
  | Embedding | ~250 gate survivors | ~102 min |
  | Scoring (tree walk) | ~250 embedded | ~9 min |
  | Report (LLM) | only score ≥60 candidates (small N) | smallest in aggregate, despite being slowest per-article |

  **Embedding and title-gating are the two real time sinks (~3 hours/day combined)** — this is what justifies restructuring the pipeline around a cheap pre-filter that runs *before* extraction/embedding, rather than optimizing the report stage first.

## Agreed: the new general shape

```
Stage 0 (unchanged for now) — keyword scrape, 5 APIs → titles + dates
        ↓
Stage 1 — cheap relevance classification on titles (embedding-similarity based,
          NOT plain clustering — see decision below) → drop clearly-irrelevant,
          keep the rest as candidates
        ↓
Stage 2 — same-event clustering (per-day only) on the candidate titles
        → entity extraction (separate fixed list from keywords, for now)
        → cluster relevance score, from entity matches
        → sort clusters by score
        → extract full content ONLY for articles in high-scoring clusters
          (this is the big compute deferral — extraction+embedding no longer
          run on every gate-survivor, only on promising clusters)
        → one canonical representative title per cluster (summarized)
        → member articles keep their own individual extracted content —
          no content merge/collapse (see decision below)
        ↓
Stage 3 — cross-day continuity: for each high-scoring cluster, retrieve
          related prior coverage via metadata-filtered vector similarity
          (filter candidates by shared entities, rank by embedding similarity,
          weight by recency) — NOT combinatorial entity-subset matching
          (see decision below)
        ↓
Stage 4 — business insight generation per cluster (grounded in the cluster's
          own content + the retrieved prior-coverage context) → daily report
```

## Decisions made this round

1. **Stage 0 (keywords/ingestion) stays as-is.** Dynamic/adaptive keywords are explicitly deferred to a later round, not part of this redesign.

2. **The title-level filter is a relevance *classifier*, not unsupervised clustering.** Clustering groups similar titles together but doesn't itself produce a keep/drop label — a tight cluster of sports articles is still a cluster. The mechanism here is embedding-similarity against known-relevant reference points (exemplar articles, or a lightweight classifier), conceptually similar to what the existing relevance tree already does, just applied at title level before extraction instead of at full-content level after. Clustering is a *separate* step (Stage 2), for grouping same-event titles together, not for relevance filtering.

3. **A shared, hand-labeled gold set (~150-200 titles) must exist before/while swapping the gate mechanism.** The current LLM gate's ~82.6%-relevant number and this round's "50% survive" number are both real, measured facts about current behavior — but there's no labeled ground truth anywhere to compute precision/recall against. Before replacing the LLM gate with the embedding classifier, both must be scored against the same held-out labeled set so we know the swap is a quality improvement (or at least quality-neutral), not just a speed improvement bought with worse recall. A false negative at this stage permanently loses the article, which is the costliest failure mode in the whole pipeline — speed alone does not justify this swap.

4. **The extraction/embedding deferral is the single highest-priority change.** Confirmed by the volume math above: moving extraction+embedding to run only on high-scoring clusters (instead of all ~250 daily gate-survivors) is the biggest available time reduction in the entire pipeline, bigger than optimizing the gate mechanism itself or the report stage.

5. **Same-event clustering is scoped per-day only.** No persistent, cross-day clusters — that's a much harder problem (drift, merge/split logic, storage model) with no clear payoff here, since cross-day continuity is handled separately by Stage 3's retrieval mechanism instead.

6. **Entity extraction uses its own fixed list, kept separate from `keywords.py` for now.** Unifying the two is an explicit future option, not part of this round.

7. **Cluster relevance scoring will fully replace the existing relativity-tree scorer** (your call, overriding my initial recommendation to keep the tree as a fallback/pre-filter). **Agreed safeguard**: run the new entity/cluster scorer in shadow mode alongside the existing tree scorer against the gold-label set for a validation window before retiring the tree — the tree is cheap (2.2s/article) and is the only currently-calibrated mechanism in the pipeline, so there's no cost to keeping it as a comparison point until the replacement is proven, and real cost to a blind cutover. *(Flag this back if you'd rather cut over cleanly without the parallel-run step — noted as agreed-but-revisitable.)*

8. **No cluster-level content merging/collapsing.** A cluster gets one canonical, summarized *title* (low risk — near-duplicate headlines really do say the same thing). Each member article keeps its own individually-extracted content; downstream consumers (the report stage) cite specific source articles rather than reasoning over one blended, potentially self-contradictory merged text. Dedup happens at the presentation/reasoning layer, not by destroying per-source data.

9. **Cross-day "is this an escalation of something already known" continuity is a metadata-filtered vector retrieval, not combinatorial entity-subset matching.** The original entity-combination/powerset idea would be combinatorially explosive (a 10-entity cluster → 1023 subsets) and brittle (an exact-match miss on one entity loses all history). Instead: filter historical clusters/articles by "shares at least one entity with the current cluster," rank the filtered set by embedding similarity, weight by recency. Same outcome (the model sees "this is the 3rd escalation"), one well-understood mechanism, no combinatorics.

10. **Independent correctness fix, unrelated to this redesign but surfaced by it**: `llm_insight_business_logic.md` names three SAP parameters (`MARC-WEBAZ`, `MARC-SHZET`, `MARC-MRPPP`) as highly influential, but they're missing from `llm_insight_sap_attributes.md`'s allowlist, so the model can never actually select them. Fix this regardless of redesign timing — it's a one-line config change, not an architecture decision.

11. **Working principle for the whole initiative**: build evaluation/gold-label infrastructure before or alongside each mechanism swap; prefer incremental, measured replacement of one stage at a time over a simultaneous multi-stage rewrite. This is both lower-risk and a better demonstration of architecture discipline than a one-shot redesign.

## Open for stage-by-stage rounds (not decided yet)

- Exact embedding-classifier design for Stage 1 (what are the "known-relevant" reference points — hand-picked exemplars? centroid of past confirmed-relevant articles? reuse of existing tree node embeddings at title level?).
- Exact clustering algorithm for Stage 2 (HDBSCAN vs. threshold-agglomerative vs. something else; distance metric; on titles alone vs. titles+description).
- Entity extraction method (NER model vs. fixed-list substring matching, mirroring `keyword_matcher.py`'s current approach).
- Exact cluster-scoring formula from entity matches (mirrors the tree's calibration problem — needs the same gold-set discipline).
- Retrieval design details for Stage 3 (embedding model reuse vs. separate index; how far back "recency" looks; how many prior items to retrieve per cluster).
- Report-stage redesign to consume a cluster + retrieved context instead of one isolated article.
- How/when to build the gold-label set (who labels, how many, refreshed how often).
- The SAP allowlist fix (trivial, but sequencing it — now vs. as part of the report-stage rework).
