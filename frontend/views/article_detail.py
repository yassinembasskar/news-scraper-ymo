"""
Article detail view.

Reached via the "See more" button on each article in components/article_list.py,
which stashes the article's id in st.session_state before switching here —
st.query_params does NOT survive a st.switch_page() navigation (it resets to
the new page's bare URL), so session_state is the reliable way to pass data
between pages here. Shows the full scraped content plus an illustration of
which relevance-tree nodes the scoring job's tree walk actually triggered for
this article (see services/scorer.py on the backend for the algorithm that
produced score_explanation). Routed in via app.py's st.navigation, not
Streamlit's automatic pages/ discovery, so there's no sidebar page-switcher
shown — navigation only ever happens programmatically via the "See more"
button.
"""

import streamlit as st

import api_client
from components.article_list import RELATIVITY_HIGH_THRESHOLD, RELATIVITY_MEDIUM_THRESHOLD

st.page_link("views/dashboard.py", label="← Back to dashboard")

article_id = st.session_state.get("selected_article_id")
if not article_id:
    st.info("No article selected — go back to the dashboard and click \"See more\" on an article.")
    st.stop()

try:
    article = api_client.get_article(article_id)
except Exception as e:
    st.error(f"Could not load article: {e}")
    st.stop()

try:
    tree_nodes = api_client.get_tree_nodes()
except Exception:
    tree_nodes = {}


def _node_label(node_id: str, language: str) -> str:
    node = tree_nodes.get(node_id)
    if node is None:
        return node_id
    return node["labels"].get(language, node_id)


def _node_path(node_id: str, language: str) -> str:
    """Root-to-node breadcrumb by walking the parent chain upward."""
    labels = []
    current = node_id
    while current is not None:
        node = tree_nodes.get(current)
        if node is None:
            break
        labels.append(_node_label(current, language))
        current = node.get("parent")
    return " › ".join(reversed(labels))


st.title(article["title"])

lang = (article.get("language") or "en").lower()
lang_tag = lang.upper()
published = article.get("published_at", "")
source = article.get("source_name") or "Unknown source"
score = article.get("relativity_score")
scored_status = article.get("scored_status", "Not Scored")

st.caption(f"{lang_tag} · {published} · {source} · [Original article]({article['url']})")

if scored_status == "Scored" and score is not None:
    if score >= RELATIVITY_HIGH_THRESHOLD:
        color = "green"
    elif score >= RELATIVITY_MEDIUM_THRESHOLD:
        color = "orange"
    else:
        color = "red"
    st.markdown(f"### Relativity score: :{color}[{score}]")
else:
    st.markdown(f"### Relativity score: :gray[{scored_status}]")

st.divider()

st.subheader("Scraped content")
st.write(article.get("content") or "No content available.")

st.divider()

st.subheader("Triggered relevance nodes")

explanation = article.get("score_explanation")
if not explanation or not explanation.get("triggered_nodes"):
    st.info("No relevance nodes were triggered for this article yet.")
else:
    primary_node_id = (explanation.get("primary_node") or {}).get("node_id")
    triggered = sorted(explanation["triggered_nodes"], key=lambda n: n["max_similarity"], reverse=True)

    for node in triggered:
        node_id = node["node_id"]
        is_primary = node_id == primary_node_id
        path = _node_path(node_id, lang)
        similarity = node["max_similarity"]
        kind = "Leaf" if node["is_leaf"] else "Branch"
        chunk_count = len(node.get("supporting_chunks", []))

        title = f"**{path}**"
        if is_primary:
            title = f"🎯 {title} — primary match"

        st.markdown(title)
        col1, col2 = st.columns([3, 1])
        col1.progress(min(similarity, 1.0), text=f"similarity {similarity:.2f}")
        col2.caption(f"{kind} · {chunk_count} chunk(s)")
        st.markdown("")
