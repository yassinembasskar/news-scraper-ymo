"""
Daily LLM Insight reports — calendar view.

A month calendar where each day that has a generated report is marked (🔴
with a count when it found relevant insights, ✅ when it ran but found
nothing). Clicking a day shows, below the calendar:
  - its insight cards, if the report has any;
  - a "no relevant insights this day" message, if it was generated empty;
  - a "Generate report" button, if it's never been generated.

Generation is a background job (a full day can take several minutes on CPU —
one LLM pass per high-relativity article), so triggering it shows a live
progress bar and the page is reloaded to pick up the finished report.
Reached via the "📄 Reports" link on the dashboard; routed through app.py's
st.navigation, same as the article-detail page.
"""

import calendar as _calendar
from datetime import date

import streamlit as st

import api_client

st.page_link("views/dashboard.py", label="← Back to dashboard")

st.title("📄 Daily Insight Reports")

# Effect → Streamlit color name, for the colored badge on each insight card.
_EFFECT_COLOR = {"negative": "red", "positive": "green", "neutral": "gray"}
_WEEKDAY_HEADERS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _init_month():
    today = date.today()
    st.session_state.setdefault("report_cal_year", today.year)
    st.session_state.setdefault("report_cal_month", today.month)


def _shift_month(delta: int):
    """Moves the displayed month by delta (±1), rolling the year over."""
    y = st.session_state["report_cal_year"]
    m = st.session_state["report_cal_month"] + delta
    if m < 1:
        m, y = 12, y - 1
    elif m > 12:
        m, y = 1, y + 1
    st.session_state["report_cal_year"] = y
    st.session_state["report_cal_month"] = m
    # A month change shouldn't keep a now-off-screen day selected.
    st.session_state.pop("selected_report_date", None)


def _render_generation_banner():
    """If a generation run is in flight, show a progress bar + stop button at
    the top. Returns the status dict so callers know which day (if any) is
    currently running."""
    try:
        status = api_client.get_report_generate_status()
    except Exception:
        return {"in_progress": False}

    if not status.get("in_progress"):
        return status

    total = status.get("total", 0)
    processed = status.get("processed", 0)
    fraction = (processed / total) if total else 0.0
    percent = round(fraction * 100)
    running_date = status.get("report_date") or "?"
    eta = status.get("estimated_minutes_remaining")
    eta_text = f" — about {eta:g} min left." if eta is not None else ""

    st.progress(fraction)
    st.info(
        f"Generating report for **{running_date}**: {processed}/{total} "
        f"articles ({percent}%).{eta_text} Reload to refresh progress."
    )
    if st.button("🛑 Stop generation"):
        try:
            api_client.stop_report_generation()
        except Exception as e:
            st.error(f"Could not stop: {e}")
        else:
            st.rerun()
    return status


def _render_calendar(year: int, month: int, marked: dict):
    """Draws the month grid. `marked` maps 'YYYY-MM-DD' -> summary dict for
    days that have a report. Clicking any day selects it."""
    nav_prev, nav_label, nav_next = st.columns([1, 3, 1])
    if nav_prev.button("◀ Prev"):
        _shift_month(-1)
        st.rerun()
    nav_label.markdown(
        f"<h3 style='text-align:center;margin:0'>{_calendar.month_name[month]} {year}</h3>",
        unsafe_allow_html=True,
    )
    if nav_next.button("Next ▶"):
        _shift_month(1)
        st.rerun()

    header_cols = st.columns(7)
    for col, name in zip(header_cols, _WEEKDAY_HEADERS):
        col.markdown(f"**{name}**")

    for week in _calendar.Calendar().monthdayscalendar(year, month):
        cols = st.columns(7)
        for col, day in zip(cols, week):
            if day == 0:
                col.write("")  # padding day from an adjacent month
                continue
            date_str = f"{year:04d}-{month:02d}-{day:02d}"
            summary = marked.get(date_str)
            candidates = summary["candidate_count"] if summary else 0
            # The button is just the day number so it stays big and readable;
            # the marker/count goes in a small caption underneath. Report
            # marker takes priority; otherwise show how many candidate
            # articles (score >= threshold) the day has, so the workload is
            # visible before generating.
            if summary and summary["has_report"]:
                marker = f"🔴 {summary['relevant_count']}" if summary["relevant_count"] > 0 else "✅"
            elif candidates > 0:
                marker = f"· {candidates}"
            else:
                marker = "&nbsp;"  # keep row height even on empty days
            if col.button(str(day), key=f"calday_{date_str}", use_container_width=True):
                st.session_state["selected_report_date"] = date_str
                st.rerun()
            col.markdown(
                f"<div style='text-align:center;font-size:0.75rem;color:#888;margin-top:-6px'>{marker}</div>",
                unsafe_allow_html=True,
            )


