import streamlit as st
import requests

API_BASE = "http://backend:8000/api/v1"

st.set_page_config(page_title="Email Wakeup Agent", layout="wide")

st.title("Email Wakeup Agent")

tab_prospects, tab_threads, tab_meetings = st.tabs(["Prospects", "Threads", "Meetings"])

with tab_prospects:
    st.header("Prospects")
    if st.button("Refresh"):
        resp = requests.get(f"{API_BASE}/prospects/")
        st.json(resp.json())

with tab_threads:
    st.header("Email Threads")
    if st.button("Refresh", key="threads_refresh"):
        resp = requests.get(f"{API_BASE}/threads/")
        st.json(resp.json())

with tab_meetings:
    st.header("Meetings")
    if st.button("Refresh", key="meetings_refresh"):
        resp = requests.get(f"{API_BASE}/meetings/")
        st.json(resp.json())
