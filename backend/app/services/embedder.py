"""
Embedding orchestrator.

This is the "Embed articles" counterpart to services/scraper.py's
run_scrape() and services/extractor.py's run_extraction(). It ties
together:
  - repository.py         -> finds not-yet-embedded articles, persists
                              per-article and per-run results
  - services/chunking.py  -> line-break chunking (validated separately,
                              reused as-is)
  - sentence-transformers -> embeds each chunk with Qwen3-Embedding-0.6B
  - pinecone               -> upserts vectors into the "yazaki" index

Workflow, every time it's triggered:
  1. Guard   -> refuse to start if an embedding run is already in
                progress. Unlike extraction's in-process threading.Lock,
                this guard is the Mongo document meta._id="embedding_status"
                (in_progress flag) — survives a process restart, at the
                cost of no staleness timeout yet: a run that crashes
                without reaching its finally block would need a manual
                reset.
  2. Gather  -> every article with ingestion_status == "Successful" and
                embedding_status != "Embedded" (incremental; "Failed"
                articles are naturally retried next run, no separate
                retry path needed).
  3. Embed   -> for each candidate: chunk its content, embed every chunk,
                upsert into Pinecone, then record chunk_ids /
                number_of_chunks / embedding_status on that article. A
                failure on one article is logged and does not abort the
                run — it's simply picked up again next run.
  4. Log     -> one embedding_runs document for the whole run.
"""

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from pinecone import Pinecone
from sentence_transformers import SentenceTransformer

from app import repository
from app.services.chunking import chunk_text

EMBEDDING_MODEL_NAME = "Qwen/Qwen3-Embedding-0.6B"
PINECONE_INDEX_NAME = "yazaki"
UPSERT_BATCH_SIZE = 100


@dataclass
class EmbeddingSummary:
    status: str  # "success" | "partial" | "failed" | "skipped"
    message: str
    embedded_articles: int = 0
    failed_articles: int = 0
    errors: List[str] = field(default_factory=list)


def _embed_one_article(model, index, article) -> List[str]:
    """Chunks, embeds, and upserts one article's content. Returns the
    chunk ids that were upserted. Raises on any failure — the caller is
    responsible for catching this per-article so one bad article doesn't
    abort the whole run."""
    chunks = chunk_text(article.id, article.language, article.content or "")
    if not chunks:
        raise ValueError("No chunks produced from content")

    contents = [chunk["content"] for chunk in chunks]
    embeddings = model.encode(contents)

    vectors = [
        {
            "id": chunk["chunk_id"],
            "values": embedding.tolist(),
            "metadata": {
                "article_id": chunk["article_id"],
                "url": str(article.url),
                "order": chunk["order"],
                "language": chunk["language"],
                "content": chunk["content"],
            },
        }
        for chunk, embedding in zip(chunks, embeddings)
    ]

    for i in range(0, len(vectors), UPSERT_BATCH_SIZE):
        index.upsert(vectors=vectors[i : i + UPSERT_BATCH_SIZE])

    return [vector["id"] for vector in vectors]


def run_embedding() -> EmbeddingSummary:
    now = datetime.now(timezone.utc)
    candidates = repository.get_articles_pending_embedding()

    if not candidates:
        summary = EmbeddingSummary(status="skipped", message="No articles pending embedding.")
        repository.set_embedding_run_outcome(now, summary.status)
        repository.log_embedding_run(
            status=summary.status,
            message=summary.message,
            embedded_articles=0,
            failed_articles=0,
            errors=[],
        )
        return summary

    model = SentenceTransformer(EMBEDDING_MODEL_NAME)
    pc = Pinecone(api_key=os.environ["PINECONE_API_KEY"])
    index = pc.Index(PINECONE_INDEX_NAME)

    embedded_count = 0
    failed_count = 0
    errors: List[str] = []

    for article in candidates:
        try:
            chunk_ids = _embed_one_article(model, index, article)
            repository.set_article_embedding_result(article.id, chunk_ids, "Embedded")
            embedded_count += 1
        except Exception as e:
            repository.set_article_embedding_status(article.id, "Failed")
            failed_count += 1
            errors.append(f"{article.title}: {e}")

    if failed_count == 0:
        run_status = "success"
    elif embedded_count == 0:
        run_status = "failed"
    else:
        run_status = "partial"

    message = (
        f"Embedding {run_status}: {embedded_count} embedded, {failed_count} failed "
        f"(of {len(candidates)} candidates)."
    )

    repository.set_embedding_run_outcome(now, run_status)
    repository.log_embedding_run(
        status=run_status,
        message=message,
        embedded_articles=embedded_count,
        failed_articles=failed_count,
        errors=errors,
    )

    return EmbeddingSummary(
        status=run_status,
        message=message,
        embedded_articles=embedded_count,
        failed_articles=failed_count,
        errors=errors,
    )


def is_embedding_running() -> bool:
    """Returns True if an embedding run is currently in progress, per the
    Mongo-backed flag. Used by the router both to give the user immediate
    feedback ("already running") and as the actual guard against
    overlapping runs before scheduling a new one."""
    status = repository.get_embedding_status()
    return bool(status and status.in_progress)


def run_embedding_job() -> None:
    """Background-task entrypoint: guards against overlapping runs via
    the Mongo in_progress flag, runs run_embedding(), and always clears
    the flag in a finally block — whether the run succeeded, partially
    failed, or raised. Nothing is waiting on a return value here — the
    endpoint that scheduled this already responded. Progress is only
    visible by reloading the article list and checking each article's
    embedding_status; the final summary is logged server-side."""
    if is_embedding_running():
        return

    repository.set_embedding_in_progress(True)
    try:
        summary = run_embedding()
        print(f"[embedding] {summary.message}")
    finally:
        repository.set_embedding_in_progress(False)
