"""
Keyword matching and exclusion logic.

Given a raw article (title + description) and its language, this module
answers two questions:
  - which configured keywords actually appear in it (Gap 1 from the original
    architecture discussion: GNews's OR-queries return everything that
    matched ANY keyword, but don't say which one — so we check ourselves)
  - whether it should be dropped entirely because its title contains an
    excluded word (e.g. "job", "hiring")

Pure text-matching only — no HTTP calls, no MongoDB, no GNews-specific
field names beyond what's passed in.
"""

from typing import List, Optional

from app.keywords import EXCLUDE_WORDS_BY_LANGUAGE, KEYWORDS_BY_LANGUAGE


def find_matched_keywords(title: str, description: Optional[str], language: str) -> List[str]:
    """Returns every keyword configured for this language that appears in
    the article's title or description (case-insensitive substring match).
    """
    haystack = f"{title or ''} {description or ''}".lower()
    keywords = KEYWORDS_BY_LANGUAGE.get(language, [])
    return [kw for kw in keywords if kw.lower() in haystack]


def is_excluded(title: str, language: str) -> bool:
    """Returns True if the title contains any excluded word configured for
    this language (e.g. "job", "hiring" for English). Matches the original
    script's behavior: exclusion is checked against the title only, not the
    description.
    """
    title_lower = (title or "").lower()
    excluded_words = EXCLUDE_WORDS_BY_LANGUAGE.get(language, [])
    return any(word.lower() in title_lower for word in excluded_words)