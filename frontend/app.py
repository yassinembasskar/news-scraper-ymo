"""
Streamlit entrypoint.

Thin router only — actual page content lives in views/dashboard.py and
views/article_detail.py. Uses st.navigation with position="hidden" rather
than Streamlit's automatic pages/ directory discovery, so no sidebar
page-switcher is shown; the only way to reach the detail view is
programmatically, via the "See more" button in components/article_list.py.
"""

import streamlit as st

st.set_page_config(
    page_title="Yazaki News Monitor",
    page_icon="📰",
    layout="wide",
)

pages = [
    st.Page("views/dashboard.py", title="Dashboard", default=True),
    st.Page("views/article_detail.py", title="Article detail"),
    st.Page("views/reports.py", title="Reports"),
]
st.navigation(pages, position="hidden").run()
