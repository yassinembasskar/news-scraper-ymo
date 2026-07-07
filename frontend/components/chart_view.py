"""
Chart view: range picker (day/week/month) with period navigation, and the
stacked en/fr/ar bar chart. Clicking a bar stores its date_from/date_to in
session_state — article_list.py reads that to show the matching articles.
This component never applies a keyword filter; that only affects the list
below, per the design.
"""

from datetime import date

import plotly.graph_objects as go
import streamlit as st

import api_client

LANGUAGE_COLORS = {"en": "#4C78A8", "fr": "#F58518", "ar": "#54A24B"}
LANGUAGE_LABELS = {"en": "English", "fr": "French", "ar": "Arabic"}


def render():
    st.subheader("Article activity")

    range_type = st.radio("Range", ["Day", "Week", "Month"], horizontal=True, key="range_type")

    if range_type == "Day":
        chart_data = _get_day_chart()
    elif range_type == "Week":
        chart_data = _get_week_chart()
    else:
        chart_data = _get_month_chart()

    if chart_data is None:
        return

    _render_chart(chart_data)


def _get_day_chart():
    selected_date = st.date_input("Day", value=date.today(), key="day_picker")
    try:
        return api_client.get_day_chart(selected_date.isoformat())
    except Exception as e:
        st.error(f"Could not load chart: {e}")
        return None


def _get_week_chart():
    today = date.today()
    default_year, default_week, _ = today.isocalendar()

    col1, col2 = st.columns(2)
    iso_year = col1.number_input("ISO year", min_value=2020, max_value=2100, value=default_year, key="week_year")
    iso_week = col2.number_input("ISO week", min_value=1, max_value=53, value=default_week, key="week_number")

    try:
        return api_client.get_week_chart(int(iso_year), int(iso_week))
    except Exception as e:
        st.error(f"Could not load chart: {e}")
        return None


def _get_month_chart():
    today = date.today()

    col1, col2 = st.columns(2)
    year = col1.number_input("Year", min_value=2020, max_value=2100, value=today.year, key="month_year")
    month = col2.selectbox(
        "Month",
        options=list(range(1, 13)),
        index=today.month - 1,
        format_func=lambda m: date(2000, m, 1).strftime("%B"),
        key="month_number",
    )

    try:
        return api_client.get_month_chart(int(year), int(month))
    except Exception as e:
        st.error(f"Could not load chart: {e}")
        return None


def _render_chart(chart_data: dict):
    buckets = chart_data["buckets"]
    labels = [b["label"] for b in buckets]

    fig = go.Figure()
    for lang in ["en", "fr", "ar"]:
        fig.add_trace(go.Bar(
            name=LANGUAGE_LABELS[lang],
            x=labels,
            y=[b[lang] for b in buckets],
            marker_color=LANGUAGE_COLORS[lang],
        ))

    fig.update_layout(
        barmode="stack",
        title=chart_data["period_label"],
        yaxis_title="Articles",
        legend_title="Language",
    )

    event = st.plotly_chart(fig, on_select="rerun", key="activity_chart", width="stretch")

    selected_points = event["selection"]["points"] if event and event.get("selection") else []
    if selected_points:
        clicked_label = selected_points[0]["x"]
        clicked_bucket = next((b for b in buckets if b["label"] == clicked_label), None)
        if clicked_bucket:
            st.session_state["selected_bucket"] = clicked_bucket