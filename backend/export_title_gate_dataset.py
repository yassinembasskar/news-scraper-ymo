"""
One-off export script (temporary, not part of the app) — pulls every
article that's already been through the title gate
(title_gate_status == "Gated") into a CSV of {title, language, label},
for building and evaluating an embedding-based classifier as a candidate
replacement for the LLM gate (see docs/brainstorm-general-architecture.md).

label: 1 = the LLM gate kept it (relevant), 0 = the LLM gate archived it
(not relevant). This is the LLM gate's own output used as a reference —
not independently verified ground truth — so what this dataset measures is
agreement with the LLM gate, not absolute correctness. That's a deliberate,
acknowledged limitation for this first pass (see the brainstorm log).

Deliberately does NOT split into train/val/test here — that's an
experiment-design decision, not a data-export one, and belongs in the
notebook that actually runs the experiment (so the split can be reshuffled,
re-seeded, or re-ratio'd without ever touching Mongo again). This script's
only job is to get every labeled row out.

Run inside the backend container:
    MSYS_NO_PATHCONV=1 docker cp backend/export_title_gate_dataset.py news-scraper-ymo-backend-1:/app/export_title_gate_dataset.py
    docker exec news-scraper-ymo-backend-1 python /app/export_title_gate_dataset.py
    MSYS_NO_PATHCONV=1 docker cp news-scraper-ymo-backend-1:/app/title_gate_dataset.csv backend/title_gate_dataset.csv
"""

import csv

from app.database import articles

OUTPUT_PATH = "/app/title_gate_dataset.csv"


def _label_for(doc: dict) -> int:
    return 0 if doc.get("ingestion_status") == "Archived" else 1


def main():
    docs = list(
        articles.find(
            {"title_gate_status": "Gated"},
            {"title": 1, "language": 1, "ingestion_status": 1, "published_at": 1},
        )
    )
    total = len(docs)
    print(f"{total} gated articles found", flush=True)
    if not docs:
        print("Nothing to export.", flush=True)
        return

    rows = []
    n_relevant = 0
    for doc in docs:
        label = _label_for(doc)
        n_relevant += label
        rows.append({
            "article_id": str(doc["_id"]),
            "title": doc.get("title", ""),
            "language": doc.get("language") or "en",
            "label": label,
            "published_at": doc.get("published_at"),
        })

    with open(OUTPUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["article_id", "title", "language", "label", "published_at"]
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nWrote {len(rows)} rows to {OUTPUT_PATH}", flush=True)
    print(f"  relevant (1): {n_relevant}", flush=True)
    print(f"  archived (0): {total - n_relevant}", flush=True)


if __name__ == "__main__":
    main()