def _render_insight_card(insight: dict):
    effect = insight.get("effect", "neutral")
    color = _EFFECT_COLOR.get(effect, "gray")
    with st.container(border=True):
        st.markdown(f"**{insight.get('title', '')}**")
        st.markdown(
            f":{color}[**{effect.upper()}**] · "
            f"`{insight.get('concerned_param', '—')}` · "
            f"confidence **{insight.get('confidence', 0)}%**"
        )
        action = insight.get("recommended_action")
        if action:
            st.markdown(f"🛠️ {action}")
        st.caption(f"Source: {insight.get('source_title', '')}")


def _render_selected_day(generation_status: dict, marked: dict, min_score: int):
    """Renders the detail panel for whichever day is selected."""
    date_str = st.session_state.get("selected_report_date")
    if not date_str:
        st.info("Pick a day on the calendar to see or generate its report.")
        return

    st.subheader(f"Report — {date_str}")

    candidate_count = (marked.get(date_str) or {}).get("candidate_count", 0)
    st.caption(f"{candidate_count} article(s) scored ≥ {min_score}% relativity on this day.")

    try:
        report = api_client.get_daily_report(date_str)
    except Exception as e:
        st.error(f"Could not load report: {e}")
        return

    # This exact day is being generated right now.
    if generation_status.get("in_progress") and generation_status.get("report_date") == date_str:
        st.info("This day's report is being generated right now — see the progress above.")
        return

    if not report.get("generated"):
        st.write("No report has been generated for this day yet.")
        if generation_status.get("in_progress"):
            st.warning(
                "Another day's report is currently generating — only one runs "
                "at a time. Try again once it finishes."
            )
            return
        if st.button("⚙️ Generate report", type="primary"):
            try:
                result = api_client.generate_daily_report(date_str)
            except Exception as e:
                st.error(f"Could not start generation: {e}")
            else:
                status = result.get("status")
                msg = result.get("message", "")
                if status == "started":
                    st.success(msg)
                elif status == "already_running":
                    st.warning(msg)
                else:
                    st.error(msg)
                st.rerun()
        return

    # Generated. Either it has insights (cards) or it doesn't (message).
    insights = report.get("insights", [])
    st.caption(
        f"Generated {(report.get('generated_at') or '').replace('T', ' ')[:19]} · "
        f"{report.get('articles_processed', 0)} article(s) processed · "
        f"{report.get('relevant_count', 0)} relevant"
    )

    if not insights:
        st.info("There are no relevant insights for this day.")
    else:
        for insight in insights:
            _render_insight_card(insight)

    if st.button("🔄 Regenerate this day"):
        try:
            result = api_client.generate_daily_report(date_str)
        except Exception as e:
            st.error(f"Could not start regeneration: {e}")
        else:
            status = result.get("status")
            if status == "started":
                st.success(result.get("message", ""))
            else:
                st.warning(result.get("message", ""))
            st.rerun()


_init_month()

year = st.session_state["report_cal_year"]
month = st.session_state["report_cal_month"]

generation_status = _render_generation_banner()

min_score = 60
try:
    calendar_data = api_client.get_report_calendar(year, month)
    marked = {d["report_date"]: d for d in calendar_data.get("days", [])}
    min_score = calendar_data.get("min_relativity_score", 60)
except Exception as e:
    st.error(f"Could not load the report calendar: {e}")
    marked = {}

_render_calendar(year, month, marked)
st.caption(
    f"🔴 N = relevant insights found · ✅ = generated, none relevant · "
    f"· N = articles scored ≥ {min_score}% awaiting a report"
)
st.divider()
_render_selected_day(generation_status, marked, min_score)
