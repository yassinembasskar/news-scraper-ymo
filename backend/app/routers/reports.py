"""
Router: daily LLM Insight reports — the calendar page's backend.

Four endpoints:
  - GET  /reports/calendar    -> per-day markers for a month (which days have
                                 a generated report, and how many relevant
                                 insights each has).
  - GET  /reports/{date}      -> one day's full report (the insight cards),
                                 or a "not generated yet" marker.
  - POST /reports/{date}/generate -> kick off generation for that day as a
                                 background task (long-running: the LLM
                                 processes each of the day's high-relativity
                                 articles one at a time).
  - GET  /reports/generate/status -> poll live progress + ETA, same shape as
                                 the other stages' status endpoints.

Generation mirrors the embedding/scoring stages' background-task pattern —
a full day can take many minutes on CPU, far too long to block an HTTP
request on.
"""

from datetime import date as date_type
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel

from app import repository
from app.models import ReportInsight
from app.services import llm_insight, report_job

router = APIRouter(prefix="/reports")


class CalendarDay(BaseModel):
    report_date: str
    candidate_count: int  # articles scored >= MIN_RELATIVITY_SCORE that day
    has_report: bool
    relevant_count: int
    articles_processed: int


class CalendarResponse(BaseModel):
    days: List[CalendarDay]
    min_relativity_score: int


class DailyReportResponse(BaseModel):
    report_date: str
    generated: bool  # False -> never run for this day (show the Generate button)
    generated_at: Optional[str] = None
    articles_processed: int = 0
    relevant_count: int = 0
    insights: List[ReportInsight] = []


class GenerateTriggerResponse(BaseModel):
    status: str  # "started" | "already_running" | "unavailable" | "stopping" | "not_running"
    message: str


class GenerateStatusResponse(BaseModel):
    in_progress: bool
    report_date: Optional[str] = None
    total: int
    processed: int
    estimated_minutes_remaining: Optional[float] = None


@router.get("/calendar", response_model=CalendarResponse)
def get_calendar(year: int, month: int):
    """Per-day info for one calendar month: how many report-candidate
    articles each day has (scored >= MIN_RELATIVITY_SCORE, non-expired), and
    — for days already generated — the report's relevant-insight count. A day
    appears if it has either candidates or a report; a day with neither is
    absent and the frontend renders it plain."""
    start = f"{year:04d}-{month:02d}-01"
    end = f"{year:04d}-{month:02d}-31"  # lexical upper bound; 32+ never exist
    summaries = {s["report_date"]: s for s in repository.get_daily_reports_in_range(start, end)}
    candidates = repository.get_candidate_counts_for_month(
        year, month, report_job.MIN_RELATIVITY_SCORE
    )

    days = []
    for report_date in sorted(set(summaries) | set(candidates)):
        summary = summaries.get(report_date)
        days.append(
            CalendarDay(
                report_date=report_date,
                candidate_count=candidates.get(report_date, 0),
                has_report=summary is not None,
                relevant_count=summary["relevant_count"] if summary else 0,
                articles_processed=summary["articles_processed"] if summary else 0,
            )
        )
    return CalendarResponse(days=days, min_relativity_score=report_job.MIN_RELATIVITY_SCORE)


@router.get("/generate/status", response_model=GenerateStatusResponse)
def get_generate_status():
    """Live progress for the report run currently in flight (or the last
    one). Same recency-weighted ETA as the other stages' status endpoints.
    Declared before /{date} so 'generate' can't be captured as a date."""
    status = repository.get_report_status()
    if status is None:
        return GenerateStatusResponse(in_progress=False, total=0, processed=0)

    eta_minutes = None
    if status.in_progress and status.avg_seconds_per_article is not None:
        remaining = max(status.total - status.processed, 0)
        eta_minutes = round((status.avg_seconds_per_article * remaining) / 60, 1)

    return GenerateStatusResponse(
        in_progress=status.in_progress,
        report_date=status.report_date,
        total=status.total,
        processed=status.processed,
        estimated_minutes_remaining=eta_minutes,
    )


@router.post("/generate/stop", response_model=GenerateTriggerResponse)
def stop_generation():
    """Requests that the current run stop after the article it's on right
    now finishes — same cooperative stop as the other stages. Whatever
    insights were already collected are still saved."""
    if report_job.request_report_stop():
        return GenerateTriggerResponse(
            status="stopping",
            message=(
                "Stop requested. The current article will finish, then the "
                "run will end and save whatever it found so far."
            ),
        )
    return GenerateTriggerResponse(
        status="not_running",
        message="No report generation is currently in progress.",
    )


@router.get("/{report_date}", response_model=DailyReportResponse)
def get_report(report_date: str):
    """One day's report. `generated` is False when the day has never been
    run (frontend shows the Generate button); True with insights == [] means
    it was run and nothing was relevant (frontend shows the "no insights"
    message)."""
    report = repository.get_daily_report(report_date)
    if report is None:
        return DailyReportResponse(report_date=report_date, generated=False)
    return DailyReportResponse(
        report_date=report.report_date,
        generated=True,
        generated_at=report.generated_at.isoformat(),
        articles_processed=report.articles_processed,
        relevant_count=report.relevant_count,
        insights=report.insights,
    )


@router.post("/{report_date}/generate", response_model=GenerateTriggerResponse)
def generate_report(report_date: str, background_tasks: BackgroundTasks):
    """Starts (or restarts) generation for one day in the background. Refuses
    if any pipeline stage is already running (report generation shares the
    single-stage guard), or if Ollama isn't reachable — better to say so
    immediately than to start a job that fails on its first article."""
    # Validate the date shape early so a typo can't create a garbage report.
    try:
        date_type.fromisoformat(report_date)
    except ValueError:
        return GenerateTriggerResponse(
            status="unavailable",
            message=f"'{report_date}' is not a valid YYYY-MM-DD date.",
        )

    if report_job.is_report_running():
        return GenerateTriggerResponse(
            status="already_running",
            message="A report is already being generated. Reload in a bit to check progress.",
        )

    other_stage = repository.get_running_pipeline_stage()
    if other_stage:
        return GenerateTriggerResponse(
            status="already_running",
            message=(
                f"A {other_stage} run is currently in progress — report "
                "generation doesn't run at the same time as the other stages "
                "(they'd fight each other for CPU). Try again once it finishes."
            ),
        )

    if not llm_insight.ollama_available():
        return GenerateTriggerResponse(
            status="unavailable",
            message=(
                "The LLM service (Ollama) isn't reachable right now. Make sure "
                "the ollama container is up and the model has finished "
                "downloading, then try again."
            ),
        )

    background_tasks.add_task(report_job.run_report_job, report_date)
    return GenerateTriggerResponse(
        status="started",
        message=(
            f"Report generation started for {report_date}. This can take a "
            "few minutes — reload the page to see progress and, once done, "
            "the insights."
        ),
    )
