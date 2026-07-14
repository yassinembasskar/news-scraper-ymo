"""
Below-the-chart panel: bucket summary (total, top keyword, per-language
counts) for whichever bar was clicked in chart_view, plus a keyword filter
and the resulting article list. The keyword filter only ever changes the
list below — it never affects the chart or the summary numbers, since it's
applied client-side to this component's own /articles call, not to the
stats endpoints.
"""

import streamlit as st

import api_client


def render():
    bucket = st.session_state.get("selected_bucket")

    if bucket is None:
        st.info("Click a bar in the chart above to see articles for that period.")
        return

    st.subheader(f"Details — {bucket['label']}")

    try:
        summary = api_client.get_bucket_summary(bucket["date_from"], bucket["date_to"])
    except Exception as e:
        st.error(f"Could not load summary: {e}")
        return

    lang_counts = summary.get("language_counts", {})
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Total articles", summary["total"])
    col2.metric("Top keyword", summary["top_keyword"] or "—")
    col3.metric("English", lang_counts.get("en", 0))
    col4.metric("French", lang_counts.get("fr", 0))
    col5.metric("Arabic", lang_counts.get("ar", 0))

    keyword_filter = st.text_input("Filter by keyword (optional)", key="article_keyword_filter")

    try:
        articles = api_client.get_articles(
            date_from=bucket["date_from"],
            date_to=bucket["date_to"],
            keyword=keyword_filter or None,
        )
    except Exception as e:
        st.error(f"Could not load articles: {e}")
        return

    if not articles:
        st.write("No articles match this filter.")
        return

    for article in articles:
        lang_tag = (article.get("language") or "").upper()
        published = article.get("published_at", "")
        keywords = ", ".join(article.get("matched_keywords", [])) or "—"
        source = article.get("source_name") or "Unknown source"
        ingestion_status = article.get("ingestion_status", "No Extraction")

        st.markdown(
            f"**[{article['title']}]({article['url']})**  \n"
            f"{lang_tag} · {published} · {source} · Extraction: {ingestion_status}  \n"
            f"Keywords: {keywords}"
        )

        if ingestion_status == "Failed":
            _render_retry_button(article)
            _render_manual_content_form(article)

        st.divider()


def _render_manual_content_form(article: dict):
    """Lets the user paste an article's content by hand when automatic
    extraction failed — saved via PUT /articles/{id}/content, which stores
    it exactly as a successful extraction would."""
    article_id = article["id"]
    with st.expander("Paste content manually"):
        pasted = st.text_area("Article content", key=f"manual_content_{article_id}")
        if st.button("Save", key=f"manual_content_save_{article_id}"):
            if not pasted.strip():
                st.warning("Paste some content first.")
            else:
                try:
                    api_client.submit_manual_content(article_id, pasted)
                except Exception as e:
                    st.error(f"Could not save content: {e}")
                else:
                    st.success("Content saved.")
                    st.rerun()

def _render_retry_button(article: dict):
    """Lets the user retry automatic extraction for one Failed article,
    as a lighter alternative to pasting content by hand."""
    article_id = article["id"]
    if st.button("🔁 Retry automatic extraction", key=f"retry_extract_{article_id}"):
        with st.spinner("Retrying extraction..."):
            try:
                result = api_client.retry_article_extraction(article_id)
            except Exception as e:
                st.error(f"Retry failed: {e}")
            else:
                status = result.get("status")
                if status == "Successful":
                    st.success("Extraction succeeded.")
                    st.rerun()
                elif status == "already_running":
                    st.warning(result.get("message"))
                elif status == "not_found":
                    st.error("Article no longer exists.")
                else:
                    st.error(result.get("message", "Extraction failed again."))