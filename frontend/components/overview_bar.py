"""
Overview bar: today/month totals + top keywords, last scrape time, and the
manual "Update" button. Calls GET /stats/overview and POST /update via
api_client — no direct HTTP calls here.
"""

import streamlit as st

import api_client


def render():
    st.subheader("Overview")

    # If an update just ran (see button handler below), show its result
    # once, then clear it — otherwise it would reappear on every rerun.
    if "update_result" in st.session_state:
        _show_update_result(st.session_state.pop("update_result"))

    try:
        overview = api_client.get_overview()
    except Exception as e:
        st.error(f"Could not load overview: {e}")
        overview = None

    col1, col2, col3, col4, col5 = st.columns(5)

    if overview:
        col1.metric("Articles today", overview["today_total"])
        col2.metric("Top keyword today", overview["today_top_keyword"] or "—")
        col3.metric("Articles this month", overview["month_total"])
        col4.metric("Top keyword this month", overview["month_top_keyword"] or "—")
        col5.metric("Last updated", _format_last_scraped(overview.get("last_scraped")))

    if st.button("🔄 Update now"):
        with st.spinner("Fetching latest news — this may take a minute..."):
            try:
                result = api_client.trigger_update()
            except Exception as e:
                st.session_state["update_result"] = {
                    "status": "failed",
                    "message": f"Request to backend failed: {e}",
                }
            else:
                st.session_state["update_result"] = result
        st.rerun()


def _format_last_scraped(value):
    if not value:
        return "Never"
    # value is an ISO datetime string from the backend
    return value.replace("T", " ").split(".")[0]


def _show_update_result(result: dict):
    status = result.get("status")
    message = result.get("message", "")

    if status == "success":
        st.success(message)
    elif status == "partial":
        st.warning(message)
    elif status == "failed":
        st.error(message)
    else:  # skipped
        st.info(message)