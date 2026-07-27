"""
Main dashboard view: overview bar, chart, and article list. This used to be
the entire content of app.py — moved here so app.py can be a thin router
(st.navigation) between this and views/article_detail.py, with no visible
page-switcher UI (position="hidden").
"""

import streamlit as st

from components import article_list, chart_view, overview_bar

st.title("📰 Yazaki News Monitor")

overview_bar.render()
st.divider()
chart_view.render()
st.divider()
article_list.render()
