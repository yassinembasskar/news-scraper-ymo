"""
Overview bar: today/month totals + top keywords, last scrape time, a
per-stage status breakdown under each pipeline button (see
_show_pipeline_stats()), and the manual "Update", "Extract articles",
"Embed articles", and "Relativity scoring" buttons (all three of the
latter become "Stop extraction"/"Stop embedding"/"Stop scoring" while
their run is in progress). Calls GET /stats/overview, GET /stats/pipeline,
POST /update, POST /articles/extract, GET /articles/extract/status, POST
/articles/extract/stop, POST /articles/embed, GET /articles/embed/status,
POST /articles/embed/stop, POST /articles/score, GET /articles/score/status,
and POST /articles/score/stop via api_client — no direct HTTP calls here.
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
    if "stop_extraction_result" in st.session_state:
        _show_stop_extraction_result(st.session_state.pop("stop_extraction_result"))
    if "embedding_result" in st.session_state:
        _show_embedding_result(st.session_state.pop("embedding_result"))
    if "stop_embedding_result" in st.session_state:
        _show_stop_embedding_result(st.session_state.pop("stop_embedding_result"))
    if "scoring_result" in st.session_state:
        _show_scoring_result(st.session_state.pop("scoring_result"))
    if "stop_scoring_result" in st.session_state:
        _show_stop_scoring_result(st.session_state.pop("stop_scoring_result"))

    try:
        extraction_status = api_client.get_extraction_run_status()
    except Exception:
        extraction_status = {"in_progress": False}
    extraction_running = extraction_status.get("in_progress", False)

    try:
        embedding_status = api_client.get_embedding_run_status()
    except Exception:
        embedding_status = {"in_progress": False}
    embedding_running = embedding_status.get("in_progress", False)

    try:
        scoring_status = api_client.get_scoring_run_status()
    except Exception:
        scoring_status = {"in_progress": False}
    scoring_running = scoring_status.get("in_progress", False)

    try:
        pipeline_stats = api_client.get_pipeline_stats()
    except Exception:
        pipeline_stats = None

    _show_extraction_progress(extraction_status)
    _show_embedding_progress(embedding_status)
    _show_scoring_progress(scoring_status)

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

    col_update, col_extract, col_embed, col_score = st.columns(4)

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

    if extraction_running:
        if col_extract.button("🛑 Stop extraction"):
            try:
                result = api_client.stop_extraction()
            except Exception as e:
                st.session_state["stop_extraction_result"] = {
                    "status": "error",
                    "message": f"Request to backend failed: {e}",
                }
            else:
                st.session_state["stop_extraction_result"] = result
            st.rerun()
    elif col_extract.button("🧹 Extract articles"):
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

    if scoring_running:
        if col_score.button("🛑 Stop scoring"):
            try:
                result = api_client.stop_scoring()
            except Exception as e:
                st.session_state["stop_scoring_result"] = {
                    "status": "error",
                    "message": f"Request to backend failed: {e}",
                }
            else:
                st.session_state["stop_scoring_result"] = result
            st.rerun()
    elif col_score.button("🎯 Relativity scoring"):
        try:
            result = api_client.trigger_scoring()
        except Exception as e:
            st.session_state["scoring_result"] = {
                "status": "error",
                "message": f"Request to backend failed: {e}",
            }
        else:
            st.session_state["scoring_result"] = result
        st.rerun()

    _show_pipeline_stats(pipeline_stats, col_extract, col_embed, col_score)


def _show_pipeline_stats(stats: dict, col_extract, col_embed, col_score):
    """Small status-breakdown line under each pipeline button — each
    stage's "X out of Y" is scoped to that stage's own candidate pool
    (see services/stats.py's get_pipeline_stats() docstring), not the
    whole article collection. Silently does nothing if the stats request
    failed (e.g. backend down) rather than erroring out the whole bar."""
    if not stats:
        return

    extraction = stats.get("extraction", {})
    embedding = stats.get("embedding", {})
    scoring = stats.get("scoring", {})

    col_extract.caption(
        f"{extraction.get('successful', 0)}/{extraction.get('total', 0)} extracted — "
        f"❌ {extraction.get('failed', 0)} failed · "
        f"⌛ {extraction.get('expired', 0)} expired · "
        f"⬜ {extraction.get('not_extracted', 0)} not extracted"
    )
    col_embed.caption(
        f"{embedding.get('embedded', 0)}/{embedding.get('total', 0)} embedded "
        f"(of successful extractions) — "
        f"❌ {embedding.get('failed', 0)} failed · "
        f"⬜ {embedding.get('not_embedded', 0)} not embedded"
    )
    col_score.caption(
        f"{scoring.get('scored', 0)}/{scoring.get('total', 0)} scored "
        f"(of embedded) — "
        f"❌ {scoring.get('failed', 0)} failed · "
        f"⬜ {scoring.get('not_scored', 0)} not scored"
    )


def _format_duration(total_minutes) -> str:
    """Converts a raw minutes figure (as returned by every
    estimated_minutes_remaining field) into a practical "1d 14h 1min" /
    "10h 15min" / "20min" string — a bare "127.3 min" isn't something
    anyone reads at a glance once a run's ETA stretches past an hour or
    two, which the CPU-bound embedding/scoring jobs routinely do. Any
    zero-valued unit is dropped entirely (10h 0min -> "10h", not "0d 10h
    0min"), except minutes when every unit is zero (nothing left)."""
    whole_minutes = round(max(total_minutes, 0))
    days, remainder = divmod(whole_minutes, 24 * 60)
    hours, minutes = divmod(remainder, 60)

    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes or not parts:
        parts.append(f"{minutes}min")
    return " ".join(parts)


def _show_extraction_result(result: dict):
    status = result.get("status")
    message = result.get("message", "")

    if status in ("started", "skipped"):
        st.info(message)
    elif status == "already_running":
        st.warning(message)
    else:
        st.error(message)


def _show_extraction_progress(status: dict):
    """Shows a progress bar while an extraction run is in flight — mirrors
    _show_embedding_progress() exactly."""
    if not status.get("in_progress"):
        return

    total = status.get("total", 0)
    processed = status.get("processed", 0)
    fraction = (processed / total) if total else 0.0
    percent = round(fraction * 100)

    eta = status.get("estimated_minutes_remaining")
    eta_text = f" — expected to finish in about {_format_duration(eta)}." if eta is not None else ""

    st.progress(fraction)
    st.info(f"Extraction in progress: {processed}/{total} articles ({percent}%).{eta_text} Reload to see progress.")


def _show_stop_extraction_result(result: dict):
    status = result.get("status")
    message = result.get("message", "")

    if status == "stopping":
        st.info(message)
    elif status == "not_running":
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
    eta_text = f" — expected to finish in about {_format_duration(eta)}." if eta is not None else ""

    st.progress(fraction)
    st.info(f"Embedding in progress: {processed}/{total} articles ({percent}%).{eta_text} Reload to see progress.")


def _show_embedding_result(result: dict):
    status = result.get("status")
    message = result.get("message", "")

    if status in ("started", "skipped"):
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


def _show_scoring_progress(status: dict):
    """Shows a progress bar while a scoring run is in flight — mirrors
    _show_embedding_progress() exactly."""
    if not status.get("in_progress"):
        return

    total = status.get("total", 0)
    processed = status.get("processed", 0)
    fraction = (processed / total) if total else 0.0
    percent = round(fraction * 100)

    eta = status.get("estimated_minutes_remaining")
    eta_text = f" — expected to finish in about {_format_duration(eta)}." if eta is not None else ""

    st.progress(fraction)
    st.info(f"Scoring in progress: {processed}/{total} articles ({percent}%).{eta_text} Reload to see progress.")


def _show_scoring_result(result: dict):
    status = result.get("status")
    message = result.get("message", "")

    if status in ("started", "skipped"):
        st.info(message)
    elif status == "already_running":
        st.warning(message)
    else:
        st.error(message)


def _show_stop_scoring_result(result: dict):
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

