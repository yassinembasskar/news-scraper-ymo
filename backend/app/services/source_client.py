"""
Shared types for pluggable news-source clients (GNews, NewsAPI.org, ...).

Every source client module (see gnews_client.py for the reference shape)
exposes one function:

    fetch_articles(date_from: datetime, date_to: datetime) -> FetchResult

returning articles already normalized into RawArticle — scraper.py never
touches a source's raw JSON shape directly, so adding a new source is just
adding a new client module plus one line registering it in scraper.py's
SOURCES list. Each client owns its own auth, pagination, rate-limiting, and
date-format parsing; one source (or one language/query within a source)
failing is recorded in FetchResult.errors rather than raised, so the rest
of that source and every other source still runs.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

# Matches the trailing truncation marker several sources append to a
# preview/content field once it's cut off, e.g. "... [+2893 chars]" or
# "... [18992 chars]" — GNews uses both forms (the "+" isn't always
# present; confirmed on a live response), NewsAPI.org uses the "+" form.
# Stripped either way so it doesn't end up looking like real article text
# once stored.
_TRUNCATION_MARKER_RE = re.compile(r"\s*\[\+?\d+ chars\]\s*$")


def best_description(*candidates: Optional[str]) -> Optional[str]:
    """Picks the richest available preview text for RawArticle.description,
    in the given priority order (a source's truncated content/snippet field
    is more useful than its one-line description when both exist — see
    gnews_client.py / newsapi_org_client.py / thenewsapi_com_client.py),
    stripping any trailing "[+N chars]" truncation marker. None/empty
    candidates are skipped."""
    for candidate in candidates:
        if candidate:
            return _TRUNCATION_MARKER_RE.sub("", candidate).strip() or None
    return None


@dataclass
class RawArticle:
    """One article already normalized to the shape scraper.py understands,
    regardless of which API it came from. published_at is already a parsed,
    timezone-aware datetime — each client is responsible for its own API's
    date format."""
    url: str
    title: str
    description: Optional[str]
    source_name: Optional[str]
    published_at: datetime
    language: str


@dataclass
class FetchResult:
    articles: List[RawArticle] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)  # human-readable, one per failed language/request
    # Records this source returned but couldn't normalize (e.g. missing a
    # required field like url or a publish date) — dropped before reaching
    # scraper.py, but still counted so they show up in malformed_articles
    # instead of silently vanishing.
    malformed_count: int = 0
