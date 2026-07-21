import json
import re
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from sentence_transformers import SentenceTransformer

TREE_PATH = Path(__file__).resolve().parent / "yazaki_tree_id_map.json"
LANGUAGES = ("en", "fr", "ar")

with open(TREE_PATH, encoding="utf-8") as f:
    tree = json.load(f)

for node in tree.values():
    node["threshold"] = 0.2

texts = []
index = []
for node_key, node in tree.items():
    for lang in LANGUAGES:
        texts.append(node["labels"][lang])
        index.append((node_key, lang))

print(f"Encoding {len(texts)} sentences from {len(tree)} nodes...")

model = SentenceTransformer("Qwen/Qwen3-Embedding-0.6B")
embeddings = model.encode(texts, show_progress_bar=True)

for (node_key, lang), embedding in zip(index, embeddings):
    tree[node_key].setdefault("embeddings", {})[lang] = [round(float(x), 6) for x in embedding]

NUMBER = r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?"
NUMBER_ARRAY = re.compile(r"\[\n((?:\s*" + NUMBER + r",?\n)+)\s*\]")


def compact_number_arrays(json_text):
    def replace(match):
        numbers = re.findall(NUMBER, match.group(1))
        return "[" + ", ".join(numbers) + "]"

    return NUMBER_ARRAY.sub(replace, json_text)


json_text = json.dumps(tree, ensure_ascii=False, indent=2)
json_text = compact_number_arrays(json_text)

with open(TREE_PATH, "w", encoding="utf-8") as f:
    f.write(json_text)

print(f"Wrote embeddings for {len(tree)} nodes to {TREE_PATH}")
