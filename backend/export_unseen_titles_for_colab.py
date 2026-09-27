"""
One-off export script (temporary, not part of the app) — fast, no
embedding. Reproduces the notebook's train/val/test split (same seed/code
as research/title_gate_classifier.ipynb) to find every article the
classifier was NOT built on, then dumps those titles + the LLM gate's
actual decision to a CSV for the comparison to be finished on Colab's GPU
instead of this machine's CPU (see compare_model_vs_llm_gate.py, which
does the same comparison end-to-end but embeds locally on CPU — too slow
to wait on here).

"Unseen" = the original 356-row test split (already held out, never used
to fit the centroids) plus every article the LLM gate has gated since
title_gate_dataset.csv was exported.

Requires pandas + scikit-learn (installed ad hoc in the container, not in
requirements.txt) and title_gate_dataset.csv already copied into /app.

Run inside the backend container:
    docker exec news-scraper-ymo-backend-1 pip install -q pandas scikit-learn
    MSYS_NO_PATHCONV=1 docker cp backend/title_gate_dataset.csv news-scraper-ymo-backend-1:/app/title_gate_dataset.csv
    MSYS_NO_PATHCONV=1 docker cp backend/export_unseen_titles_for_colab.py news-scraper-ymo-backend-1:/app/export_unseen_titles_for_colab.py
    docker exec news-scraper-ymo-backend-1 python /app/export_unseen_titles_for_colab.py
    MSYS_NO_PATHCONV=1 docker cp news-scraper-ymo-backend-1:/app/unseen_titles_for_colab.csv backend/unseen_titles_for_colab.csv
"""

import pandas as pd
from sklearn.model_selection import train_test_split

from app.database import articles

DATASET_CSV = "/app/title_gate_dataset.csv"
OUTPUT_CSV = "/app/unseen_titles_for_colab.csv"

# Must exactly match research/title_gate_classifier.ipynb's split cell.
SPLIT_SEED = 42
VAL_FRACTION = 0.25
TEST_FRACTION = 0.25


def main():
    print("Reproducing the notebook's train/val/test split...", flush=True)
    df = pd.read_csv(DATASET_CSV)
    train_df, holdout_df = train_test_split(
        df, test_size=(VAL_FRACTION + TEST_FRACTION), stratify=df["label"], random_state=SPLIT_SEED
    )
    val_df, test_df = train_test_split(
        holdout_df,
        test_size=TEST_FRACTION / (VAL_FRACTION + TEST_FRACTION),
        stratify=holdout_df["label"],
        random_state=SPLIT_SEED,
    )
    seen_ids = set(train_df["article_id"]) | set(val_df["article_id"])
    print(f"  train={len(train_df)}  val={len(val_df)}  test={len(test_df)}  seen(train+val)={len(seen_ids)}", flush=True)

    print("\nFetching every currently-gated article from Mongo...", flush=True)
    all_gated = list(
        articles.find(
            {"title_gate_status": "Gated"},
            {"title": 1, "language": 1, "ingestion_status": 1, "published_at": 1},
        )
    )
    print(f"  total gated in Mongo now: {len(all_gated)}", flush=True)

    remained = [doc for doc in all_gated if str(doc["_id"]) not in seen_ids]
    print(f"  unseen by the model (test split + gated-since-export): {len(remained)}", flush=True)

    rows = [{
        "article_id": str(doc["_id"]),
        "title": doc.get("title", ""),
        "language": doc.get("language") or "en",
        "llm_gate_decision": 0 if doc.get("ingestion_status") == "Archived" else 1,
        "published_at": doc.get("published_at"),
    } for doc in remained]

    pd.DataFrame(rows).to_csv(OUTPUT_CSV, index=False)
    print(f"\nWrote {len(rows)} rows to {OUTPUT_CSV}", flush=True)


if __name__ == "__main__":
    main()
