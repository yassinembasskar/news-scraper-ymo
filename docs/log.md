# Project Log

Chronological record of production rounds — what was done, what was decided, and what's still open. Each entry closes out one production phase from the brainstorming/research/production/log cycle. See [pipeline-analysis.md](pipeline-analysis.md) for the original system diagnosis and [brainstorm-general-architecture.md](brainstorm-general-architecture.md) for the architecture decisions this work builds on.

---

## 2026-08-25 — Stage 1 (title gate): LLM → embedding classifier

**Context**: Round 1 of the general-architecture brainstorm identified the title gate and the embedding stage as the two dominant time costs in the pipeline (~75 min/day and ~102 min/day respectively, out of ~500 articles/day scraped). The agreed plan was to validate an embedding-based classifier against the LLM gate's own past decisions before considering a swap — see decisions #2–3 in the brainstorm log.

**What was done**:
1. Confirmed in code that scraping (`POST /update`) and title-gating (`POST /articles/title-gate`) are fully decoupled — a scraped article sits untouched until the title-gate job is triggered separately.
2. Ran the production title-gate job against the real backlog to build a labeled dataset from the LLM gate's own decisions: **1,423 articles gated (786 relevant / 637 archived)**.
3. Built `backend/export_title_gate_dataset.py` — exports every gated article (`title, language, label, published_at`) from Mongo, with **no pre-baked split**; splitting is left to whatever notebook/experiment consumes the data, not baked into the export.
4. Built `research/title_gate_classifier.ipynb` — embeds titles with the same `Qwen/Qwen3-Embedding-0.6B` model the production embedding stage already uses (same plain `.encode()` call, no new convention), trains three candidates (nearest-centroid, kNN, logistic regression) against the LLM gate's labels, evaluates precision/recall/F1 plus two failure counts framed for this use case (`leaked_through` = FP, `wrongly_archived` = FN — the costlier one, since it permanently loses an article), and times GPU vs. CPU inference.
5. Ran it in Colab (GPU). Split 711 train / 356 val / 356 test (stratified, seed 42). Selection rule (added by user): pick whichever classifier has the **best recall** on validation — deliberately mirrors the LLM gate's own "say yes when unsure" bias, since a false negative here is the unrecoverable failure mode.

**Known gap in this run**: a "4.1 Hyperparameter Tuning" section was added to the notebook claiming `GridSearchCV` was used to tune kNN and logistic regression, but no `GridSearchCV` code actually exists — kNN's `k=7` still comes from the original manual sweep over 7 fixed values, and logistic regression's hyperparameters are untouched defaults (`C=1.0`). Not yet fixed; the results below reflect this.

**Results**:

Validation set (197 relevant / 159 archived):

| Classifier | Accuracy | Precision | Recall | F1 | Leaked-through (FP) | Wrongly-archived (FN) |
|---|---|---|---|---|---|---|
| Nearest-centroid | 78.1% | 71.8% | **99.5%** | 83.4% | 77 | 1 |
| kNN (k=7) | 85.7% | 80.9% | 97.0% | 88.2% | 45 | 6 |
| Logistic Regression (untuned) | 88.5% | 89.4% | 89.8% | 89.6% | 21 | 20 |

Test set, nearest-centroid (selected by the recall rule above):
- Accuracy 77.2%, Precision 71.1%, **Recall 99.0%**, F1 82.7%
- 2 of 196 truly-relevant articles wrongly archived (out of 356 test rows)
- **79 of 160 archived-by-the-LLM articles (49%) leak through as "relevant"** — the direct cost of optimizing for recall

Timing (per title, title-only embedding + classification):
- GPU: 12.28 ms → "~733x" vs. the LLM gate's measured 9s/title — **not representative of production (CPU-only)**
- **CPU (the real number): ~1,861.5 ms embedding + ~0.004 ms classification ≈ ~5x speedup**, not 733x

**Decision**: replace the LLM title gate with the nearest-centroid classifier, for now — the near-elimination of wrongly-archived articles (2/196, vs. the LLM gate's own unmeasured false-negative rate) is judged worth more than the precision it gives up. Explicitly acknowledged trade-off: because precision dropped to 71%, roughly half of what the LLM gate would have archived now flows through to extraction/embedding instead, so the downstream compute savings from gating are meaningfully smaller than the classifier's raw speed suggests (~5x per-title speedup on the gate itself, not a ~5x reduction in total pipeline time).

**Not yet done** (explicitly out of scope for this round):
- The classifier is **not wired into `backend/app/services/title_gate.py`** — this was an offline experiment against exported data only. Production still runs the LLM gate today.
- Per the brainstorm log's agreed safeguard, before any real cutover this should run in **shadow mode** against the live LLM gate for a validation window.
- The labels used throughout are the LLM gate's own decisions ("silver standard"), not independently hand-labeled ground truth — the real gold-labeled set (docs/brainstorm-general-architecture.md, decision #3) still doesn't exist.
- The GridSearchCV gap above (logistic regression untuned, kNN not actually cross-validated) — if a properly tuned comparison is wanted, that code still needs writing.

**Artifacts**:
- `backend/title_gate_dataset.csv` — the exported labeled pool (1,423 rows, unsplit)
- `backend/title_gate_classifier.ipynb` — the executed notebook with all outputs
- `backend/title_gate_classifier/title_gate_classifier_nearest_centroid.pkl` — the saved model
- `backend/title_gate_classifier/embeddings_{train,val,test}.npy` — cached embeddings
