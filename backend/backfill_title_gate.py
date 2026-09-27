"""
One-off backfill script (temporary, not part of the app) — applies the
title-gate LLM pre-filter (app.services.llm_insight.gate_title) retroactively
to every already-scraped article that hasn't been embedded yet, archiving the
ones judged unable to affect the tracked business. New articles already get
this treatment at scrape time (see services/scraper.py); this just catches
the pre-existing backlog that was scraped before that feature existed.

Run inside the backend container (needs app.* imports + reachable Ollama):
    docker exec news-scraper-ymo-backend-1 python /app/backfill_title_gate.py
"""

import time

from app.database import articles
from app.services import llm_insight

def main():
    candidates = list(
        articles.find(
            {"embedding_status": "Not Embedded", "ingestion_status": {"$ne": "Archived"}},
            {"title": 1, "language": 1},
        )
    )
    total = len(candidates)
    print(f"{total} not-yet-embedded articles to gate", flush=True)

    archived = 0
    kept = 0
    errors = 0
    start = time.monotonic()
    for i, doc in enumerate(candidates, 1):
        title = doc.get("title", "")
        language = doc.get("language") or "en"
        try:
            relevant = llm_insight.gate_title(title, language)
        except Exception as e:
            errors += 1
            print(f"[{i}/{total}] ERROR: {e} :: {title[:70]}", flush=True)
            continue

        if relevant:
            kept += 1
            tag = "kept   "
        else:
            archived += 1
            articles.update_one({"_id": doc["_id"]}, {"$set": {"ingestion_status": "Archived"}})
            tag = "ARCHIVE"

        elapsed = time.monotonic() - start
        avg = elapsed / i
        eta_min = avg * (total - i) / 60
        print(
            f"[{i}/{total}] {tag} (archived={archived} kept={kept} errors={errors}, "
            f"~{eta_min:.0f}min left) :: {title[:70]}",
            flush=True,
        )

    print(f"DONE. total={total} archived={archived} kept={kept} errors={errors}", flush=True)

if __name__ == "__main__":
    main()
