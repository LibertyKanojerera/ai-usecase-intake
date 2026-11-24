"""AI Use Case Intake & Risk Check: entry point and navigation.

Run with:  streamlit run app.py
"""
import streamlit as st

st.set_page_config(page_title="AI Use Case Intake & Risk Check", page_icon="🧭", layout="wide")

nav = st.navigation([
    st.Page("pages/0_Home.py", title="Home", icon="🧭", default=True),
    st.Page("pages/1_Submit.py", title="Submit a request", icon="📝"),
])
nav.run()
