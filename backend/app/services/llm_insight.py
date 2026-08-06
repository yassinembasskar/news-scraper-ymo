"""
LLM Insight client — the reusable core of the daily-report (Stage 5)
pipeline, extracted from the scratch validation done during that stage's
design.

Two functions, both hitting the containerized Ollama service
(qwen2.5:7b-instruct-q4_K_M) over HTTP:
  - translate_to_english(): translates a non-English article to English,
    because testing showed the model reliably judges relevance in English
    but silently misses the same clearly-relevant event when it's in Arabic
    (confirmed against a ground-truth case that 8 English framings caught
    and the Arabic original did not). Returns (title, body) separately, via
    schema-constrained output so the split is reliable. The caller
    (services/report_job.py) caches the result on the article so this only
    runs once per non-English article, not on every report run.
  - assess(): one already-English article in, a list of structured insight
    dicts out (0 or 1 element — a per-article call, never a batch, because
    batch calls were found to make the model lazily return an empty array
    and skip reasoning entirely once ~4 articles were mixed together).
    Translation is NOT done here — the caller passes English text (original,
    or the cached translation).

Design decisions baked in here, all validated during Stage 3:
  - JSON-Schema-constrained output (Ollama's `format`), not just
    format:"json" — the latter guarantees valid JSON but not the required
    shape (it once returned {} instead of []).
  - num_ctx=8192 explicitly — Ollama's 4096 default silently truncates; a
    real article + the business-logic prompt already exceeded 4096.
  - temperature=0.2 — this is structured classification, not open-ended
    generation; a low temperature stabilizes the concerned_param field
    choice, which wobbled at the chat default.

No Mongo access here — orchestration (which articles, storing results,
progress) lives in services/report_job.py.
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import List, Tuple

import requests

from app.config import settings

_APP_DIR = Path(__file__).resolve().parents[1]
BUSINESS_LOGIC_PATH = _APP_DIR / "llm_insight_business_logic.md"
SAP_ATTRIBUTES_PATH = _APP_DIR / "llm_insight_sap_attributes.md"

# Guardrail against a pathologically long article overflowing num_ctx even
# with the system prompt (~2.9k tokens) accounted for. ~8000 chars is
# roughly 2000-2600 tokens, leaving comfortable headroom inside 8192.
MAX_CONTENT_CHARS = 8000

# Long enough for a cold first call (~120s one-time model warm-up on CPU)
# plus generation; the report job's per-article loop pays that warm-up only
# once, on the first article of a run.
REQUEST_TIMEOUT = 300

_INSIGHT_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "confidence": {"type": "integer"},
            "concerned_param": {"type": "string"},
            "effect": {"type": "string", "enum": ["positive", "negative", "neutral"]},
            "recommended_action": {"type": "string"},
        },
        "required": ["title", "confidence", "concerned_param", "effect", "recommended_action"],
    },
}

# Translating to {title, body} under a schema (rather than a free-text blob
# we'd have to parse back apart) makes the title/body split reliable.
_TRANSLATION_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "body": {"type": "string"},
    },
    "required": ["title", "body"],
}


@lru_cache(maxsize=1)
def _system_prompt() -> str:
    """Business-logic doc + authoritative SAP attribute list, concatenated
    once. Both are static config files (see the two llm_insight_*.md files);
    cached so they're not re-read on every article."""
    business_logic = BUSINESS_LOGIC_PATH.read_text(encoding="utf-8")
    sap_attributes = SAP_ATTRIBUTES_PATH.read_text(encoding="utf-8")
    return f"{business_logic}\n\n## SAP attribute list (authoritative)\n\n{sap_attributes}"


def _chat(messages: list, fmt=None, temperature: float = 0.2) -> str:
    """One Ollama /api/chat call, non-streaming. Returns the assistant
    message content string. Raises on HTTP error (the caller decides
    whether one article's failure aborts the run — it doesn't)."""
    payload = {
        "model": settings.ollama_model,
        "messages": messages,
        "stream": False,
        "options": {"num_ctx": 8192, "temperature": temperature},
    }
    if fmt is not None:
        payload["format"] = fmt
    response = requests.post(
        f"{settings.ollama_host}/api/chat",
        json=payload,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()["message"]["content"]


def translate_to_english(title: str, content: str) -> Tuple[str, str]:
    """Translates a non-English article to English. Returns
    (translated_title, translated_body) — the caller caches these on the
    article and later feeds "Title: <t>\\n\\n<body>" into assess()."""
    content = (content or "")[:MAX_CONTENT_CHARS]
    raw = _chat(
        messages=[
            {
                "role": "system",
                "content": (
                    "Translate the following news article to English. Return "
                    "the translated title and body — a faithful translation "
                    "only, no commentary or notes."
                ),
            },
            {"role": "user", "content": f"Title: {title}\n\n{content}"},
        ],
        fmt=_TRANSLATION_SCHEMA,
    )
    parsed = json.loads(raw)
    return parsed.get("title", title), parsed.get("body", content)


def assess(title: str, content: str) -> List[dict]:
    """Assesses one already-English article against the business-logic
    rules. Returns a list of insight dicts — normally 0 (not relevant) or 1
    (relevant) element. Each dict matches ReportInsight's LLM-produced
    fields (title, confidence, concerned_param, effect, recommended_action);
    the caller attaches article_id/source_title. Does NOT translate — pass
    English text (the original, or a cached translation)."""
    content = (content or "")[:MAX_CONTENT_CHARS]
    user_prompt = (
        "Here is today's batch of 1 pre-filtered article. Apply the rules "
        "above and return a single JSON array — only the relevant ones, in "
        "the specified shape. If none are relevant, return an empty array.\n\n"
        f"### Article 1\nTitle: {title}\n\n{content}"
    )

    raw = _chat(
        messages=[
            {"role": "system", "content": _system_prompt()},
            {"role": "user", "content": user_prompt},
        ],
        fmt=_INSIGHT_SCHEMA,
    )
    parsed = json.loads(raw)
    # Schema constrains this to a list, but guard anyway — a future model/
    # Ollama change shouldn't be able to crash the whole report run.
    if not isinstance(parsed, list):
        return []
    return parsed


def ollama_available() -> bool:
    """Quick liveness check on the Ollama service — used by the report
    router to fail a generate request fast with a clear message if the
    model server is down, rather than starting a job that will error on its
    first article."""
    try:
        response = requests.get(f"{settings.ollama_host}/api/tags", timeout=10)
        response.raise_for_status()
        return True
    except Exception:
        return False
