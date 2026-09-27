"""
One-off migration: transfers the old hand-curated relevance tree
(app/yazaki_tree_id_map.json — 155 nodes, formerly services/scorer.py's
per-node embedding-threshold walk) into the new `entities` Mongo
collection, as entity_group == "TOPIC" entries — see models.Entity's
docstring for why TOPIC is a separate, disjoint group from the
NER-derived PER/ORG/LOC/MISC entities canonicalize_entities() produces.

Reuses the tree's own precomputed per-language embeddings (same
Qwen3-Embedding-0.6B, 1024-dim, already confirmed to match
title_gate_model's) instead of re-embedding anything — this migration
makes zero calls to the embedding model.

Idempotent: run it again any time (e.g. after a tree label edit) and it
just re-upserts the same 155 documents via repository.upsert_topic_entity,
no duplicates.

Usage: python migrate_tree_to_entities.py
"""

import json
from pathlib import Path

from app import repository

TREE_PATH = Path(__file__).resolve().parent / "app" / "yazaki_tree_id_map.json"


def main() -> None:
    with open(TREE_PATH, encoding="utf-8") as f:
        tree = json.load(f)

    for node_id, node in tree.items():
        labels = node.get("labels", {})
        embeddings_by_language = node.get("embeddings", {})
        canonical_name = labels.get("en") or node_id
        aliases = [label for lang, label in labels.items() if lang != "en" and label]
        primary_embedding = embeddings_by_language.get("en") or next(iter(embeddings_by_language.values()))

        repository.upsert_topic_entity(
            node_id=node_id,
            canonical_name=canonical_name,
            aliases=aliases,
            embedding=primary_embedding,
            embeddings_by_language=embeddings_by_language,
            parent_node_id=node.get("parent"),
            depth=node.get("depth", 0),
            threshold=node.get("threshold", 0.4),
        )

    print(f"migrated {len(tree)} tree nodes into the entities collection as TOPIC entries")


if __name__ == "__main__":
    main()
