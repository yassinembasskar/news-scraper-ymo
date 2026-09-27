"""
LLM Insight client — the reusable core of the LLM-backed pipeline stages,
extracted from the scratch validation done during the original per-article
report stage's design.

Functions, all hitting the containerized Ollama service
(qwen2.5:7b-instruct-q4_K_M) over HTTP:
  - gate_title(): a fast, title-only pre-filter — "could this headline
    plausibly concern anything this deployment tracks?" Deliberately
    shallow (short prompt, small context, no business-logic doc) so it
    stays cheap on CPU; biased to say yes when unsure, since a false
    negative here means the article is archived and never reaches
    extraction/preprocessing/insights at all. Not the live gate anymore
    (see services/title_gate.py) — kept as the reference judgment periodic
    comparison batches check the embedding classifier against.
  - assess_cluster(): preprocessing phase 6 (services/preprocessing.py) —
    one condensed cluster's chunks in (plus its similar_clusters context),
    a list of structured insight dicts out, each citing which chunk
    indices support it. Replaced assess()/translate_to_english() (removed
    2026-09-15, see docs/completion-plan.md): those judged one already-
    English article at a time, once per report generation; this judges an
    entire clustered story at once, as a normal preprocessing phase, and
    can actually use similar_clusters (an article-level call never had
    that context). Chunks are NOT translated to English first — a
    deliberate scope cut versus the old per-article flow (see this
    function's docstring for why, and the README's Known limitations).
  - summarize_cluster(): an earlier, abstractive cluster-summary attempt
    (superseded by services/cluster_condenser.py's non-LLM extractive
    condensation for being too slow on CPU) — kept unused, same "future
    reconsideration" treatment as the LLM title gate above.

Design decisions baked in here, all validated during the original
per-article report stage's design and still true of assess_cluster():
  - JSON-Schema-constrained output (Ollama's `format`), not just
    format:"json" — the latter guarantees valid JSON but not the required
    shape (it once returned {} instead of []).
  - num_ctx=8192 explicitly — Ollama's 4096 default silently truncates; a
    real article + the business-logic prompt already exceeded 4096.
  - temperature=0.2 — this is structured classification, not open-ended
    generation; a low temperature stabilizes the concerned_param field
    choice, which wobbled at the chat default.

No Mongo access here — orchestration (which clusters, storing results,
progress) lives in services/preprocessing.py.
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

# On this hardware (CPU-only, no GPU — see docker-compose.yml's ollama
# service) a single call was observed taking the full previous 300s budget
# and still not finishing. 600s gives slow-but-working calls room to
# actually complete instead of being counted as a failure; a call that's
# truly stuck still gets killed rather than hanging forever.
REQUEST_TIMEOUT = 600

_CLUSTER_INSIGHT_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "confidence": {"type": "integer"},
            "concerned_param": {"type": "string"},
            "effect": {"type": "string", "enum": ["positive", "negative", "neutral"]},
            "recommended_action": {"type": "string"},
            "chunk_indices": {"type": "array", "items": {"type": "integer"}},
        },
        "required": [
            "title", "confidence", "concerned_param", "effect",
            "recommended_action", "chunk_indices",
        ],
    },
}

_SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
}

_GATE_SCHEMA = {
    "type": "object",
    "properties": {"relevant": {"type": "boolean"}},
    "required": ["relevant"],
}

# Title + the (short, no entity list) gate prompt is well under 200 tokens;
# far below the 8192 the full assess()/translate calls need for a whole
# article + the business-logic doc, so the gate call's KV-cache stays small
# and fast.
_GATE_NUM_CTX = 1024

_GATE_PROMPT = (
    "You are a relevance filter for a business news monitoring system. "
    "Judge whether this headline could affect: (1) a supply chain or "
    "logistics business, (2) the automotive industry, or (3) businesses in "
    "general. Be loose — only answer no if it's clearly unrelated (e.g. "
    "sports, entertainment, personal news). Respond with the required JSON only."
)


@lru_cache(maxsize=1)
def _system_prompt() -> str:
    """Business-logic doc + authoritative SAP attribute list, concatenated
    once. Both are static config files (see the two llm_insight_*.md files);
    cached so they're not re-read on every article."""
    business_logic = BUSINESS_LOGIC_PATH.read_text(encoding="utf-8")
    sap_attributes = SAP_ATTRIBUTES_PATH.read_text(encoding="utf-8")
    return f"{business_logic}\n\n## SAP attribute list (authoritative)\n\n{sap_attributes}"


