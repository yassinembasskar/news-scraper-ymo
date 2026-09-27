# Pipeline Analysis — Where This System Stands, and the Gap to "Proper RAG"

*Working document for our brainstorming sessions. Reflects the repo as of 2026-08-25 (commit `6019832`, plus the uncommitted title-gate + dedup changes in the working tree). This is a snapshot, not a spec — re-read the code before trusting a specific number or file name against a later state.*

---

## 1. What this system currently is

Stripped of the domain dressing (Yazaki, SAP fields, ports), the pipeline is:

```
scrape (5 APIs) → keyword-match/dedupe → title-gate (LLM) → extract (headless browser)
  → chunk → embed (Qwen3-0.6B) → upsert to Pinecone
  → score (tree-similarity walk against a hand-built taxonomy)
  → human feedback (nudges tree thresholds)
  → daily report (translate + per-article LLM classification against a business-rules doc)
  → dashboard
```

It is **not**, today, a RAG system in the retrieval-augmented-generation sense. It's an **embedding-backed classifier pipeline**: the vector index is used to compute similarity against ~144 fixed taxonomy nodes, and the LLM stage classifies *one article at a time* against a static rules document — it never retrieves other chunks/articles as grounding context, and there's no query interface for a human or another system to ask a free-text question over the corpus. That's the central gap Section 7 works through. Everything before that section describes what exists today and where its time/quality costs actually sit.

---

## 2. Stage-by-stage breakdown

For each stage: what it does, the value it delivers, its cost, its quality risk, and what would move the needle — followed by how to actually evaluate it (most stages have **no repeatable evaluation today**, only one-off manual benchmarks quoted in the README).

### Stage 0 — Ingestion (scrape + keyword match + dedupe)

- **What**: 5 independent free news APIs (`services/*_client.py`), normalized to a shared `RawArticle` shape, queried per configured keyword/language. Title-only exclusion filter (`keyword_matcher.is_excluded`), keyword tagging, URL-based dedup on upsert, and (new, uncommitted) a title-based cross-source dedup fallback in `repository.upsert_article` for the same story reaching two different URLs.
- **Value**: broad multi-language coverage for near-zero API cost; incremental (single watermark) so steady-state runs are cheap.
- **Cost**: minutes per run, gated by each source's own rate limit — not a bottleneck.
- **Quality risk**: sources return only truncated previews, not full text (so keyword matching runs on a sliver of the article); ~9% raw duplicate rate observed pre-dedup; a single shared watermark across all 5 sources × 3 languages means one source's transient failure marks the *whole* run partial and re-fetches everything next time, not just the failed slice.
- **What's missing**:
  - Per-source, per-language watermarks instead of one global one — the single-watermark design is the reason a NewsData.io hiccup forces a full re-fetch of everything else too.
  - Near-duplicate detection beyond exact URL/title match — the known "8 cards for one Red Sea story" problem downstream starts here, not just at report time. A cheap embedding-similarity or MinHash pass across same-day articles would catch it earlier and cheaper than making the LLM report stage deduplicate.
  - No source-quota telemetry (README already flags this as a KPI gap) — right now you find out you hit a cap when a request errors, not before.
- **How to evaluate**: keep a per-run log of `(source, language) → {requested, returned, matched, excluded, deduped}` and watch the ratios drift over time; a source whose match rate craters is either exhausted (quota) or its keyword list has gone stale. Duplicate rate (`duplicate_articles / total_raw`) is already computed per run — trend it.

### Stage 1 — Title-gate pre-filter (LLM, cheap)

