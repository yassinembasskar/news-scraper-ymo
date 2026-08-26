# Title gate stage — design notes

Part of the RAG-pipeline redesign discussion (see `01_ingestion.md` for the previous stage). Same format: code-grounded current state, dated decisions, open items carried forward.

## Current state (as of this discussion, pre-redesign)

- `gate_title()` in `backend/app/services/llm_insight.py` (lines 171-203): an Ollama LLM call (`qwen2.5:7b-instruct-q4_K_M`), title-only, `num_ctx=1024`, static English prompt for every language, no entity list.
- Deliberately loose ("biased to say yes when unsure") since a false negative here permanently archives the article before extraction.
- Its own docstring documents a prior failed design: feeding the LLM a keyword/entity list and instructing it to "keep anything with some relation to these keywords" made it too strict and caused real misses (e.g. "Thai Honda holds prices amid cost pressures," two Red Sea ship-sinking headlines) — fixed by dropping the anchor list and trusting open-ended LLM judgment (8/20 relevant caught vs. 3/20 with either entity-list version, on a real 20-article sample).
- Measured cold-load-contaminated timing was 30s-1min/article; warm CPU timing (back-to-back, after model already loaded) measured at **9s/article average** → 500 articles ≈ 75 min of CPU time, comfortably within the 6h window. GPU timing was never measured — turned out to be moot (see decision below).

## Decision (2026-08-19)

**Replacing the LLM gate with a nearest-centroid classifier over title embeddings**, using the same Qwen embedding model/vectors that the clustering stage needs anyway — making gate's marginal cost effectively just a distance-to-centroid computation once title embeddings exist, not a separate LLM call. This also moves title embedding earlier in the pipeline (now pre-gate, on all raw ~500 articles/day, rather than post-gate on the ~250 survivors) and removes gate from the GPU-hour budget entirely (no LLM call).

**Rationale offered**: title-only LLM strictness failure (documented above) was attributed to the *instruction* ("keep anything related to these keywords") rather than anchor-based classification being inherently flawed — worth noting for the record that a nearest-centroid classifier is still mechanically a proximity-to-reference-points decision rule, so this isn't a fully closed question, just a deliberate, examined tradeoff rather than an assumed one.

**Validation so far**:
- Model trained on 700 articles, validated on 350, tested on 350 (held-out).
- Against the *current LLM gate's own decisions as ground truth* (not independently human-verified): of 160 articles the LLM gate archived, 79 were let through by the new model (49.4% disagreement on the negative class); of 196 the LLM gate passed, the new model rejected 2 (~1% disagreement on the positive class).
- A second, larger held-out set (1500 datapoints, unseen by the model) was raised as a further check — results not yet supplied/discussed in this conversation.
- **Explicitly accepted, not resolved**: full manual review of the 79 disagreement cases was judged impractical; the tradeoff (favor recall/prevent data loss, accept more irrelevant leakage) was accepted as deliberate and consistent with the existing gate's own bias philosophy, not because the leaked articles were confirmed acceptable.

## Open items (carried forward, not yet resolved)

- Ground truth for the 79/2 disagreement counts is the old LLM gate's own judgment, not independent human labels — true precision/recall against real-world relevance is still unknown.
- No check yet against the specific known-tricky cases from the old gate's docstring (Thai Honda, Red Sea headlines) to confirm the new model handles them correctly.
- Train/val/test split methodology (by article vs. by story/date, to avoid near-duplicate leakage across splits) not confirmed.
- Whether the 700/350/350 label distribution reflects real-world class balance, or was deliberately balanced for training, not confirmed.
- A lightweight spot-check (e.g. 15-20 of the 79 leaked articles) was suggested as a cheap partial validation — not yet done, not a blocker, worth doing if downstream stages later show noise traceable to gate.
- The second 1500-datapoint held-out evaluation was mentioned but numbers not yet shared/discussed.

## Future updates

_(append dated entries here as this stage's design evolves)_
