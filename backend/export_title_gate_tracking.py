"""
One-off export script (temporary, not part of the app) — dumps every
article gated under the new OR-ensemble title gate (services/title_gate.py,
added 2026-08-26: archived only if BOTH the LLM gate and the embedding
classifier say not-relevant) to a CSV of
{article_id, llm_gate_decision, model_decision, final_decision, agree}.

This is the ONGOING tracking export, for live production traffic gated
after the ensemble was deployed. For the one-off retrospective comparison
against articles gated before the ensemble existed (using the model
offline against the LLM gate's historical decisions), see
compare_model_vs_llm_gate.py instead.

Only includes articles gated since the ensemble was deployed --
title_gate_llm_decision is None for anything gated by the old code path.

Run inside the backend container:
    MSYS_NO_PATHCONV=1 docker cp backend/export_title_gate_tracking.py news-scraper-ymo-backend-1:/app/export_title_gate_tracking.py
    docker exec news-scraper-ymo-backend-1 python /app/export_title_gate_tracking.py
    MSYS_NO_PATHCONV=1 docker cp news-scraper-ymo-backend-1:/app/title_gate_tracking.csv backend/title_gate_tracking.csv
"""

import csv

from app.database import articles

OUTPUT_PATH = "/app/title_gate_tracking.csv"


def main():
    docs = list(
        articles.find(
            {"title_gate_llm_decision": {"$ne": None}},
            {
                "title": 1,
                "language": 1,
                "title_gate_llm_decision": 1,
                "title_gate_model_decision": 1,
                "ingestion_status": 1,
                "published_at": 1,
            },
        )
    )
    total = len(docs)
    print(f"{total} articles gated under the OR-ensemble so far", flush=True)
    if not docs:
        print("Nothing to export yet -- no article has been gated since the ensemble was deployed.", flush=True)
        return

    rows = []
    n_agree = 0
    for doc in docs:
        llm_decision = int(doc["title_gate_llm_decision"])
        model_decision = int(doc["title_gate_model_decision"])
        final_decision = 0 if doc.get("ingestion_status") == "Archived" else 1
        agree = int(llm_decision == model_decision)
        n_agree += agree
        rows.append({
            "article_id": str(doc["_id"]),
            "title": doc.get("title", ""),
            "language": doc.get("language") or "en",
            "llm_gate_decision": llm_decision,
            "model_decision": model_decision,
            "final_decision": final_decision,
            "agree": agree,
            "published_at": doc.get("published_at"),
        })

    with open(OUTPUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "article_id", "title", "language",
                "llm_gate_decision", "model_decision", "final_decision", "agree",
                "published_at",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nWrote {total} rows to {OUTPUT_PATH}", flush=True)
    print(f"  agreement: {n_agree}/{total} ({n_agree/total*100:.1f}%)", flush=True)


if __name__ == "__main__":
    main()