- **What**: one short (~1024-ctx) Ollama call per newly-scraped article, judging from the *title alone* whether it could plausibly matter, before the expensive stages run. Fails open on error; biased toward "keep."
- **Value**: the single highest-leverage cost-control lever in the pipeline — it prevents ~17% of ingested articles (per the README's 100-title benchmark) from ever reaching extraction/embedding/scoring/report, at ~9s/title instead of the ~24s (embedding) + ~2s (scoring) + ~30-60s (report, if it clears the score threshold) those stages would have cost.
- **Cost**: ~9s/article on CPU — real but far cheaper than what it prevents downstream.
- **Quality risk**: it's a **one-shot, irreversible-by-default** decision made on a title alone, evaluated once against a single 20-article ad hoc sample (documented in the `gate_title()` docstring, not a maintained eval set). A false negative here permanently removes an article from every downstream stage unless a human catches it on the Archived review page. There's no ongoing measurement of the false-negative rate; the docstring's "8/20 relevant" comparison was a design justification for a prompt choice, not a running metric.
- **What's missing**:
  - A persisted, growing gold-labeled title set (even 100-200 titles, hand-labeled once) to compute precision/recall on every prompt change instead of re-running an ad hoc sample and eyeballing it.
  - Visibility into the archive rate over time on the dashboard (README lists this as a KPI gap too) — a silent creep in the archive rate (e.g. from a keyword-list change bringing in a different title distribution) would go unnoticed.
- **How to evaluate**: (1) precision/recall against the gold set above whenever the prompt or model changes; (2) sample and human-audit ~5% of *archived* articles weekly — this is the cheapest possible check because false negatives are exactly the failure mode that matters most and the Archived page already exists for exactly this; (3) trend the archive rate — a step change is a signal something upstream (keyword list, source mix) shifted.

### Stage 2 — Extraction (Playwright + trafilatura)

- **What**: headless Chromium loads each article, `trafilatura` pulls main body text. `Failed` articles aren't auto-retried (deliberate). Manual paste-in exists as a human fallback.
- **Value**: the only source of full article text in the whole pipeline — the free-tier APIs give previews or nothing.
- **Cost**: ~3.4s/article measured (98% success on a 100-article re-extraction sample); heavier images/build time from bundling Chromium.
- **Quality risk**: paywalled/cookie-walled/unusual layouts fail permanently unless a human retries or pastes; no confidence signal on *how much* of the article's actual content trafilatura captured versus boilerplate it missed.
- **What's missing**:
  - Extraction success rate isn't surfaced as a trended metric (the README explicitly flags this as a trivial-to-add KPI still not built).
  - No structural signal (e.g. extracted-length vs. HTML size, or a trafilatura confidence score if available) to flag a "succeeded but probably garbage" extraction — this matters because everything downstream (embeddings, scoring, LLM assessment) silently trusts extracted content as ground truth.
- **How to evaluate**: success rate per run (trivial, already logged in `extraction_runs`, just not charted); periodic manual spot-check of a random extracted-content sample against the live page to catch systematic garbage (e.g. a layout change on a frequently-scraped domain).

### Stage 3 — Chunking

- **What**: line-break splitting with an overlapping-window fallback (`MAX_CHUNK_CHARS=2000`, `CHUNK_OVERLAP_CHARS=200`) for any single line that's too long.
- **Value**: cheap, no dependency, avoided a real production OOM (a 16,651-char single line blowing up self-attention memory) — the fallback exists because of a real incident, not speculatively.
- **Cost**: negligible (pure Python, no model calls).
- **Quality risk**: line-break splitting is a proxy for "paragraph," not "semantic unit" — a chunk can end mid-thought if the source HTML/extraction didn't paragraph-break cleanly, and there's no chunk-boundary awareness of sentence completeness for the *non-fallback* path (only the fallback path uses overlap). For a scoring-only downstream (current use) this barely matters; for a genuine RAG retrieval use case (Section 7) it matters more, because a retrieved chunk with a severed sentence is a worse citation.
- **What's missing**: no chunk-quality metric at all today (e.g. average chunk length, fraction of chunks under some minimum useful length, fraction that hit the overlap fallback path).
- **How to evaluate**: log chunk-count and chunk-length distribution per article at embedding time (cheap, already have the data); if/when semantic retrieval becomes a real use case, an offline check of "does a chunk boundary ever split the sentence containing a key entity" on a small sample is a fast signal not currently gathered anywhere.

### Stage 4 — Embedding (Qwen3-Embedding-0.6B → Pinecone)

- **What**: every chunk of a `Successful`-extraction article is embedded and upserted with metadata (`article_id`, `url`, `order`, `language`, raw chunk text).
- **Value**: this is the reusable asset — the embeddings exist independent of the current tree-scoring use case, which is exactly what makes real RAG (semantic search, related-article lookup, retrieval-grounded reports) a *data-already-there* problem, not a from-scratch one.
- **Cost**: ~24.4s/article average, ~1.11s/chunk (dominated by content length, not fixed overhead) — the second-most CPU-expensive stage after the LLM report.
- **Quality risk**: no re-embedding path if the embedding model ever changes (a version bump would silently produce an index mixing two embedding spaces unless everything is explicitly re-embedded); embedding failures retry automatically but a per-article 300s timeout kill means very long articles are systematically at risk of never completing.
- **What's missing**:
  - No embedding-model version tag on stored vectors/metadata — if you ever swap `Qwen/Qwen3-Embedding-0.6B` for something else, nothing stops half the index being in one embedding space and half in another.
  - No retrieval-quality check at all today, because nothing retrieves from the index yet outside the tree walk (see Section 7).
- **How to evaluate**: throughput/latency trend (already tracked via the EMA rate, just not persisted historically — a known KPI gap per the README); once retrieval exists, standard IR metrics (recall@k against a small hand-built query→relevant-article set) become the real signal — there's nothing to measure on this axis today because there's no retrieval to measure.

### Stage 5 — Relativity scoring (tree-similarity walk)

- **What**: per-chunk cosine similarity against a hand-built ~144-node taxonomy, combined via a hand-calibrated formula (`SCORE_POWER=1.8` plus breadth/volume/density bonuses) into a 0-100 score.
- **Value**: fast (~2.2s/article), fully explainable (every triggered node + similarity is stored and surfaced in the UI), and tunable without touching code (thresholds, taxonomy labels, business-logic doc are all data/config).
- **Cost**: cheap relative to everything else — not a bottleneck.
- **Quality risk**: explicitly acknowledged in the code and README as **heuristic, not learned** — `SCORE_POWER` and the bonus constants were hand-tuned to match a *target score distribution* on a 50-article sample, not validated against labeled ground truth on precision/recall. The feedback buttons nudge thresholds but there's no formal retraining loop, and feedback is global (not per-user), so conflicting human judgments on a shared deployment fight each other with no resolution mechanism.
- **What's missing**:
  - A held-out labeled set (even 50-100 articles hand-scored "relevant"/"not relevant"/"borderline" by a human) to compute actual precision/recall of the 0-100 score against a threshold, instead of eyeballing the score *distribution shape*.
  - Any mechanism to distinguish "this deployment's feedback signal" from "that deployment's feedback signal" if this is ever used by more than one team/person at once.
  - A relativity-score histogram on the dashboard (README already flags — the data exists, just isn't aggregated).
- **How to evaluate**: precision/recall @ threshold against a hand-labeled sample (this is the single highest-value evaluation addition in the whole non-LLM part of the pipeline, and currently doesn't exist at all); correlation between human feedback direction and score movement (does a "👎 Less" click on average measurably lower that article's *and similar future articles'* scores, or does it get drowned out by conflicting feedback); score-distribution drift over time as a cheap smoke test.

### Stage 6 — Human feedback loop

- **What**: 👍/👎 both nudge the specific article's score directly (±5, persisted) and nudge triggered nodes' thresholds proportionally to how much of the article supported each node (persisted as Mongo overrides, not written into the tree file).
- **Value**: a real, if narrow, human-in-the-loop correction mechanism — genuinely useful for catching a systematically miscalibrated node without an ML retraining pipeline.
- **Cost**: negligible (a Mongo write).
- **Quality risk**: global feedback with no per-user scoping (flagged above) is the main one; also, a single click's effect on *other* articles is invisible — there's no "here's what changed as a result of your feedback" surface, so a user can't tell if their correction actually generalized or got lost in noise.
- **What's missing**: an audit view of accumulated threshold overrides (which nodes have drifted furthest from baseline, and in which direction) — right now this state is fully invisible outside direct Mongo inspection.
- **How to evaluate**: track override magnitude per node over time; a node that's been nudged repeatedly in the same direction is a strong signal its *baseline* threshold (or its taxonomy label/embedding) is wrong, not that feedback needs to keep compensating for it forever.

### Stage 7 — Daily LLM insight report (translate + assess)

- **What**: for each day's candidates (`relativity_score ≥ 60`, not expired), translate non-English to English (cached), then one Ollama call per article against a business-logic Markdown doc + a strict SAP-attribute allowlist, schema-constrained output, temperature 0.2.
- **Value**: the actual "so what" output of the whole pipeline — turns a ranked article list into a structured, addressable recommendation. Local-only (no data leaves the machine) — genuinely valuable if source content or business rules are sensitive.
- **Cost**: this is **the dominant cost center of the entire system**. ~30-60s/article on CPU, one article at a time (batching was tried and made the model lazily skip reasoning), translation adds another full LLM call for non-English articles. A busy day can take 30+ minutes and blocks every other pipeline stage while it runs (shared single-stage guard, because it's CPU-heavy on the one Ollama instance shared with title-gating).
- **Quality risk**: capped by a 7B quantized model's judgment ceiling; `confidence` is **the model's own uncalibrated self-estimate**, explicitly documented as not a real probability; a known unresolved config bug exists today — `llm_insight_business_logic.md` tells the model to prefer `MARC-WEBAZ`/`MARC-SHZET`/`MARC-MRPPP` as the most influential parameters, but those three aren't in `llm_insight_sap_attributes.md`'s allowlist, so the model is structurally unable to select the parameters its own instructions call most important; per-article assessment with zero cross-article context means the same event covered by 8 outlets currently produces 8 separate, un-deduplicated cards.
- **What's missing** (this is where quality and time both concentrate, and where Section 7's RAG argument matters most):
  - **The SAP-attribute allowlist bug above** — a one-line config fix, but currently silently degrading every report's recommendations toward the four *less* important parameters.
  - **No confidence calibration** — the model's `confidence` field has never been checked against actual outcomes; either calibrate it (log confidence vs. a later human "was this actually right" judgment) or stop displaying it as if it were meaningful.
  - **No cross-article deduplication/clustering before the LLM stage** — merging near-duplicate stories *before* spending an LLM call on each one would cut both redundant compute and redundant cards in one move; this is a pure win sitting on top of the Stage-0 dedup gap above.
  - **No retrieval grounding** — each assessment reasons from one article's text and a static rules doc, with no ability to pull in *related* prior coverage (e.g. "is this the 3rd escalation of an ongoing situation, or the first sign of one") even though the embeddings needed to do that already exist in Pinecone from Stage 4. This is the single biggest quality lever available and it's currently unused — see Section 7.
  - **No batching/parallelism ceiling exploration** — per-article-serial was chosen because *4-article* batches broke reasoning; that doesn't rule out 2-article batches, or running two independent Ollama processes/models in parallel on separate CPU core sets if hardware allows — untested, not necessarily a dead end.
- **How to evaluate**: build a small (20-30 day, or 100-150 article) held-out set with a human-assigned "should this have been flagged, and was the recommended parameter/effect right" label; measure precision (of flagged insights, how many are real/actionable) and recall (of genuinely actionable articles in the candidate pool, how many got flagged); track confidence-vs-correctness to see if the self-reported number carries any signal at all; track report generation wall-clock per day as a plain operational metric (this one's cheap and already loggable from existing timestamps).

### Stage 8 — Presentation (React dashboard)

- **What**: chart/filter/article-list/detail-page/report-calendar UI, live progress+ETA on every background job, feedback buttons, Archived review page.
- **Value**: makes every other stage's output legible and actionable to a human without touching Mongo directly — this is a real, working product surface, not a demo scaffold.
- **Cost/quality risk**: not a pipeline bottleneck; the main gap is the KPI table the README itself already lays out (funnel success rates, score histogram, duplicate rate, report yield trend, API quota) — all computable from data already stored, none built yet.
- **How to evaluate**: this layer doesn't need its own quality metric — it needs the metrics from Stages 0-7 actually surfaced, which is a UI/aggregation task, not a new capability.

---

## 3. Time budget — where the clock actually goes

Per-article, on the reference deployment's CPU-only hardware (numbers are the README's own measured benchmarks, not estimates):

| Stage | Time/article | Share of total per fully-processed article |
|---|---|---|
| Title gate | ~9s | ~10% |
| Extraction | ~3.4s | ~4% |
| Embedding | ~24.4s | ~28% |
| Scoring | ~2.2s | ~2% |
| Report (if candidate, score ≥ 60) | ~30-60s (+translation if non-English) | ~35-55%+ |

**The LLM report stage is the dominant cost by a wide margin**, and it's also the stage gated to run last, alone, blocking everything else — so on a busy day, the *pipeline's* wall-clock latency is essentially "report generation time," not the sum of the other four stages. Any time-consumption fix that doesn't touch Stage 7 is optimizing the smaller half of the problem.

## 4. Quality budget — where correctness risk concentrates

| Stage | Failure mode if wrong | Currently caught by |
|---|---|---|
| Title gate | Article permanently lost (false negative, before anything else runs) | Nothing automatic — human audit of the Archived page only |
| Extraction | Garbage or empty content trusted as ground truth downstream | Nothing — only a binary success/fail, not a content-quality check |
| Scoring | Miscalibrated 0-100 score (heuristic, never validated against labels) | Feedback buttons, but global/unscoped and unmeasured in aggregate |
| Report | Wrong `concerned_param`/effect, structurally impossible for 3 named-important params, uncalibrated confidence | Nothing — no held-out eval set exists anywhere in the repo |

The pattern across every stage: **there is no gold-labeled evaluation set anywhere in this repository.** Every quality claim in the README ("~82.6% relevant," "target score distribution," "8/20 vs 3/20") is a one-off ad hoc sample used to justify a design decision at the time it was made, not a maintained benchmark that would catch regression when a prompt, model, or config file changes later. That's the single most valuable piece of infrastructure missing before any of the "make it faster/better" ideas can be measured rather than guessed at.

## 5. What's actually missing to become "a proper RAG system"

The pipeline embeds everything and does nothing retrieval-shaped with it. A genuine RAG loop needs, at minimum:

1. **A retrieval interface** — given a query (a user's free-text question, or a report-generation context like "what else do we know about this situation"), fetch the top-k semantically relevant chunks from Pinecone. This doesn't exist at all today — Pinecone is currently write-only from the app's perspective except for the tree-scoring read-back.
2. **Retrieval-grounded generation** — the LLM report stage currently reasons over exactly one article in isolation. Real RAG would let it pull in related prior coverage (via the retrieval interface above) before writing its assessment, so "this is the 3rd escalation of an ongoing port closure" becomes visible to the model instead of each mention being scored as an independent, context-free event.
3. **Citations** — a RAG answer's value is largely in being able to point back at what it's grounded in; today's report cards cite nothing beyond the one source article they came from.
4. **A user-facing query surface** — right now the only way to "ask the corpus a question" is to browse/filter the dashboard. A proper RAG system's headline capability is a question-answering interface over the whole archive, which requires 1-3 above as prerequisites but is itself new surface area (API + UI).
5. **Cross-article deduplication/clustering as a first-class step** — both a quality fix (fewer redundant report cards) and a RAG-enabler (clustering *is* a form of the "related coverage" grounding retrieval would otherwise have to do per-query).

None of this requires re-architecting what exists — the embeddings, the chunk metadata, the Mongo/Pinecone split are all already the right shape for it. It's additive work on top of a pipeline that currently stops one step short of being RAG rather than being a wrong foundation for it.

---

*Next: brainstorming phase — bring your ideas on which of the above to tackle first and how; the critique-and-counter-idea format applies from here.*
