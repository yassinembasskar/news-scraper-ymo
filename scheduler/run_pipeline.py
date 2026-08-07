"""
Daily pipeline orchestrator.

Runs the full pipeline once a day at PIPELINE_HOUR_UTC:00 UTC, in order:
    Update -> Extract -> Embed -> Score -> Report

Each stage is triggered via the same HTTP endpoints the Streamlit
dashboard's buttons call, then polled until it's no longer in_progress
before moving to the next stage -- deliberately serial, matching how a
human clicking the buttons one at a time would drive it. The backend
itself already refuses to run two heavy stages at once (see
repository.get_running_pipeline_stage()), so this script isn't the thing
enforcing that; it's just what decides *when* to ask for each stage next.

No cron, no external scheduler library -- just a sleep-until-next-2am
loop. That's deliberately the simplest thing that works for "one job,
once a day": easy to read, nothing to configure, and it survives a
container restart cleanly (it just recomputes the next 2am and waits
again; a restart mid-pipeline is equivalent to the pipeline having been
interrupted, same as if someone had stopped it via the dashboard --
whichever stage was running gets picked up fresh on the *next* scheduled
run, since every stage here is itself resumable/incremental).
"""

import os
import time
from datetime import datetime, timedelta, timezone

import requests

BACKEND_URL = os.environ.get("BACKEND_API_URL", "http://backend:8000")
PIPELINE_HOUR_UTC = int(os.environ.get("PIPELINE_HOUR_UTC", "2"))
POLL_INTERVAL_SECONDS = 15
# Generous per-request timeout -- these are trigger/status calls, not the
# actual (much longer) background work, but /update runs synchronously
# and can legitimately take a couple of minutes.
REQUEST_TIMEOUT_SECONDS = 180


def _log(message: str) -> None:
    print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] {message}", flush=True)


def _wait_until_idle(status_path: str) -> None:
    """Polls a stage's status endpoint until in_progress is False."""
    while True:
        response = requests.get(f"{BACKEND_URL}{status_path}", timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        if not response.json().get("in_progress"):
            return
        time.sleep(POLL_INTERVAL_SECONDS)


def _run_stage(name: str, trigger_method: str, trigger_path: str, status_path: str = None) -> None:
    """Triggers one stage and, if it actually started a background run,
    waits for it to finish before returning. Stages with no status_path
    (just /update) run synchronously on the backend and are done as soon
    as the trigger call returns."""
    _log(f"{name}: triggering {trigger_method} {trigger_path}")
    response = requests.request(trigger_method, f"{BACKEND_URL}{trigger_path}", timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    result = response.json()
    _log(f"{name}: {result}")

    if status_path and result.get("status") == "started":
        _wait_until_idle(status_path)

    _log(f"{name}: done")


def run_pipeline() -> None:
    _log("=== Daily pipeline starting ===")
    try:
        _run_stage("Update", "POST", "/update")
        _run_stage("Extract", "POST", "/articles/extract", "/articles/extract/status")
        _run_stage("Embed", "POST", "/articles/embed", "/articles/embed/status")
        _run_stage("Score", "POST", "/articles/score", "/articles/score/status")

        # The report covers the day that just finished, not "today" --
        # this runs at PIPELINE_HOUR_UTC (early morning), so "today" has
        # barely started and has almost no articles yet. Yesterday (UTC)
        # is the day every article scraped/extracted/embedded/scored
        # above actually belongs to.
        report_date = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        _run_stage("Report", "POST", f"/reports/{report_date}/generate", "/reports/generate/status")
    except Exception as e:
        # A stage failing shouldn't crash the scheduler loop -- log it
        # and just wait for tomorrow's run rather than exiting the
        # container (which would flip in_progress flags left dangling by
        # a mid-stage failure only on the *next* backend restart, per
        # main.py's lifespan reset).
        _log(f"=== Pipeline FAILED: {e} ===")
        return

    _log("=== Daily pipeline finished ===")


def _seconds_until_next_run() -> float:
    now = datetime.now(timezone.utc)
    target = now.replace(hour=PIPELINE_HOUR_UTC, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


if __name__ == "__main__":
    while True:
        sleep_seconds = _seconds_until_next_run()
        _log(f"Sleeping {sleep_seconds / 3600:.1f}h until next run at {PIPELINE_HOUR_UTC:02d}:00 UTC")
        time.sleep(sleep_seconds)
        run_pipeline()
