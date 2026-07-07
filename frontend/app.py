"""
Streamlit entrypoint.

Lays out the page and orchestrates the three dashboard sections. Each
component owns its own rendering and session-state usage; this file just
sequences them.
"""

import streamlit as st

from config import settings
from components import article_list, chart_view, overview_bar

st.set_page_config(
    page_title="Yazaki News Monitor",
    page_icon="📰",
    layout="wide",
)

st.title("📰 Yazaki News Monitor")

overview_bar.render()
st.divider()
chart_view.render()
st.divider()
article_list.render()