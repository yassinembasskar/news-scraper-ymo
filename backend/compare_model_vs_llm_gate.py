"""
One-off comparison script (temporary, not part of the app) — compares the
nearest-centroid title-gate classifier (research/title_gate_classifier.ipynb)
against the LLM gate's actual decisions, on every article the model has
NEVER seen: i.e. the full currently-gated pool, minus whatever article_ids
were in the train+val split used to build/tune the model (reproduced here
with the exact same split code/seed the notebook used, so "unseen" is
correct). The original test split (already held out) plus every article
the LLM gate has gated since the CSV was exported both count as "unseen" —
this is a strictly bigger, still-uncontaminated comparison set than the
notebook's own 356-row test set.

Requires pandas + scikit-learn (installed ad hoc in the container — not in
requirements.txt, this is a one-off analysis script) and title_gate_dataset.csv
+ title_gate_classifier_nearest_centroid.pkl already copied into /app.

Run inside the backend container:
    docker exec news-scraper-ymo-backend-1 pip install -q pandas scikit-learn
    MSYS_NO_PATHCONV=1 docker cp backend/title_gate_dataset.csv news-scraper-ymo-backend-1:/app/title_gate_dataset.csv
    MSYS_NO_PATHCONV=1 docker cp backend/title_gate_classifier/title_gate_classifier_nearest_centroid.pkl news-scraper-ymo-backend-1:/app/title_gate_classifier_nearest_centroid.pkl
    MSYS_NO_PATHCONV=1 docker cp backend/compare_model_vs_llm_gate.py news-scraper-ymo-backend-1:/app/compare_model_vs_llm_gate.py
    docker exec news-scraper-ymo-backend-1 python /app/compare_model_vs_llm_gate.py
    MSYS_NO_PATHCONV=1 docker cp news-scraper-ymo-backend-1:/app/model_vs_llm_gate_comparison.csv backend/model_vs_llm_gate_comparison.csv
"""

import pickle
import sys
import time

import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split

from app.database import articles

DATASET_CSV = "/app/title_gate_dataset.csv"
MODEL_PKL = "/app/title_gate_classifier_nearest_centroid.pkl"
OUTPUT_CSV = "/app/model_vs_llm_gate_comparison.csv"
EMBEDDING_MODEL_NAME = "Qwen/Qwen3-Embedding-0.6B"

# Must exactly match research/title_gate_classifier.ipynb's split cell so
# the reproduced train/val article_ids are the real ones the model was
# built on -- any mismatch here would leak "seen" articles into the
# comparison and bias the result.
SPLIT_SEED = 42
VAL_FRACTION = 0.25
TEST_FRACTION = 0.25

ENCODE_BATCH_SIZE = 64
PROGRESS_EVERY = 200


# Same class the notebook pickled -- pickle stores it as "__main__.NearestCentroidClassifier"
# because Colab cells execute in __main__, so this script (also running as
# __main__) needs an identically-defined class of that name for pickle.load
# to resolve it.
def normalize(x: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(x, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return x / norms


class NearestCentroidClassifier:
    def fit(self, X, y):
        Xn = normalize(X)
        self.centroid_1 = Xn[y == 1].mean(axis=0)
        self.centroid_0 = Xn[y == 0].mean(axis=0)
        return self

    def predict(self, X):
        Xn = normalize(X)
        sim_1 = Xn @ self.centroid_1
        sim_0 = Xn @ self.centroid_0
        return (sim_1 > sim_0).astype(int)


def main():
    print("Loading pickled model...", flush=True)
    with open(MODEL_PKL, "rb") as f:
        clf = pickle.load(f)
    print(f"  centroid_1 shape={clf.centroid_1.shape}  centroid_0 shape={clf.centroid_0.shape}", flush=True)

    print("\nReproducing the notebook's train/val/test split...", flush=True)
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

    if not remained:
        print("Nothing to compare.", flush=True)
        return

    print(f"\nLoading {EMBEDDING_MODEL_NAME} for classification...", flush=True)
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(EMBEDDING_MODEL_NAME)

    titles = [doc.get("title", "") for doc in remained]
    print(f"\nEmbedding {len(titles)} titles (batch size {ENCODE_BATCH_SIZE}, "
          f"progress every {PROGRESS_EVERY})...", flush=True)
    t0 = time.monotonic()
    embedding_chunks = []
    for i in range(0, len(titles), PROGRESS_EVERY):
        chunk = titles[i:i + PROGRESS_EVERY]
        embedding_chunks.append(model.encode(chunk, batch_size=ENCODE_BATCH_SIZE, show_progress_bar=False))
        done = min(i + PROGRESS_EVERY, len(titles))
        elapsed_so_far = time.monotonic() - t0
        rate = elapsed_so_far / done
        eta_min = rate * (len(titles) - done) / 60
        print(f"  {done}/{len(titles)}  ({rate*1000:.0f}ms/title so far, ~{eta_min:.1f}min left)", flush=True)
    embeddings = np.concatenate(embedding_chunks, axis=0)
    elapsed = time.monotonic() - t0
    print(f"  done in {elapsed:.1f}s ({elapsed/len(titles)*1000:.1f}ms/title)", flush=True)

    Xn = normalize(np.asarray(embeddings, dtype=np.float32))
    sim_1 = Xn @ clf.centroid_1
    sim_0 = Xn @ clf.centroid_0
    model_decisions = (sim_1 > sim_0).astype(int)

    rows = []
    n_agree = 0
    n_model_looser = 0    # model says relevant, LLM archived
    n_llm_missed = 0      # model says archived, LLM kept relevant -- i.e. model would have
                           # archived something the LLM chose to keep (from the model's
                           # perspective the LLM "let it through"; doesn't itself prove the
                           # LLM was wrong -- see printed caveat below)
    for doc, model_decision in zip(remained, model_decisions):
        llm_decision = 0 if doc.get("ingestion_status") == "Archived" else 1
        agree = int(llm_decision == model_decision)
        n_agree += agree
        if not agree:
            if model_decision == 1 and llm_decision == 0:
                n_model_looser += 1
            else:
                n_llm_missed += 1
        rows.append({
            "article_id": str(doc["_id"]),
            "title": doc.get("title", ""),
            "language": doc.get("language") or "en",
            "llm_gate_decision": llm_decision,
            "model_decision": int(model_decision),
            "agree": agree,
            "published_at": doc.get("published_at"),
        })

    pd.DataFrame(rows).to_csv(OUTPUT_CSV, index=False)

    n = len(rows)
    print(f"\nWrote {n} comparison rows to {OUTPUT_CSV}", flush=True)
    print(f"  agreement: {n_agree}/{n} ({n_agree/n*100:.1f}%)", flush=True)
    print(f"  model relevant, LLM archived (model looser / potential leaks): {n_model_looser} ({n_model_looser/n*100:.1f}%)", flush=True)
    print(f"  model archived, LLM relevant (model stricter than LLM here):   {n_llm_missed} ({n_llm_missed/n*100:.1f}%)", flush=True)
    print(
        "\nNote: disagreement alone doesn't say which mechanism was 'right' -- "
        "that needs a human look at a sample of the disagreements (or the "
        "downstream pipeline outcome, once the OR-ensemble is live) since "
        "both decisions are compared against each other, not against "
        "independent ground truth.",
        flush=True,
    )


if __name__ == "__main__":
    main()
