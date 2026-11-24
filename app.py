"""AI Use Case Intake & Risk Check: entry point and navigation.

Run with:  streamlit run app.py
"""
import streamlit as st

st.set_page_config(page_title="AI Use Case Intake & Risk Check", page_icon="🧭", layout="wide")

nav = st.navigation([
    st.Page("app_pages/0_Home.py", title="Home", icon="🧭", default=True),
    st.Page("app_pages/1_Submit.py", title="Submit a request", icon="📝"),
    st.Page("app_pages/2_Review.py", title="Review queue", icon="✅"),
    st.Page("app_pages/3_Portfolio.py", title="Portfolio", icon="📊"),
])
nav.run()
