"""
Router: dashboard stats.

Thin HTTP wrappers around services/stats.py — no bucketing math or Mongo
calls here, just query-parameter parsing and response shaping.

Chart periods use three separate endpoints (day/week/month) rather than one
endpoint with conditional parameters, so FastAPI can validate each period
type's required params automatically instead of hand-rolled validation.
"""

from datetime import date as date_type
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.services import stats

router = APIRouter(prefix="/stats")


class OverviewResponse(BaseModel):
    today_total: int
    today_top_keyword: Optional[str] = None
    month_total: int
    month_top_keyword: Optional[str] = None
    last_scraped: Optional[datetime] = None


class ChartBucket(BaseModel):
    label: str
    en: int
    fr: int
    ar: int
    total: int
    date_from: str  # UTC ISO string — pass straight through to /articles for drill-down
    date_to: str


class ChartResponse(BaseModel):
    range: str
    period_label: str
    buckets: List[ChartBucket]


class BucketSummaryResponse(BaseModel):
    total: int
    top_keyword: Optional[str] = None
    language_counts: Dict[str, int]


@router.get("/overview", response_model=OverviewResponse)
def overview():
    """Today's total + top keyword, this month's total + top keyword (both
    in Morocco local time), plus the last successful scrape watermark."""
    return stats.get_overview()


@router.get("/chart/day", response_model=ChartResponse)
def chart_day(
    date: date_type = Query(..., description="Morocco-local calendar day, e.g. 2026-07-06"),
):
    """Hourly bars for one Morocco-local calendar day."""
    return stats.get_day_chart(date)


@router.get("/chart/week", response_model=ChartResponse)
def chart_week(
    iso_year: int = Query(..., description="ISO year, e.g. 2026"),
    iso_week: int = Query(..., ge=1, le=53, description="ISO week number, e.g. 27"),
):
    """Daily bars (Mon-Sun) for one ISO week."""
    return stats.get_week_chart(iso_year, iso_week)


@router.get("/chart/month", response_model=ChartResponse)
def chart_month(
    year: int = Query(..., description="Calendar year, e.g. 2026"),
    month: int = Query(..., ge=1, le=12, description="Calendar month, 1-12"),
):
    """Weekly bars for one calendar month. A week overlapping the previous
    or next month is split at the boundary — see services/stats.py."""
    return stats.get_month_chart(year, month)


@router.get("/bucket-summary", response_model=BucketSummaryResponse)
def bucket_summary(
    date_from: datetime = Query(..., description="Start of the range (UTC)"),
    date_to: datetime = Query(..., description="End of the range (UTC)"),
):
    """Total, top keyword, and per-language breakdown for an arbitrary date
    range — used for the panel shown below the chart when a bar is clicked."""
    return stats.get_bucket_summary(date_from, date_to)