"""
Stats orchestration.

All bucketing math — hour/day/week grouping, Morocco local time conversion,
and the month-boundary week-splitting rule — lives here in plain Python
(Option B from our design discussion), operating on the lean projection
returned by repository.get_articles_for_stats(). No Mongo aggregation
pipelines; one query per chart request, then bucketed in memory.

Morocco does not use a fixed UTC+1 offset year-round (it reverts to UTC+0
for roughly a month around Ramadan), so all local-time handling goes through
zoneinfo.ZoneInfo("Africa/Casablanca") rather than a hardcoded offset.
"""

import calendar
from collections import Counter
from datetime import date as date_type
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from app import repository

MOROCCO_TZ = ZoneInfo("Africa/Casablanca")
LANGUAGES = ["en", "fr", "ar"]


# ── Time helpers ────────────────────────────────────────────────────────

def _ensure_utc(dt: datetime) -> datetime:
    """Mongo hands back naive datetimes on read (see database.py notes) —
    treat naive as UTC, then normalize anything else to UTC too."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _to_morocco(dt: datetime) -> datetime:
    return _ensure_utc(dt).astimezone(MOROCCO_TZ)


def _day_bounds_utc(local_date: date_type) -> Tuple[datetime, datetime]:
    """Returns (start, end) of one Morocco-local calendar day, as UTC
    datetimes suitable for querying Mongo (published_at is stored in UTC)."""
    start_local = datetime(local_date.year, local_date.month, local_date.day, tzinfo=MOROCCO_TZ)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def _month_bounds_utc(year: int, month: int) -> Tuple[datetime, datetime]:
    start_local = datetime(year, month, 1, tzinfo=MOROCCO_TZ)
    if month == 12:
        end_local = datetime(year + 1, 1, 1, tzinfo=MOROCCO_TZ)
    else:
        end_local = datetime(year, month + 1, 1, tzinfo=MOROCCO_TZ)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def _current_morocco_date() -> date_type:
    return datetime.now(MOROCCO_TZ).date()


# ── Core counting ───────────────────────────────────────────────────────

def _summarize(raw_articles: List[dict]) -> dict:
    """Given lean article dicts, returns total count, the single most
    frequent matched keyword, and per-language counts."""
    language_counts = {lang: 0 for lang in LANGUAGES}
    keyword_counter: Counter = Counter()

    for a in raw_articles:
        lang = a.get("language")
        if lang in language_counts:
            language_counts[lang] += 1
        for kw in a.get("matched_keywords", []):
            keyword_counter[kw] += 1

    top_keyword = keyword_counter.most_common(1)[0][0] if keyword_counter else None

    return {
        "total": len(raw_articles),
        "top_keyword": top_keyword,
        "language_counts": language_counts,
    }


def get_bucket_summary(date_from: datetime, date_to: datetime) -> dict:
    """Total, top keyword, and per-language breakdown for one arbitrary
    date range — used both by get_overview() and by the frontend's
    click-a-bar panel."""
    raw = repository.get_articles_for_stats(date_from, date_to)
    return _summarize(raw)


# ── Pipeline stage stats (overview bar, under each button) ─────────────

def get_pipeline_stats() -> dict:
    """Per-stage status breakdowns shown under the "Gate titles", "Extract
    articles", "Embed articles", and "Relativity scoring" buttons. Each
    stage's denominator is its own actual candidate pool, not the whole
    collection — extraction is counted over every article, title gating
    over articles still in "No Extraction" (same pool
    get_articles_pending_title_gate() draws from — anything
    Successful/Failed/Expired/Archived is already resolved one way or
    another and will never be touched by this stage again, regardless of
    whether it was ever literally run through the LLM check, so counting
    it here would just be noise), embedding over articles that were
    actually successfully extracted (the only ones ever eligible, per
    get_articles_pending_embedding()'s ingestion_status == "Successful"
    filter), and scoring over articles that were actually embedded (same
    reasoning, embedding_status == "Embedded")."""
    title_gate_counts = repository.get_status_field_counts(
        "title_gate_status", {"ingestion_status": "No Extraction"}
    )
    title_gate_total = sum(title_gate_counts.values())
    extraction_counts = repository.get_status_field_counts("ingestion_status")
    total_articles = sum(extraction_counts.values())

    embedding_counts = repository.get_status_field_counts(
        "embedding_status", {"ingestion_status": "Successful"}
    )
    successful_extractions = extraction_counts.get("Successful", 0)

    scoring_counts = repository.get_status_field_counts(
        "scored_status", {"embedding_status": "Embedded"}
    )
    embedded_total = embedding_counts.get("Embedded", 0)

    return {
        "title_gate": {
            "total": title_gate_total,
            "gated": title_gate_counts.get("Gated", 0),
            "not_gated": title_gate_counts.get("Not Gated", 0),
        },
        "extraction": {
            "total": total_articles,
            "successful": extraction_counts.get("Successful", 0),
            "failed": extraction_counts.get("Failed", 0),
            # Its own bucket, not folded into "failed" — an archived article
            # was never even attempted; it was deliberately never sent to
            # extraction at all, a different outcome from a real extraction
            # failure (paywall, timeout, layout). See routers/articles.py's
            # Archived-review endpoints.
            "archived": extraction_counts.get("Archived", 0),
            "expired": extraction_counts.get("Expired", 0),
            "not_extracted": extraction_counts.get("No Extraction", 0),
            "pending": extraction_counts.get("Pending", 0),
        },
        "embedding": {
            "total": successful_extractions,
            "embedded": embedding_counts.get("Embedded", 0),
            "not_embedded": embedding_counts.get("Not Embedded", 0),
            "failed": embedding_counts.get("Failed", 0),
            "expired": embedding_counts.get("Expired", 0),
            "pending": embedding_counts.get("Pending", 0),
        },
        "scoring": {
            "total": embedded_total,
            "scored": scoring_counts.get("Scored", 0),
            "not_scored": scoring_counts.get("Not Scored", 0),
            "failed": scoring_counts.get("Failed", 0),
            "pending": scoring_counts.get("Pending", 0),
        },
    }


# ── Overview (top summary bar) ──────────────────────────────────────────

def get_overview() -> dict:
    """Today's total + top keyword, this month's total + top keyword (both
    in Morocco local time), plus the last successful scrape watermark."""
    today_local = _current_morocco_date()

    day_from, day_to = _day_bounds_utc(today_local)
    month_from, month_to = _month_bounds_utc(today_local.year, today_local.month)

    day_summary = get_bucket_summary(day_from, day_to)
    month_summary = get_bucket_summary(month_from, month_to)

    scrape_status = repository.get_scrape_status()
    last_scraped = scrape_status.last_scraped if scrape_status else None

    return {
        "today_total": day_summary["total"],
        "today_top_keyword": day_summary["top_keyword"],
        "month_total": month_summary["total"],
        "month_top_keyword": month_summary["top_keyword"],
        "last_scraped": last_scraped,
    }


# ── Charts ───────────────────────────────────────────────────────────────

def get_day_chart(local_date: date_type) -> dict:
    """Hourly bars (00:00-23:00) for one Morocco-local calendar day, each
    broken down by language. Each bucket carries its own UTC date_from/
    date_to so the frontend can drill into /articles without redoing any
    bucketing math itself."""
    day_from, day_to = _day_bounds_utc(local_date)
    raw = repository.get_articles_for_stats(day_from, day_to)

    buckets = []
    for hour in range(24):
        hour_start_local = datetime(local_date.year, local_date.month, local_date.day, hour, tzinfo=MOROCCO_TZ)
        hour_end_local = hour_start_local + timedelta(hours=1)
        buckets.append({
            "label": f"{hour:02d}:00",
            "en": 0, "fr": 0, "ar": 0, "total": 0,
            "date_from": hour_start_local.astimezone(timezone.utc).isoformat(),
            "date_to": hour_end_local.astimezone(timezone.utc).isoformat(),
        })

    for a in raw:
        local_dt = _to_morocco(a["published_at"])
        bucket = buckets[local_dt.hour]
        lang = a.get("language")
        if lang in LANGUAGES:
            bucket[lang] += 1
        bucket["total"] += 1

    return {"range": "day", "period_label": local_date.isoformat(), "buckets": buckets}


def get_week_chart(iso_year: int, iso_week: int) -> dict:
    """Daily bars (Mon-Sun) for one ISO week, each broken down by language."""
    week_start = date_type.fromisocalendar(iso_year, iso_week, 1)  # Monday

    range_from, _ = _day_bounds_utc(week_start)
    _, range_to = _day_bounds_utc(week_start + timedelta(days=6))
    raw = repository.get_articles_for_stats(range_from, range_to)

    buckets = []
    for i in range(7):
        day = week_start + timedelta(days=i)
        day_from, day_to = _day_bounds_utc(day)
        buckets.append({
            "label": day.strftime("%a %d"),
            "en": 0, "fr": 0, "ar": 0, "total": 0,
            "date_from": day_from.isoformat(),
            "date_to": day_to.isoformat(),
        })

    for a in raw:
        local_dt = _to_morocco(a["published_at"])
        day_index = (local_dt.date() - week_start).days
        if 0 <= day_index < 7:
            bucket = buckets[day_index]
            lang = a.get("language")
            if lang in LANGUAGES:
                bucket[lang] += 1
            bucket["total"] += 1

    return {"range": "week", "period_label": f"Week {iso_week}, {iso_year}", "buckets": buckets}


def get_month_chart(year: int, month: int) -> dict:
    """Weekly bars (Mon-Sun) for one calendar month, each broken down by
    language. A week overlapping two months is split at the month boundary:
    each bar only counts the days of that week that actually fall within
    THIS month — the same ISO week appears as a separate, smaller bar in
    whichever other month it also touches, rather than being credited
    entirely to one or the other."""
    days_in_month = calendar.monthrange(year, month)[1]
    first_day = date_type(year, month, 1)
    last_day = date_type(year, month, days_in_month)

    # Walk every day of the month once, grouping by ISO (year, week) and
    # tracking the min/max date seen per week — this naturally clips each
    # week's range to only the days that are actually inside this month.
    week_order: List[Tuple[int, int]] = []
    week_date_span: Dict[Tuple[int, int], List[date_type]] = {}
    seen = set()

    d = first_day
    while d <= last_day:
        key = d.isocalendar()[:2]
        if key not in seen:
            seen.add(key)
            week_order.append(key)
            week_date_span[key] = [d, d]
        else:
            week_date_span[key][1] = d
        d += timedelta(days=1)

    week_counts = {key: {"en": 0, "fr": 0, "ar": 0, "total": 0} for key in week_order}

    month_from, _ = _day_bounds_utc(first_day)
    _, month_to = _day_bounds_utc(last_day)
    raw = repository.get_articles_for_stats(month_from, month_to)

    for a in raw:
        local_dt = _to_morocco(a["published_at"])
        key = local_dt.date().isocalendar()[:2]
        if key in week_counts:  # always true given the query range, but safe
            lang = a.get("language")
            if lang in LANGUAGES:
                week_counts[key][lang] += 1
            week_counts[key]["total"] += 1

    buckets = []
    for key in week_order:
        min_date, max_date = week_date_span[key]
        range_from, _ = _day_bounds_utc(min_date)
        _, range_to = _day_bounds_utc(max_date)
        buckets.append({
            "label": f"Week {key[1]}",
            **week_counts[key],
            "date_from": range_from.isoformat(),
            "date_to": range_to.isoformat(),
        })

    return {
        "range": "month",
        "period_label": f"{calendar.month_name[month]} {year}",
        "buckets": buckets,
    }