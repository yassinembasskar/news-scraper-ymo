# Ingestion stage — design notes

Part of the RAG-pipeline redesign discussion (see sibling files in this folder for other stages). This file is a living record: current-state facts are code-grounded (file/line refs given so they can be re-verified as the code changes), decisions are dated, open items are carried forward until resolved.

## Current state (verified against code, 2026-08-19)

- Entry point: `backend/app/services/scraper.py` (`run_scrape()`), invoked via `POST /update` in `backend/app/routers/update.py`.
- Five source clients, each `fetch_articles(date_from, date_to) -> FetchResult`:
  `gnews_client.py`, `newsapi_org_client.py`, `newsdata_io_client.py`, `currents_client.py`, `thenewsapi_com_client.py`.
- All five do a keyword-OR search against `keywords.py`'s `KEYWORDS_BY_LANGUAGE` (81 EN / 80 FR / 72 AR terms), chunked to fit each API's query-length cap. Four of five restrict matching to the **title only** (`in:title` / `searchIn:title` / `qInTitle`); Currents does not.
- Per-source daily quotas: GNews 100 req/day, NewsAPI.org 100 req/day, NewsData.io 200 credits/day, Currents 600-1,000 req/day, TheNewsAPI.com 100 req/day (vendor-hardcapped at 3 articles/page regardless of requested limit).
- No global daily article cap exists — volume is an emergent property of the five sources' independent page/request limits, not a designed number.
- Dedup (`repository.upsert_article()`, `backend/app/repository.py:71-146`): URL dedup (Mongo upsert on `url`) plus a title-dedup layer for cross-source wire-story syndication (earliest-published wins; a comment notes one real wire story appeared under 16 distinct domains with an identical title).
- Exclusion (distinct from dedup): `keyword_matcher.is_excluded()` drops any article whose title contains a language-specific excluded word (`EXCLUDE_WORDS_BY_LANGUAGE` in `keywords.py`, e.g. en: `job`, `hiring`, `recruitment`, `vacancy`, `internship`). **This is ingestion-stage-only — no other stage in the pipeline has an equivalent negative/exclusion list.**
- Existing metrics: `ScrapeSummary` per run (`new_articles`, `duplicate_articles`, `excluded_articles`, `malformed_articles`, `archived_articles`, `errors`, `status`), persisted to the `scrape_runs` Mongo collection. Not currently exposed on any dashboard endpoint (only a single overwritten "last scrape" watermark is).
- Confirmed gaps: no per-source breakdown (articles aren't tagged with which of the 5 APIs found them), no API quota/rate-limit tracking, no measurement of ingestion recall (articles the keyword list never surfaces are invisible by construction).

## Decisions made in this discussion (2026-08-19)

- **The ingestion keyword list and the downstream entity dictionary (used for cluster-relevance scoring, see the clustering/entity-filter stage doc) will be unified into one curated list**, not two independent ones. An entity a human confirms as relevant during downstream review becomes a future ingestion keyword too, closing the loop between "discovered as relevant after the fact" and "actively searched for going forward."
- The ingestion stage's **exclusion list** (negative keywords) was flagged as a useful asset the rest of the pipeline currently lacks — worth considering whether an equivalent negative list would help later stages (e.g. the title gate), open for later discussion.
- **Ingestion-stage metrics and quality measurement are explicitly deferred** — user's call: for now, ingestion quality is considered acceptable, and the priority is the unified dynamic keyword/entity list; a dedicated metrics discussion (recall measurement, per-source breakdown, quota tracking) will happen later.

## Open items (carried forward, not yet resolved)

- How the unified keyword/entity list is actually curated day-to-day: same UX as the entity-dictionary human-in-the-loop review (select causing entity to promote/demote), applied here too? Not yet confirmed.
- Whether a newly-confirmed entity name translates cleanly into a good API search phrase (a discovered entity like a company name may need reformatting to work within each source's query syntax/character caps).
- Recall measurement (how many relevant articles are missed because the keyword list doesn't contain the right term, or because matching is title-only for 4/5 sources) — deferred, not designed.
- Per-source quality breakdown and API quota tracking — deferred, not designed (both are pre-existing README-flagged gaps, not new).

## Future updates

_(append dated entries here as this stage's design evolves)_
