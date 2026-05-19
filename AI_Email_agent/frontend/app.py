import os
import requests
import pandas as pd
import streamlit as st
from datetime import datetime

API_BASE = os.getenv("API_BASE_URL", "http://localhost:8000/api/v1")


# ── Helpers ───────────────────────────────────────────────────────────────────

def api_get(path: str, params: dict = None) -> dict | list | None:
    try:
        resp = requests.get(f"{API_BASE}{path}", params=params, timeout=5)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.ConnectionError:
        st.error("Cannot connect to backend. Is the API running?")
        return None
    except requests.exceptions.HTTPError as e:
        st.error(f"API error: {e.response.status_code} — {e.response.text}")
        return None
    except Exception as e:
        st.error(f"Unexpected error: {e}")
        return None


def api_post(path: str, payload: dict) -> dict | None:
    try:
        resp = requests.post(f"{API_BASE}{path}", json=payload, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.ConnectionError:
        st.error("Cannot connect to backend.")
        return None
    except requests.exceptions.HTTPError as e:
        st.error(f"API error: {e.response.status_code} — {e.response.text}")
        return None
    except Exception as e:
        st.error(f"Unexpected error: {e}")
        return None


def api_put(path: str, payload: dict) -> dict | None:
    try:
        resp = requests.put(f"{API_BASE}{path}", json=payload, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        st.error(f"Unexpected error: {e}")
        return None


# ── Page Config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Email Wake-Up Agent",
    page_icon="✉️",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ── Sidebar Navigation ────────────────────────────────────────────────────────

with st.sidebar:
    st.title("✉️ Email Wake-Up Agent")
    st.caption("AI-powered outreach automation")
    st.divider()

    page = st.radio(
        "Navigate",
        [
            "🏠 Dashboard",
            "👥 Prospects",
            "📧 Threads",
            "📅 Meetings",
            "⚙️ Agent Config",
            "📋 Logs",
        ],
        label_visibility="collapsed",
    )

    st.divider()

    health = api_get("/health/")
    if health:
        st.success("API: Online")
    else:
        st.error("API: Offline")


# ── Dashboard ─────────────────────────────────────────────────────────────────

if page == "🏠 Dashboard":
    st.title("Dashboard")

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        prospects = api_get("/prospects/") or []
        st.metric("Total Prospects", len(prospects))

    with col2:
        threads = api_get("/threads/") or []
        st.metric("Active Threads", len(threads))

    with col3:
        meetings = api_get("/meetings/") or []
        st.metric("Meetings Scheduled", len(meetings))

    with col4:
        logs = api_get("/logs/") or []
        st.metric("Agent Runs", len(logs))

    st.divider()

    st.subheader("Recent Agent Activity")
    logs = api_get("/logs/", params={"limit": 10}) or []
    if logs:
        df = pd.DataFrame(logs)
        st.dataframe(df, use_container_width=True)
    else:
        st.info("No agent runs yet.")


# ── Prospects ─────────────────────────────────────────────────────────────────

elif page == "👥 Prospects":
    st.title("Prospects")

    tab_list, tab_add, tab_agent = st.tabs(["View Prospects", "Add Prospect", "Run Agent"])

    with tab_list:
        if st.button("Refresh", key="refresh_prospects"):
            st.rerun()

        prospects = api_get("/prospects/") or []
        if prospects:
            df = pd.DataFrame(prospects)
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.info("No prospects found. Add one in the 'Add Prospect' tab.")

    with tab_add:
        st.subheader("Add New Prospect")
        with st.form("add_prospect_form"):
            name = st.text_input("Full Name *")
            email = st.text_input("Email Address *")
            timezone = st.selectbox(
                "Timezone",
                ["UTC", "US/Eastern", "US/Central", "US/Pacific", "Europe/London", "Asia/Kolkata"],
            )
            submitted = st.form_submit_button("Add Prospect")

        if submitted:
            if not name or not email:
                st.error("Name and email are required.")
            else:
                result = api_post("/prospects/", {"name": name, "email": email, "timezone": timezone})
                if result:
                    st.success(f"Prospect '{name}' added successfully!")

    with tab_agent:
        st.subheader("Start Agent for Prospect")
        prospects = api_get("/prospects/") or []
        if prospects:
            options = {f"{p['name']} ({p['email']})": p["id"] for p in prospects}
            selected = st.selectbox("Select Prospect", list(options.keys()))
            if st.button("Start Agent"):
                prospect_id = options[selected]
                result = api_post("/agent/start", {"prospect_id": prospect_id})
                if result:
                    st.success(f"Agent started! Task ID: `{result.get('task_id')}`")
                    st.json(result)
        else:
            st.info("No prospects available. Add one first.")


# ── Threads ───────────────────────────────────────────────────────────────────

elif page == "📧 Threads":
    st.title("Email Threads")

    threads = api_get("/threads/") or []

    if not threads:
        st.info("No email threads found.")
    else:
        df = pd.DataFrame(threads)
        selected_idx = st.selectbox(
            "Select a thread to view",
            range(len(threads)),
            format_func=lambda i: f"{threads[i].get('subject', 'No Subject')} — {threads[i].get('status', '')}",
        )

        thread = threads[selected_idx]
        st.divider()

        col1, col2 = st.columns([2, 1])
        with col1:
            st.subheader(thread.get("subject", "No Subject"))
        with col2:
            status_color = {"active": "🟢", "closed": "🔴", "pending": "🟡"}.get(
                thread.get("status", ""), "⚪"
            )
            st.markdown(f"**Status:** {status_color} {thread.get('status', 'unknown')}")

        detail = api_get(f"/threads/{thread['id']}")
        if detail and "messages" in detail:
            st.subheader("Messages")
            for msg in detail["messages"]:
                with st.chat_message("user" if msg.get("sender") != "agent" else "assistant"):
                    st.markdown(f"**From:** {msg.get('sender', 'unknown')}")
                    st.markdown(msg.get("body", ""))
                    st.caption(msg.get("timestamp", ""))


# ── Meetings ──────────────────────────────────────────────────────────────────

elif page == "📅 Meetings":
    st.title("Meetings")

    if st.button("Refresh", key="refresh_meetings"):
        st.rerun()

    meetings = api_get("/meetings/") or []

    if not meetings:
        st.info("No meetings scheduled yet.")
    else:
        for m in meetings:
            with st.expander(
                f"📅 {m.get('scheduled_at', 'TBD')} — {m.get('status', 'unknown').upper()}"
            ):
                col1, col2 = st.columns(2)
                with col1:
                    st.markdown(f"**Meeting ID:** `{m.get('id')}`")
                    st.markdown(f"**Thread ID:** `{m.get('thread_id')}`")
                    st.markdown(f"**Google Event ID:** `{m.get('google_event_id', 'N/A')}`")
                with col2:
                    st.markdown(f"**Status:** {m.get('status')}")
                    st.markdown(f"**Reschedule Count:** {m.get('reschedule_count', 0)}")
                    st.markdown(f"**Scheduled At:** {m.get('scheduled_at', 'TBD')}")


# ── Agent Config ──────────────────────────────────────────────────────────────

elif page == "⚙️ Agent Config":
    st.title("Agent Configuration")

    current = api_get("/config/") or {}

    with st.form("config_form"):
        st.subheader("Gig & Outreach")
        gig_description = st.text_area(
            "Gig Description",
            value=current.get("gig_description", ""),
            height=120,
            help="Describe the role/project you're recruiting for.",
        )
        tone = st.selectbox(
            "Email Tone",
            ["professional", "friendly", "formal", "casual"],
            index=["professional", "friendly", "formal", "casual"].index(
                current.get("tone", "professional")
            ),
        )

        st.subheader("Budget & Negotiation")
        budget_ceiling = st.number_input(
            "Maximum Budget Ceiling ($)",
            min_value=0.0,
            value=float(current.get("budget_ceiling", 5000.0)),
            step=100.0,
        )

        st.subheader("Availability")
        timezone = st.selectbox(
            "Agent Timezone",
            ["UTC", "US/Eastern", "US/Central", "US/Pacific", "Europe/London", "Asia/Kolkata"],
            index=0,
        )
        col1, col2 = st.columns(2)
        with col1:
            working_start = st.number_input("Working Hours Start", min_value=0, max_value=23, value=9)
        with col2:
            working_end = st.number_input("Working Hours End", min_value=0, max_value=23, value=18)

        submitted = st.form_submit_button("Save Configuration")

    if submitted:
        payload = {
            "gig_description": gig_description,
            "tone": tone,
            "budget_ceiling": budget_ceiling,
            "timezone": timezone,
            "working_hours": f"{working_start:02d}:00-{working_end:02d}:00",
        }
        result = api_put("/config/", payload) or api_post("/config/", payload)
        if result:
            st.success("Configuration saved!")


# ── Logs ──────────────────────────────────────────────────────────────────────

elif page == "📋 Logs":
    st.title("Agent Execution Logs")

    col1, col2, col3 = st.columns(3)
    with col1:
        filter_thread = st.text_input("Filter by Thread ID")
    with col2:
        filter_node = st.text_input("Filter by Node Name")
    with col3:
        limit = st.number_input("Max Results", min_value=10, max_value=200, value=50, step=10)

    if st.button("Fetch Logs"):
        params = {"limit": limit}
        if filter_thread:
            params["thread_id"] = filter_thread
        if filter_node:
            params["node_name"] = filter_node

        logs = api_get("/logs/", params=params) or []
        if logs:
            df = pd.DataFrame(logs)
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.info("No logs found matching the filters.")
