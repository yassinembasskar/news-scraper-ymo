"""
One-off apply script (temporary, not part of the app) — writes
model_vs_llm_gate_comparison.csv (produced by
research/compare_model_vs_llm_gate.ipynb on Colab) back onto the Mongo
articles it covers:
  - backfills title_gate_llm_decision / title_gate_model_decision on every
    article in the CSV (these predate the 2026-08-26 OR-ensemble
    deployment, so they were never recorded)
  - retroactively applies the same OR-ensemble rule the live pipeline now
    uses (services/title_gate.py): un-archives any article where the LLM
    gate said not-relevant but the model said relevant, since the
    ensemble rule keeps an article if EITHER mechanism says relevant.
    These un-archived articles re-enter the live pipeline (extraction ->
    embedding -> scoring -> reports) on the next run of each stage.

Also backfills title_embedding when the CSV has an "embedding" column
(added after the first run of research/compare_model_vs_llm_gate.ipynb,
which now exports each title's embedding alongside the two decisions) --
stored as a plain list of floats, same shape as what the live OR-ensemble
pipeline writes (see app/services/title_gate.py). Re-running this script
against an older CSV without that column simply skips the embedding
backfill (title_embedding stays unset), everything else unchanged.

Run inside the backend container:
    docker exec news-scraper-ymo-backend-1 pip install -q pandas
    MSYS_NO_PATHCONV=1 docker cp backend/model_vs_llm_gate_comparison.csv news-scraper-ymo-backend-1:/app/model_vs_llm_gate_comparison.csv
    MSYS_NO_PATHCONV=1 docker cp backend/apply_model_vs_llm_gate_comparison.py news-scraper-ymo-backend-1:/app/apply_model_vs_llm_gate_comparison.py
    docker exec news-scraper-ymo-backend-1 python /app/apply_model_vs_llm_gate_comparison.py
"""

import json

import pandas as pd
from bson import ObjectId

from app.database import articles

CSV_PATH = "/app/model_vs_llm_gate_comparison.csv"


def main():
    df = pd.read_csv(CSV_PATH)
    total = len(df)
    has_embeddings = "embedding" in df.columns
    print(f"{total} rows loaded from {CSV_PATH} (embeddings present: {has_embeddings})", flush=True)

    n_backfilled = 0
    n_embedded = 0
    n_unarchived = 0
    n_not_found = 0

    for _, row in df.iterrows():
        llm_decision = bool(row["llm_gate_decision"])
        model_decision = bool(row["model_decision"])
        update = {
            "title_gate_llm_decision": llm_decision,
            "title_gate_model_decision": model_decision,
        }
        if not llm_decision and model_decision:
            update["ingestion_status"] = "No Extraction"
            n_unarchived += 1

        if has_embeddings:
            update["title_embedding"] = json.loads(row["embedding"])
            n_embedded += 1

        result = articles.update_one({"_id": ObjectId(row["article_id"])}, {"$set": update})
        if result.matched_count:
            n_backfilled += 1
        else:
            n_not_found += 1

    print(f"\nBackfilled decisions on {n_backfilled}/{total} articles", flush=True)
    print(f"Backfilled title_embedding on {n_embedded}/{total} articles", flush=True)
    print(f"Un-archived (LLM said no, model said yes): {n_unarchived}", flush=True)
    if n_not_found:
        print(f"Not found in Mongo (skipped): {n_not_found}", flush=True)


if __name__ == "__main__":
    main()