def _chat(
    messages: list, fmt=None, temperature: float = 0.2, num_ctx: int = 8192, timeout: int = REQUEST_TIMEOUT
) -> str:
    """One Ollama /api/chat call, non-streaming. Returns the assistant
    message content string. Raises on HTTP error (the caller decides
    whether one article's failure aborts the run — it doesn't). `timeout`
    defaults to the fixed REQUEST_TIMEOUT, but assess_cluster() passes a
    per-call value scaled to how much content is actually in the prompt —
    see SECONDS_PER_CHUNK."""
    payload = {
        "model": settings.ollama_model,
        "messages": messages,
        "stream": False,
        "options": {"num_ctx": num_ctx, "temperature": temperature},
    }
    if fmt is not None:
        payload["format"] = fmt
    response = requests.post(
        f"{settings.ollama_host}/api/chat",
        json=payload,
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()["message"]["content"]


# Per-member content cap when building the multi-article summarization
# prompt — a cluster can have many members; capping each one (rather than
# the whole prompt) keeps every member at least partially represented
# instead of the first one eating the whole context budget.
SUMMARY_MEMBER_CONTENT_CHARS = 1500
# Caps how many member articles actually go into the prompt — beyond this,
# more members mostly add redundant detail (it's the same story) rather
# than new information, and it keeps the prompt bounded regardless of how
# large a cluster gets.
SUMMARY_MAX_MEMBERS = 8


def summarize_cluster(members: List[Tuple[str, str]]) -> str:
    """Summarizes a cluster of same-story articles into one short
    paragraph. `members` is [(title, content), ...] for the cluster's
    successfully-extracted articles (see services/preprocessing.py) —
    content from a Failed extraction isn't available to summarize, so
    those members are simply absent from the prompt, not represented by
    empty text.

    Deliberately abstractive (one written-from-scratch summary, not a
    copy of any single source) — unlike the report stage's per-article
    assess(), which never blends facts across sources because doing so
    there would fabricate a claim no single source made, blending
    multiple accounts of the SAME event into one paragraph is the actual
    point of this function, not a risk to avoid. The prompt still asks
    the model to flag rather than silently resolve any factual
    disagreement between sources."""
    selected = members[:SUMMARY_MAX_MEMBERS]
    sources = "\n\n".join(
        f"### Source {i + 1}\nTitle: {title}\n\n{(content or '')[:SUMMARY_MEMBER_CONTENT_CHARS]}"
        for i, (title, content) in enumerate(selected)
    )
    raw = _chat(
        messages=[
            {
                "role": "system",
                "content": (
                    "The following are multiple news sources covering the same "
                    "story. Write one concise, factual summary (2-4 sentences) "
                    "of the story they describe together. If the sources "
                    "disagree on a specific fact (a number, a date, a name), "
                    "note the disagreement rather than silently picking one."
                ),
            },
            {"role": "user", "content": sources},
        ],
        fmt=_SUMMARY_SCHEMA,
    )
    parsed = json.loads(raw)
    return parsed.get("summary", "")


def gate_title(title: str, language: str) -> bool:
    """Fast, title-only relevance pre-filter — see the module docstring.
    Returns True (keep, proceed to extraction) or False (archive, skip
    extraction). Raises on HTTP error, same as the other functions here;
    the caller decides how to handle that (the scraper fails open rather
    than lose an article to a transient LLM/Ollama hiccup).

    One static English-language prompt for every article language, no
    translation and deliberately no tracked-entity list — both were tried
    against the same real 20-article sample and did worse. An entity list
    (first keywords.py's company names, then the relevance tree's ~155
    topic labels) anchored the model too narrowly: it missed "Thai Honda
    holds prices amid cost pressures" and two "ship sinks in the Red Sea
    after attack" headlines, because "Red Sea"/"Suez Canal" happen not to be
    tree node labels even though they're tracked search keywords. Dropping
    the entity list and trusting the model's own judgment on the three
    categories below fixed every one of those misses with no new false
    positives on the sports/celebrity/personal-news noise (8/20 relevant vs.
    3/20 with either entity list, same 20 real articles). Translating
    non-English titles first was tried too and was both slower (reuses the
    full 8192-context call) and less accurate (a real mistranslation —
    "أوبل"/Opel became "Oblig" and was then missed) than judging the
    original-language title directly, which is what this does."""
    raw = _chat(
        messages=[
            {"role": "system", "content": _GATE_PROMPT},
            {"role": "user", "content": title},
        ],
        fmt=_GATE_SCHEMA,
        num_ctx=_GATE_NUM_CTX,
    )
    parsed = json.loads(raw)
    return bool(parsed.get("relevant", True))


# Character budget for the numbered chunk block fed to assess_cluster() —
# guards against overflowing num_ctx (8192 tokens) once the ~2.9k-token
# system prompt is accounted for; ~8000 chars is roughly 2000-2600 tokens,
# leaving comfortable headroom. A cluster can have up to
# cluster_condenser.CONDENSE_MAX_CHUNKS (100)
# chunks; rather than raise the budget to fit all of them, extra chunks
# beyond it are simply left out of the prompt (see _build_chunk_block) —
# they're the ones MMR selected last, i.e. the least additionally distinct
# from what's already included, so dropping them costs the least.
MAX_CLUSTER_CONTENT_CHARS = 8000

# Per-call timeout for assess_cluster(), scaled to how many chunks actually
# made it into the prompt rather than a fixed budget — measured live
# (2026-09-15): a real 24-chunk cluster took ~484s (~20.2s/chunk), and a
# real 90-chunk cluster exceeded the old fixed 600s REQUEST_TIMEOUT
# outright. A fixed timeout either wastes time waiting on small clusters
# or kills large ones that were genuinely still working — scaling it to
# the actual prompt size (via the chunk count _build_chunk_block reports,
# which already accounts for MAX_CLUSTER_CONTENT_CHARS truncation) tracks
# the real cost driver instead.
SECONDS_PER_CHUNK = 20

# Flat safety margin added on top of SECONDS_PER_CHUNK x chunk count. The
# 20s/chunk rate itself came from a single measurement with zero slack
# (484s actual vs. 480s a bare 20x24 would compute) — without this, that
# exact call would have been killed ~4s before it actually finished. This
# doesn't fix the underlying slowness, just stops normal call-to-call
# variance around the measured average from being misread as failures.
TIMEOUT_BUFFER_SECONDS = 100


def _build_chunk_block(chunks: List[str]) -> Tuple[str, int]:
    """Numbers every chunk (`[i] text`, matching the index assess_cluster()
    asks the model to cite back) up to MAX_CLUSTER_CONTENT_CHARS, keeping
    as many whole chunks as fit rather than truncating mid-chunk. Returns
    (block, count) — count is how many chunks actually made it in, the
    basis for assess_cluster()'s dynamic timeout (SECONDS_PER_CHUNK)."""
    lines: List[str] = []
    total = 0
    for i, chunk in enumerate(chunks):
        line = f"[{i}] {chunk}"
        if lines and total + len(line) > MAX_CLUSTER_CONTENT_CHARS:
            break
        lines.append(line)
        total += len(line)
    return "\n".join(lines), len(lines)


def assess_cluster(chunks: List[str], similar_clusters: List[dict]) -> List[dict]:
    """Assesses one condensed cluster's content chunks against the
    business-logic rules. Returns a list of insight dicts, each matching
    ClusterInsight's LLM-produced fields (title, confidence,
    concerned_param, effect, recommended_action, chunk_indices) — normally
    0 to a handful of elements, one per distinct concrete operational
    event the story describes. `chunk_indices` are validated against the
    real range of `chunks` before being returned: a hallucinated
    out-of-range index is dropped rather than kept, since a wrong citation
    is worse than a missing one.

    `similar_clusters` (each a SimilarClusterRef.model_dump()) is passed
    as context only — related past coverage the model can use to judge
    whether this is a continuation of an ongoing situation or something
    new, never something to cite chunk_indices against (it has none of
    its own chunks here).

    Deliberately does NOT translate non-English chunks first, unlike the
    per-article flow this replaced — chunks in one cluster can mix
    languages (different member articles), so there's no single "the
    article's language" to translate from, and per-chunk translation
    would reintroduce a per-item LLM round trip this whole condensation-
    based redesign was built to avoid. The prompt instead tells the model
    each chunk may be in English, French, or Arabic. Not yet validated
    against the same kind of real mixed-language test the old per-article
    translate-first design was (see the README's Known limitations)."""
    chunk_block, included_count = _build_chunk_block(chunks)
    if not chunk_block:
        return []

    if similar_clusters:
        context_lines = "\n".join(
            f"- {s['date']}: {s['representative_title']} "
            f"(similarity {s['score']:.2f}, shared entities: {', '.join(s['shared_entities']) or 'none'})"
            for s in similar_clusters
        )
        context_section = (
            "\n\n### Related past coverage (context only — these have no "
            f"chunk indices of their own, never cite them)\n{context_lines}"
        )
    else:
        context_section = ""

    user_prompt = (
        "Below are the numbered content chunks making up one clustered news "
        "story. Each chunk may be in English, French, or Arabic — read it "
        "in its own language. Apply the rules above and return a JSON "
        "array of the concrete operational events this story describes, "
        "each with the chunk_indices (the bracketed numbers below) that "
        "support it. If nothing is operationally relevant, return an "
        f"empty array.{context_section}\n\n### Chunks\n{chunk_block}"
    )

    raw = _chat(
        messages=[
            {"role": "system", "content": _system_prompt()},
            {"role": "user", "content": user_prompt},
        ],
        fmt=_CLUSTER_INSIGHT_SCHEMA,
        timeout=SECONDS_PER_CHUNK * included_count + TIMEOUT_BUFFER_SECONDS,
    )
    parsed = json.loads(raw)
    # Schema constrains this to a list, but guard anyway — a future model/
    # Ollama change shouldn't be able to crash the whole preprocessing run.
    if not isinstance(parsed, list):
        return []

    valid_indices = range(len(chunks))
    for item in parsed:
        item["chunk_indices"] = [i for i in item.get("chunk_indices", []) if i in valid_indices]
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
