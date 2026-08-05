"""
Line-break-based article chunking, with a length-based fallback split for
any single line that's too long to safely embed as one chunk.

Splits on "\n" first (one chunk per original paragraph/line) rather than
on sentence-ending periods — validated interactively across fr/en/ar test
articles. Naive period-splitting breaks on abbreviations like "U.S.",
which line-break splitting doesn't suffer from.

Some extracted articles contain a "paragraph" that's really one huge block
of text with no internal line breaks (an extraction artifact — e.g. a
16,651-character single line seen in production). Handing that to the
embedding model as one sequence caused a multi-GB memory spike and OOM-
killed the backend: self-attention memory scales quadratically with token
count, so one long-enough chunk is enough to blow up regardless of how
much RAM the host has. Any line over MAX_CHUNK_CHARS is therefore split
further into overlapping windows — the overlap keeps a split from
severing a sentence's context right at the boundary, which matters for
retrieval quality more than it would for a plain word-count split.

Shared by the ad-hoc ingest_article_chunks.py test script and the
production embedding job (services/embedder.py), so there is exactly one
copy of this logic.
"""

MAX_CHUNK_CHARS = 2000
# Must stay well below MAX_CHUNK_CHARS -- the step size between windows is
# MAX_CHUNK_CHARS - CHUNK_OVERLAP_CHARS, and a non-positive step would loop
# forever.
CHUNK_OVERLAP_CHARS = 200


def _split_long_fragment(text: str) -> list[str]:
    """Splits one over-length fragment into overlapping MAX_CHUNK_CHARS-
    character windows, advancing by MAX_CHUNK_CHARS - CHUNK_OVERLAP_CHARS
    each step so consecutive windows share CHUNK_OVERLAP_CHARS characters
    of context at the boundary. Returns [text] unchanged if it's already
    within the limit."""
    if len(text) <= MAX_CHUNK_CHARS:
        return [text]

    step = MAX_CHUNK_CHARS - CHUNK_OVERLAP_CHARS
    windows = []
    start = 0
    while start < len(text):
        windows.append(text[start:start + MAX_CHUNK_CHARS])
        start += step
    return windows


def chunk_text(article_id: str, language: str, text: str) -> list[dict]:
    lines = [s.strip() for s in text.split("\n") if s.strip()]
    fragments = [window for line in lines for window in _split_long_fragment(line)]

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
