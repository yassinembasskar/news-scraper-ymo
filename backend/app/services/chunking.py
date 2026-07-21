"""
Line-break-based article chunking.

Splits on "\n" (one chunk per original paragraph/line) rather than on
sentence-ending periods — validated interactively across fr/en/ar test
articles. Naive period-splitting breaks on abbreviations like "U.S.",
which line-break splitting doesn't suffer from.

Shared by the ad-hoc ingest_article_chunks.py test script and the
production embedding job (services/embedder.py), so there is exactly one
copy of this logic.
"""


def chunk_text(article_id: str, language: str, text: str) -> list[dict]:
    fragments = [s.strip() for s in text.split("\n") if s.strip()]

    return [
        {
            "chunk_id": f"{article_id}_{order}",
            "article_id": article_id,
            "order": order,
            "language": language,
            "content": content,
        }
        for order, content in enumerate(fragments)
    ]
