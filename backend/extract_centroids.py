"""
One-off extraction script (temporary, not part of the app) — unpickles the
notebook-trained nearest-centroid classifier and saves just its two
centroid vectors as a plain .npz. Production code (app/services/
title_gate_model.py) loads that .npz directly rather than unpickling a
custom class instance — simpler, no pickle/class-identity fragility to
carry forward permanently (see compare_model_vs_llm_gate.py's docstring
for why the class-shim is needed at all for the .pkl itself).

Run inside the backend container:
    MSYS_NO_PATHCONV=1 docker cp backend/extract_centroids.py news-scraper-ymo-backend-1:/app/extract_centroids.py
    docker exec news-scraper-ymo-backend-1 python /app/extract_centroids.py
    MSYS_NO_PATHCONV=1 docker cp news-scraper-ymo-backend-1:/app/title_gate_centroids.npz backend/app/title_gate_centroids.npz
"""

import numpy as np
import pickle

MODEL_PKL = "/app/title_gate_classifier_nearest_centroid.pkl"
OUTPUT_NPZ = "/app/title_gate_centroids.npz"


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
    with open(MODEL_PKL, "rb") as f:
        clf = pickle.load(f)

    np.savez(
        OUTPUT_NPZ,
        centroid_relevant=clf.centroid_1.astype(np.float32),
        centroid_archived=clf.centroid_0.astype(np.float32),
    )
    print(f"Saved centroid_relevant {clf.centroid_1.shape} and centroid_archived {clf.centroid_0.shape} to {OUTPUT_NPZ}")


if __name__ == "__main__":
    main()
