"""
Persistent subprocess that does the actual chunk/encode/upsert work for
one article at a time, on behalf of services/embedder.py.

Why a subprocess and not a thread: Python cannot force-kill a thread, so
a thread-based per-article timeout can only *abandon* a stuck thread, not
free the CPU/memory it's using. In production this caused a cascading
near-100% failure rate: one slow article's timeout left a zombie thread
still running, competing for CPU with the next article, which then also
missed its timeout, leaving another zombie, and so on until every article
was timing out. A subprocess can be terminated outright, actually
reclaiming its resources — that's the whole point of this module.

The worker process loads the embedding model and Pinecone client once and
then services articles one at a time over a Pipe, so that (multi-second)
load cost is only paid once per run — except right after a timeout, when
the stuck worker is killed and EmbeddingWorker transparently spawns (and
reloads the model into) a replacement for the next article.
"""

import multiprocessing as mp
from typing import List, Optional

from app.config import settings
from app.services.chunking import chunk_text

# An article with more raw content than this is skipped (marked "Failed")
# rather than chunked/embedded — a circuit breaker on top of
# chunking.py's per-chunk length cap, for whatever chunk_text() still
# turns into an unreasonably large number of chunks for one article.
MAX_ARTICLE_CONTENT_CHARS = 50_000
UPSERT_BATCH_SIZE = 100


def _embed_content(model, index, article_id: str, language: str, content: Optional[str], url: str) -> List[str]:
    """Chunks, embeds, and upserts one article's content. Returns the
    chunk ids that were upserted. Raises on any failure. Runs inside the
    worker subprocess (see _worker_main) — no Mongo/repository access
    here, just the pure embed-and-upsert computation."""
    content = content or ""
    if len(content) > MAX_ARTICLE_CONTENT_CHARS:
        raise ValueError(
            f"Content too long ({len(content)} chars > {MAX_ARTICLE_CONTENT_CHARS} cap) — skipped"
        )

    chunks = chunk_text(article_id, language, content)
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
                "url": url,
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


def _worker_main(conn, model_name: str, pinecone_api_key: str, index_name: str) -> None:
    """Entry point run inside the worker subprocess (via the "spawn"
    start method, so this is a fresh interpreter — nothing from the
    parent process's state carries over). Loads the model and Pinecone
    client once, then services one task at a time from the parent over
    `conn` until sent None."""
    from pinecone import Pinecone
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(model_name)
    pc = Pinecone(api_key=pinecone_api_key)
    index = pc.Index(index_name)

    while True:
        task = conn.recv()
        if task is None:
            break
        article_id, language, content, url = task
        try:
            chunk_ids = _embed_content(model, index, article_id, language, content, url)
            conn.send(("ok", chunk_ids))
        except Exception as e:
            conn.send(("error", str(e)))


class EmbeddingWorker:
    """Manages a persistent embedding subprocess for the duration of one
    embedding run. See module docstring for why this exists instead of
    calling the model directly in-process or via a thread-based timeout."""

    def __init__(self, model_name: str, index_name: str, timeout_seconds: float):
        self._model_name = model_name
        self._index_name = index_name
        self._timeout_seconds = timeout_seconds
        self._process = None
        self._conn = None

    def _spawn(self) -> None:
        ctx = mp.get_context("spawn")
        parent_conn, child_conn = ctx.Pipe()
        process = ctx.Process(
            target=_worker_main,
            args=(child_conn, self._model_name, settings.pinecone_api_key, self._index_name),
            daemon=True,
        )
        process.start()
        self._process = process
        self._conn = parent_conn

    def _kill(self) -> None:
        """Forcibly terminates the worker process, actually reclaiming
        its CPU and memory — the entire reason this is a subprocess and
        not a thread."""
        if self._process is not None:
            self._process.terminate()
            self._process.join(5)
            if self._process.is_alive():
                self._process.kill()
                self._process.join()
        self._process = None
        self._conn = None

    def embed(self, article_id: str, language: str, content: Optional[str], url: str) -> List[str]:
        """Embeds one article via the persistent worker, spawning it
        first if this is the first call (or the previous worker was
        killed after a timeout or crash). Raises TimeoutError if the
        worker doesn't respond within timeout_seconds (the worker is
        killed first, so the timed-out work's CPU/memory is actually
        reclaimed, not just abandoned), or RuntimeError if the worker
        process dies unexpectedly or the embedding itself fails."""
        if self._process is None or not self._process.is_alive():
            self._spawn()

        try:
            self._conn.send((article_id, language, content, url))
        except (BrokenPipeError, OSError) as e:
            self._kill()
            raise RuntimeError(f"Embedding worker process died before accepting the task: {e}")

        if not self._conn.poll(self._timeout_seconds):
            self._kill()
            raise TimeoutError(f"Embedding exceeded the {self._timeout_seconds}s timeout")

        try:
            status, payload = self._conn.recv()
        except EOFError as e:
            self._kill()
            raise RuntimeError(f"Embedding worker process died while processing: {e}")

        if status == "ok":
            return payload
        raise RuntimeError(payload)

    def shutdown(self) -> None:
        """Stops the worker process cleanly (or kills it if it doesn't
        exit promptly) — called once at the end of a run so nothing is
        left running after run_embedding() returns."""
        if self._process is not None and self._process.is_alive():
            try:
                self._conn.send(None)
                self._process.join(5)
            except Exception:
                pass
        self._kill()
