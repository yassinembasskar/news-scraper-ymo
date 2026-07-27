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

    # Highest relativity_score first; articles with no score yet (None)
    # sort after every scored one, but keep their relative order among
    # themselves (Python's sort is stable) so the list doesn't visibly
    # reshuffle before any scoring has happened.
    articles = sorted(
        articles,
        key=lambda a: (a.get("relativity_score") is None, -(a.get("relativity_score") or 0)),
    )

    try:
        embedding_running = api_client.get_embedding_run_status().get("in_progress", False)
    except Exception:
        embedding_running = False

    try:
        scoring_running = api_client.get_scoring_run_status().get("in_progress", False)
    except Exception:
        scoring_running = False

    for article in articles:
        lang_tag = (article.get("language") or "").upper()
        published = article.get("published_at", "")
        keywords = ", ".join(article.get("matched_keywords", [])) or "—"
        source = article.get("source_name") or "Unknown source"
        ingestion_status = article.get("ingestion_status", "No Extraction")
        embedding_status = article.get("embedding_status", "Not Embedded")
        scored_status = article.get("scored_status", "Not Scored")
        relativity_score = article.get("relativity_score")
        # A run in progress only flips the article it's actively working on
        # to "Pending" in Mongo — the rest of the *eligible* queue still
        # reads "Not Embedded"/"Not Scored" until their turn comes. Relabel
        # those for display only (not written back). Only articles with a
        # successful extraction are ever candidates for embedding (see
        # repository.get_articles_pending_embedding), and only Embedded
        # articles are ever candidates for scoring (see
        # repository.get_articles_pending_scoring) — anything else must
        # keep reading its default status since the job will never touch
        # it, no matter how long it runs.
        if embedding_running and embedding_status == "Not Embedded" and ingestion_status == "Successful":
            embedding_status = "Pending"
        if scoring_running and scored_status == "Not Scored" and embedding_status == "Embedded":
            scored_status = "Pending"

        scoring_line = f"Scoring: {_relativity_display(scored_status, relativity_score)}"

        col_main, col_feedback = st.columns([4, 3])

        with col_main:
            st.markdown(
                f"**[{article['title']}]({article['url']})**  \n"
                f"{lang_tag} · {published} · {source} · Extraction: {ingestion_status} · "
                f"Embedding: {embedding_status} · {scoring_line}  \n"
                f"Keywords: {keywords}"
            )

        with col_feedback:
            if scored_status == "Scored":
                _render_relativity_feedback(article)

        if st.button("See more", key=f"see_more_{article['id']}"):
            st.session_state["selected_article_id"] = article["id"]
            st.switch_page("views/article_detail.py")

        if ingestion_status == "Failed":
            _render_retry_button(article)
            _render_manual_content_form(article)

        st.divider()


# Bands match the calibration target in services/scorer.py's SCORE_POWER
# comment (majority <60, ~30% in 60-79, ~10% >=80) — green only for the
# genuinely high-relativity minority, not the typical article.
RELATIVITY_HIGH_THRESHOLD = 80
RELATIVITY_MEDIUM_THRESHOLD = 60


def _relativity_display(scored_status: str, relativity_score) -> str:
    """Colored markdown span for a scored article's relativity_score, or
    the plain status (grayed out) while it isn't scored yet."""
    if scored_status != "Scored" or relativity_score is None:
        return f":gray[{scored_status}]"

    if relativity_score >= RELATIVITY_HIGH_THRESHOLD:
        color = "green"
    elif relativity_score >= RELATIVITY_MEDIUM_THRESHOLD:
        color = "orange"
    else:
        color = "red"
    return f":{color}[**{relativity_score}**]"


def _render_relativity_feedback(article: dict):
    """"More relative" / "less relative" / "rescore" controls for a scored
    article. The two feedback buttons each nudge the threshold of every
    relevance-tree node this article's scoring run triggered (weighted by
    how much of the article actually hit that node), persist that to the
    shared tree (so it also shapes every future article's scoring, not
    just this one), and re-score this article immediately. Rescore just
    re-runs scoring against the tree as it currently stands — no feedback,
    no threshold change — for when *other* articles' feedback has moved
    the tree since this one was last scored."""
    article_id = article["id"]
    col_more, col_less, col_rescore = st.columns(3)

    if col_more.button("👍 More", key=f"more_relative_{article_id}", help="This article is more relevant than its score suggests"):
        try:
            api_client.submit_relativity_feedback(article_id, "more")
        except Exception as e:
            st.error(f"Could not submit feedback: {e}")
        else:
            st.rerun()

    if col_less.button("👎 Less", key=f"less_relative_{article_id}", help="This article is less relevant than its score suggests"):
        try:
            api_client.submit_relativity_feedback(article_id, "less")
        except Exception as e:
            st.error(f"Could not submit feedback: {e}")
        else:
            st.rerun()

    if col_rescore.button("🔄 Rescore", key=f"rescore_{article_id}", help="Re-run scoring against the relevance tree as it stands now"):
        try:
            api_client.rescore_article(article_id)
        except Exception as e:
            st.error(f"Could not rescore: {e}")
        else:
            st.rerun()


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