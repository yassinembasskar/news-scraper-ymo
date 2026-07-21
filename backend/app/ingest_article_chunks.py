import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from pinecone import Pinecone
from sentence_transformers import SentenceTransformer

from services.chunking import chunk_text

EXTRACTED_ARTICLES_DIR = Path(__file__).resolve().parents[1] / "extracted_articles"
EMBEDDING_MODEL_NAME = "Qwen/Qwen3-Embedding-0.6B"
PINECONE_INDEX_NAME = "yazaki"
UPSERT_BATCH_SIZE = 100


def parse_article_file(path: Path) -> dict:
    lines = path.read_text(encoding="utf-8").splitlines()

    url = lines[0].removeprefix("URL:").strip()
    status = lines[1].removeprefix("STATUS:").strip()
    language = lines[2].removeprefix("LANG:").strip().lower()
    body_lines = [line.strip() for line in lines[3:] if line.strip()]
    text = "\n".join(body_lines)

    return {
        "article_id": path.stem,
        "url": url,
        "status": status,
        "language": language,
        "text": text,
    }


def chunk_article(article: dict) -> list[dict]:
    return chunk_text(article["article_id"], article["language"], article["text"])


if __name__ == "__main__":
    files = sorted(EXTRACTED_ARTICLES_DIR.glob("*.txt"))[:5]
    all_chunks = []
    article_urls = {}

    for path in files:
        article = parse_article_file(path)
        if article["status"] != "successful":
            print(f"Skipping {article['article_id']} (status={article['status']})")
            continue

        article_urls[article["article_id"]] = article["url"]
        chunks = chunk_article(article)
        all_chunks.extend(chunks)

        print(f"=== {article['article_id']} ({len(chunks)} chunks) ===")
        for chunk in chunks:
            print(chunk)
        print("-" * 80)

    print(f"Total chunks: {len(all_chunks)}")

    model = SentenceTransformer(EMBEDDING_MODEL_NAME)
    contents = [chunk["content"] for chunk in all_chunks]
    embeddings = model.encode(contents, show_progress_bar=True)

    for chunk, embedding in zip(all_chunks, embeddings):
        chunk["embedding"] = embedding.tolist()

    print(f"Embedded {len(all_chunks)} chunks, vector length: {len(all_chunks[0]['embedding'])}")

    pc = Pinecone(api_key=os.environ["PINECONE_API_KEY"])
    index = pc.Index(PINECONE_INDEX_NAME)

    vectors = [
        {
            "id": chunk["chunk_id"],
            "values": chunk["embedding"],
            "metadata": {
                "article_id": chunk["article_id"],
                "url": article_urls[chunk["article_id"]],
                "order": chunk["order"],
                "language": chunk["language"],
                "content": chunk["content"],
            },
        }
        for chunk in all_chunks
    ]

    upserted_count = 0
    for i in range(0, len(vectors), UPSERT_BATCH_SIZE):
        batch = vectors[i : i + UPSERT_BATCH_SIZE]
        response = index.upsert(vectors=batch)
        upserted_count += response.upserted_count

    print(f"Upserted {upserted_count} vectors into '{PINECONE_INDEX_NAME}'")
