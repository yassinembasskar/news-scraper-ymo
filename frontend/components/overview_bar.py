"""
Overview bar: today/month totals + top keywords, last scrape time, and the
manual "Update", "Extract articles", and "Embed articles" buttons (the last
one becomes "Stop embedding" while a run is in progress). Calls
GET /stats/overview, POST /update, POST /articles/extract,
POST /articles/embed, GET /articles/embed/status, and
POST /articles/embed/stop via api_client — no direct HTTP calls here.
"""

import streamlit as st

import api_client


def render():
    st.subheader("Overview")

    # If an update/extraction just ran (see button handlers below), show
    # its result once, then clear it — otherwise it would reappear on
    # every rerun.
    if "update_result" in st.session_state:
        _show_update_result(st.session_state.pop("update_result"))
    if "extraction_result" in st.session_state:
        _show_extraction_result(st.session_state.pop("extraction_result"))
    if "embedding_result" in st.session_state:
        _show_embedding_result(st.session_state.pop("embedding_result"))
    if "stop_embedding_result" in st.session_state:
        _show_stop_embedding_result(st.session_state.pop("stop_embedding_result"))

    try:
        embedding_status = api_client.get_embedding_run_status()
    except Exception:
        embedding_status = {"in_progress": False}
    embedding_running = embedding_status.get("in_progress", False)

    _show_embedding_progress(embedding_status)

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

    col_update, col_extract, col_embed = st.columns(3)

    if col_update.button("🔄 Update now"):
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

    if col_extract.button("🧹 Extract articles"):
        try:
            result = api_client.trigger_extraction()
        except Exception as e:
            st.session_state["extraction_result"] = {
                "status": "error",
                "message": f"Request to backend failed: {e}",
            }
        else:
            st.session_state["extraction_result"] = result
        st.rerun()

    if embedding_running:
        if col_embed.button("🛑 Stop embedding"):
            try:
                result = api_client.stop_embedding()
            except Exception as e:
                st.session_state["stop_embedding_result"] = {
                    "status": "error",
                    "message": f"Request to backend failed: {e}",
                }
            else:
                st.session_state["stop_embedding_result"] = result
            st.rerun()
    elif col_embed.button("🧠 Embed articles"):
        try:
            result = api_client.trigger_embedding()
        except Exception as e:
            st.session_state["embedding_result"] = {
                "status": "error",
                "message": f"Request to backend failed: {e}",
            }
        else:
            st.session_state["embedding_result"] = result
        st.rerun()


def _show_extraction_result(result: dict):
    status = result.get("status")
    message = result.get("message", "")

    if status == "started":
        st.info(message)
    elif status == "already_running":
        st.warning(message)
    else:
        st.error(message)


def _show_embedding_progress(status: dict):
    """Shows a progress bar while an embedding run is in flight. Takes the
    status already fetched in render() rather than querying again."""
    if not status.get("in_progress"):
        return

    total = status.get("total", 0)
    processed = status.get("processed", 0)
    fraction = (processed / total) if total else 0.0
    percent = round(fraction * 100)

    eta = status.get("estimated_minutes_remaining")
    eta_text = f" — expected to finish in about {eta:g} min." if eta is not None else ""

    st.progress(fraction)
    st.info(f"Embedding in progress: {processed}/{total} articles ({percent}%).{eta_text} Reload to see progress.")


def _show_embedding_result(result: dict):
    status = result.get("status")
    message = result.get("message", "")

    if status == "started":
        st.info(message)
    elif status == "already_running":
        st.warning(message)
    else:
        st.error(message)


def _show_stop_embedding_result(result: dict):
    status = result.get("status")
    message = result.get("message", "")

    if status == "stopping":
        st.info(message)
    elif status == "not_running":
        st.warning(message)
    else:
        st.error(message)

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

