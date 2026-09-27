"""
Router: daily LLM Insight reports — the calendar section's backend.

Insight generation is no longer an on-demand, per-day background job: it's
preprocessing's own phase 6 (services/preprocessing.py, via
services/llm_insight.assess_cluster), running automatically over every
contextualized cluster as it becomes ready — the same way condensing,
entity extraction, and contextualization already do. This router is
therefore read-only, computed straight from the `clusters` collection:

  - GET /reports/calendar -> per-day markers for a month (how many
                              clusters that day, how many already have
                              insights generated, how many relevant
                              insights found so far).
  - GET /reports/{date}   -> one day's insights, grouped by the cluster
                              each came from, plus how many of that day's
                              clusters are still queued for phase 6.

There is no "generate"/"stop" here anymore — running preprocessing (title
gate -> clustering -> ... -> insights) is what makes a day's insights
appear; this page only displays what preprocessing has already produced.
"""

from typing import List, Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app import repository

router = APIRouter(prefix="/reports")


class CalendarDay(BaseModel):
    report_date: str
    total_clusters: int
    insights_generated_clusters: int
    relevant_count: int


class CalendarResponse(BaseModel):
    days: List[CalendarDay]


class DailyInsightItem(BaseModel):
    cluster_id: str
    representative_title: str
    title: str
    confidence: int
    concerned_param: str
    effect: Literal["positive", "negative", "neutral"]
    recommended_action: str
    chunk_indices: List[int] = []


class DailyReportResponse(BaseModel):
    report_date: str
    total_clusters: int
    insights_generated_clusters: int
    pending_clusters: int  # contextualized, but preprocessing hasn't reached phase 6 for them yet
    insights: List[DailyInsightItem] = []


@router.get("/calendar", response_model=CalendarResponse)
def get_calendar(year: int, month: int):
    """Per-day info for one calendar month: how many clusters exist, how
    many already went through phase 6, and the relevant-insight count so
    far. A day appears only if it has at least one cluster; a day with
    none is absent and the frontend renders it plain."""
    summary = repository.get_cluster_insight_month_summary(year, month)
    days = [
        CalendarDay(
            report_date=date,
            total_clusters=row["total"],
            insights_generated_clusters=row["insights_generated"],
            relevant_count=row["relevant_count"],
        )
        for date, row in sorted(summary.items())
    ]
    return CalendarResponse(days=days)


@router.get("/{report_date}", response_model=DailyReportResponse)
def get_report(report_date: str):
    """One day's insights, grouped by cluster. `pending_clusters` counts
    clusters that reached contextualization but haven't been through
    insight generation yet — a nonzero value means this day's report is
    still incomplete, not that nothing was found."""
    day_clusters = repository.get_clusters(report_date)
    insights: List[DailyInsightItem] = []
    insights_generated_clusters = 0
    pending_clusters = 0

    for cluster in day_clusters:
        if cluster.insights_generated:
            insights_generated_clusters += 1
            display = repository.get_cluster_display_info(cluster)
            for insight in cluster.insights:
                insights.append(DailyInsightItem(
                    cluster_id=cluster.cluster_id,
                    representative_title=display["representative_title"],
                    title=insight.title,
                    confidence=insight.confidence,
                    concerned_param=insight.concerned_param,
                    effect=insight.effect,
                    recommended_action=insight.recommended_action,
                    chunk_indices=insight.chunk_indices,
                ))
        elif cluster.contextualized:
            pending_clusters += 1

    return DailyReportResponse(
        report_date=report_date,
        total_clusters=len(day_clusters),
        insights_generated_clusters=insights_generated_clusters,
        pending_clusters=pending_clusters,
        insights=insights,
    )
