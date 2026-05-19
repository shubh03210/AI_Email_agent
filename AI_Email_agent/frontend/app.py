"""
Email Wake-Up Agent — Streamlit Dashboard
──────────────────────────────────────────
Full-featured frontend for managing and monitoring the AI email agent.

Pages:
  🏠 Dashboard      — live metrics + activity feed + status charts
  👥 Prospects      — CRUD table, add/edit forms, trigger outreach
  📧 Threads        — thread list + chat-style message viewer + agent run trigger
  📅 Meetings       — meeting tracker with cancel / reschedule actions
  ⚙️ Agent Config   — edit gig description, tone, budget, working hours
  📋 Logs           — filterable paginated agent run log viewer
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Any, Optional

import pandas as pd
import requests
import streamlit as st

# ── Config ────────────────────────────────────────────────────────────────────

API_BASE = os.getenv("API_BASE_URL", "http://localhost:8000/api/v1")
DEFAULT_PAGE_SIZE = 20

TIMEZONES = [
    "UTC",
    "US/Eastern",
    "US/Central",
    "US/Pacific",
    "Europe/London",
    "Europe/Paris",
    "Asia/Kolkata",
    "Asia/Singapore",
    "Asia/Tokyo",
    "Australia/Sydney",
]

STATUS_COLORS = {
    # Prospect / Thread statuses
    "pending":     "🟡",
    "active":      "🟢",
    "contacted":   "🔵",
    "interested":  "🟢",
    "negotiating": "🟠",
    "scheduled":   "🟢",
    "waiting":     "🟡",
    "declined":    "🔴",
    "closed":      "⚫",
    # Meeting statuses
    "proposed":    "🟡",
    "confirmed":   "🟢",
    "rescheduled": "🟠",
    "cancelled":   "🔴",
    "completed":   "⚫",
    # Agent run statuses
    "running":     "🔵",
    "success":     "🟢",
    "error":       "🔴",
    "skipped":     "⚫",
}


# ── API Helpers ───────────────────────────────────────────────────────────────

def _request(method: str, path: str, **kwargs) -> Optional[Any]:
    """Base HTTP helper — returns parsed JSON or None on error."""
    url = f"{API_BASE}{path}"
    try:
        resp = requests.request(method, url, timeout=10, **kwargs)
        resp.raise_for_status()
        if resp.status_code == 204:
            return True
        return resp.json()
    except requests.exceptions.ConnectionError:
        st.error("Cannot connect to backend API. Is it running?")
        return None
    except requests.exceptions.HTTPError as e:
        try:
            detail = e.response.json().get("detail", e.response.text)
        except Exception:
            detail = e.response.text
        st.error(f"API {e.response.status_code}: {detail}")
        return None
    except Exception as e:
        st.error(f"Unexpected error: {e}")
        return None


def api_get(path: str, params: dict = None) -> Optional[Any]:
    return _request("GET", path, params=params)


def api_post(path: str, payload: dict = None) -> Optional[Any]:
    return _request("POST", path, json=payload or {})


def api_put(path: str, payload: dict) -> Optional[Any]:
    return _request("PUT", path, json=payload)


def api_delete(path: str) -> Optional[Any]:
    return _request("DELETE", path)


def get_items(path: str, params: dict = None) -> tuple[list, int]:
    """
    Call a paginated endpoint and return (items, total).
    Handles both list responses and {items, total} envelope responses.
    """
    data = api_get(path, params=params)
    if data is None:
        return [], 0
    if isinstance(data, list):
        return data, len(data)
    if isinstance(data, dict) and "items" in data:
        return data["items"], data.get("total", len(data["items"]))
    return [], 0


def status_badge(status: str) -> str:
    icon = STATUS_COLORS.get(status.lower(), "⚪")
    return f"{icon} {status.capitalize()}"


def fmt_dt(iso: Optional[str]) -> str:
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%d %b %Y, %H:%M")
    except Exception:
        return iso


# ── Page Config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Email Wake-Up Agent",
    page_icon="✉️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS ────────────────────────────────────────────────────────────────

st.markdown("""
<style>
    /* Sidebar width */
    [data-testid="stSidebar"] { min-width: 230px; max-width: 230px; }
    /* Metric cards */
    [data-testid="stMetric"] {
        background: #1e1e2e;
        border: 1px solid #313244;
        border-radius: 10px;
        padding: 16px;
    }
    /* Chat messages */
    .agent-bubble {
        background: #1e3a5f;
        border-radius: 12px;
        padding: 10px 14px;
        margin: 6px 0;
    }
    .prospect-bubble {
        background: #2a2a3e;
        border-radius: 12px;
        padding: 10px 14px;
        margin: 6px 0;
    }
    .stDataFrame { border-radius: 8px; overflow: hidden; }
</style>
""", unsafe_allow_html=True)


# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("## ✉️ Email Agent")
    st.caption("AI-powered outreach automation")
    st.divider()

    page = st.radio(
        "Navigation",
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

    # Health check
    health = api_get("/health/")
    if health and health.get("status") == "ok":
        st.success("API  ●  Online")
        db_status = health.get("db", "unknown")
        if db_status == "connected":
            st.success("DB   ●  Connected")
        else:
            st.warning(f"DB   ●  {db_status}")
    else:
        st.error("API  ●  Offline")

    st.divider()
    st.caption(f"Backend: `{API_BASE}`")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════

if page == "🏠 Dashboard":
    st.title("Dashboard")

    col1, col2, col3, col4 = st.columns(4)

    prospects, prospect_total = get_items("/prospects/", {"page_size": 1})
    threads, thread_total     = get_items("/threads/",   {"page_size": 1})
    meetings, meeting_total   = get_items("/meetings/",  {"page_size": 1})
    logs, log_total           = get_items("/logs/",      {"page_size": 1})

    with col1:
        st.metric("Total Prospects", prospect_total)
    with col2:
        st.metric("Email Threads", thread_total)
    with col3:
        st.metric("Meetings", meeting_total)
    with col4:
        st.metric("Agent Runs", log_total)

    st.divider()

    left, right = st.columns([3, 2])

    with left:
        st.subheader("Recent Agent Activity")
        recent_logs, _ = get_items("/logs/", {"page_size": 15})
        if recent_logs:
            rows = []
            for r in recent_logs:
                rows.append({
                    "Time":    fmt_dt(r.get("created_at")),
                    "Thread":  r.get("thread_id"),
                    "Node":    r.get("node_name"),
                    "Status":  status_badge(r.get("status", "")),
                    "Latency": f"{r.get('latency_ms', '—')} ms",
                })
            st.dataframe(
                pd.DataFrame(rows),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("No agent runs recorded yet.")

    with right:
        st.subheader("Prospect Pipeline")
        all_prospects, _ = get_items("/prospects/", {"page_size": 200})
        if all_prospects:
            try:
                import plotly.express as px
                status_counts = pd.Series(
                    [p.get("status", "unknown") for p in all_prospects]
                ).value_counts().reset_index()
                status_counts.columns = ["Status", "Count"]
                fig = px.bar(
                    status_counts,
                    x="Status",
                    y="Count",
                    color="Status",
                    color_discrete_map={
                        "pending":     "#f9e2af",
                        "contacted":   "#89b4fa",
                        "interested":  "#a6e3a1",
                        "negotiating": "#fab387",
                        "scheduled":   "#94e2d5",
                        "declined":    "#f38ba8",
                        "closed":      "#6c7086",
                    },
                    height=280,
                )
                fig.update_layout(
                    showlegend=False,
                    margin=dict(l=0, r=0, t=20, b=0),
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    font_color="#cdd6f4",
                )
                st.plotly_chart(fig, use_container_width=True)
            except ImportError:
                # Plotly not installed — show table fallback
                status_df = pd.DataFrame(
                    [(p.get("status","?"), 1) for p in all_prospects],
                    columns=["Status", "Count"]
                ).groupby("Status").sum().reset_index()
                st.dataframe(status_df, use_container_width=True, hide_index=True)
        else:
            st.info("No prospects yet.")

        st.subheader("Upcoming Meetings")
        upcoming, _ = get_items("/meetings/", {"status": "confirmed", "page_size": 5})
        if upcoming:
            for m in upcoming:
                st.markdown(
                    f"📅 **{fmt_dt(m.get('scheduled_at'))}** — "
                    f"Thread `{m.get('thread_id')}`"
                )
        else:
            st.info("No confirmed meetings.")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: PROSPECTS
# ══════════════════════════════════════════════════════════════════════════════

elif page == "👥 Prospects":
    st.title("Prospects")

    tab_list, tab_add, tab_edit = st.tabs(["All Prospects", "Add New", "Edit / Delete"])

    # ── Tab: All Prospects ────────────────────────────────────────────────────
    with tab_list:
        f1, f2, f3 = st.columns([3, 2, 1])
        with f1:
            search = st.text_input("Search name or email", placeholder="e.g. John or john@...")
        with f2:
            status_opts = ["All", "pending", "contacted", "interested",
                           "negotiating", "scheduled", "declined", "closed"]
            status_filter = st.selectbox("Status", status_opts)
        with f3:
            if st.button("Refresh", use_container_width=True):
                st.rerun()

        params: dict = {"page_size": 100}
        if search:
            params["search"] = search
        if status_filter != "All":
            params["status"] = status_filter

        prospects, total = get_items("/prospects/", params)
        st.caption(f"{total} prospect(s) found")

        if prospects:
            rows = []
            for p in prospects:
                rows.append({
                    "ID":       p.get("id"),
                    "Name":     p.get("name"),
                    "Email":    p.get("email"),
                    "Timezone": p.get("timezone"),
                    "Status":   status_badge(p.get("status", "")),
                    "Added":    fmt_dt(p.get("created_at")),
                })
            st.dataframe(
                pd.DataFrame(rows),
                use_container_width=True,
                hide_index=True,
                column_config={
                    "ID": st.column_config.NumberColumn(width="small"),
                },
            )

            st.divider()
            st.subheader("Actions")
            a1, a2 = st.columns(2)

            with a1:
                st.markdown("**Trigger Outreach**")
                out_options = {f"{p['name']} ({p['email']})": p["id"] for p in prospects}
                selected_out = st.selectbox("Select prospect", list(out_options.keys()), key="out_sel")
                if st.button("Send Outreach Email", type="primary"):
                    pid = out_options[selected_out]
                    result = api_post(f"/prospects/{pid}/outreach")
                    if result:
                        st.success(f"Outreach queued! Task ID: `{result.get('task_id')}`")

            with a2:
                st.markdown("**Delete Prospect**")
                del_options = {f"{p['name']} ({p['email']})": p["id"] for p in prospects}
                selected_del = st.selectbox("Select prospect", list(del_options.keys()), key="del_sel")
                if st.button("Delete", type="secondary"):
                    pid = del_options[selected_del]
                    confirmed = st.session_state.get(f"confirm_del_{pid}", False)
                    if not confirmed:
                        st.session_state[f"confirm_del_{pid}"] = True
                        st.warning("Click Delete again to confirm permanent deletion.")
                    else:
                        result = api_delete(f"/prospects/{pid}")
                        if result is not None:
                            st.success("Prospect deleted.")
                            st.session_state.pop(f"confirm_del_{pid}", None)
                            time.sleep(0.5)
                            st.rerun()
        else:
            st.info("No prospects found. Use the 'Add New' tab to add one.")

    # ── Tab: Add New ──────────────────────────────────────────────────────────
    with tab_add:
        st.subheader("Add New Prospect")
        with st.form("add_prospect_form", clear_on_submit=True):
            name  = st.text_input("Full Name *")
            email = st.text_input("Email Address *")
            tz    = st.selectbox("Timezone", TIMEZONES)
            submitted = st.form_submit_button("Add Prospect", type="primary")

        if submitted:
            if not name.strip() or not email.strip():
                st.error("Name and email are required.")
            else:
                result = api_post("/prospects/", {"name": name, "email": email, "timezone": tz})
                if result:
                    st.success(f"Prospect **{result['name']}** added (ID: `{result['id']}`).")

    # ── Tab: Edit ─────────────────────────────────────────────────────────────
    with tab_edit:
        st.subheader("Edit Prospect")
        all_p, _ = get_items("/prospects/", {"page_size": 200})

        if not all_p:
            st.info("No prospects to edit.")
        else:
            edit_opts = {f"{p['name']} ({p['email']}) — #{p['id']}": p for p in all_p}
            selected_key = st.selectbox("Select prospect to edit", list(edit_opts.keys()))
            ep = edit_opts[selected_key]

            with st.form("edit_prospect_form"):
                new_name  = st.text_input("Name",  value=ep["name"])
                new_email = st.text_input("Email", value=ep["email"])
                new_tz    = st.selectbox(
                    "Timezone",
                    TIMEZONES,
                    index=TIMEZONES.index(ep["timezone"]) if ep["timezone"] in TIMEZONES else 0,
                )
                status_list = ["pending", "contacted", "interested",
                               "negotiating", "scheduled", "declined", "closed"]
                new_status = st.selectbox(
                    "Status",
                    status_list,
                    index=status_list.index(ep["status"]) if ep["status"] in status_list else 0,
                )
                save = st.form_submit_button("Save Changes", type="primary")

            if save:
                result = api_put(
                    f"/prospects/{ep['id']}",
                    {"name": new_name, "email": new_email,
                     "timezone": new_tz, "status": new_status},
                )
                if result:
                    st.success("Prospect updated.")
                    st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: THREADS
# ══════════════════════════════════════════════════════════════════════════════

elif page == "📧 Threads":
    st.title("Email Threads")

    # Filter bar
    tf1, tf2, tf3 = st.columns([2, 2, 1])
    with tf1:
        prospect_id_filter = st.number_input("Prospect ID", min_value=0, value=0, step=1)
    with tf2:
        thread_status_opts = ["All", "pending", "active", "waiting", "closed"]
        thread_status_f = st.selectbox("Status", thread_status_opts, key="thread_status_f")
    with tf3:
        if st.button("Refresh", use_container_width=True, key="refresh_threads"):
            st.rerun()

    t_params: dict = {"page_size": 50}
    if prospect_id_filter > 0:
        t_params["prospect_id"] = int(prospect_id_filter)
    if thread_status_f != "All":
        t_params["status"] = thread_status_f

    threads, thread_total = get_items("/threads/", t_params)
    st.caption(f"{thread_total} thread(s)")

    if not threads:
        st.info("No threads found. Threads are created automatically when the agent processes emails.")
    else:
        # Thread selector
        thread_labels = [
            f"#{t['id']} — {t.get('subject','(No Subject)')[:55]} "
            f"[{t.get('status','')}]"
            for t in threads
        ]
        sel_idx = st.selectbox("Select thread", range(len(threads)),
                               format_func=lambda i: thread_labels[i])
        thread = threads[sel_idx]

        st.divider()

        # Thread header
        hc1, hc2, hc3 = st.columns([3, 1, 1])
        with hc1:
            st.subheader(thread.get("subject", "(No Subject)"))
            st.caption(f"Thread #{thread['id']} · Prospect #{thread.get('prospect_id')} · "
                       f"Gmail ID: `{thread.get('gmail_thread_id','—')}`")
        with hc2:
            st.markdown(f"**Status:** {status_badge(thread.get('status',''))}")
            st.caption(f"Updated: {fmt_dt(thread.get('updated_at'))}")
        with hc3:
            if st.button("Run Agent", type="primary", use_container_width=True):
                result = api_post(f"/threads/{thread['id']}/run")
                if result:
                    st.success(f"Agent queued! Task: `{result.get('task_id')}`")

        st.divider()

        msg_tab, neg_tab, log_tab = st.tabs(["Messages", "Negotiation State", "Run Logs"])

        with msg_tab:
            detail = api_get(f"/threads/{thread['id']}")
            messages = (detail or {}).get("messages", [])

            if not messages:
                st.info("No messages in this thread yet.")
            else:
                for msg in messages:
                    is_agent = msg.get("sender") == "agent"
                    with st.chat_message("assistant" if is_agent else "user"):
                        label = "Agent" if is_agent else msg.get("sender", "Prospect")
                        intent_tag = (
                            f" · intent: `{msg['intent']}`" if msg.get("intent") else ""
                        )
                        st.caption(
                            f"**{label}**{intent_tag} · "
                            f"{fmt_dt(msg.get('timestamp'))}"
                        )
                        st.markdown(msg.get("body", ""))

        with neg_tab:
            detail = api_get(f"/threads/{thread['id']}")
            neg = (detail or {}).get("negotiation")
            if neg:
                nc1, nc2, nc3 = st.columns(3)
                nc1.metric("Status",       neg.get("status","—").capitalize())
                nc2.metric("Max Budget",   f"${neg.get('max_budget',0):,.0f}")
                nc3.metric("Current Offer",
                           f"${neg.get('current_offer',0):,.0f}"
                           if neg.get("current_offer") else "None")
                st.caption(f"Last updated: {fmt_dt(neg.get('updated_at'))}")
            else:
                st.info("No negotiation record for this thread.")

        with log_tab:
            run_logs, _ = get_items(f"/threads/{thread['id']}/logs", {"page_size": 30})
            if run_logs:
                log_rows = []
                for r in run_logs:
                    log_rows.append({
                        "ID":      r.get("id"),
                        "Node":    r.get("node_name"),
                        "Status":  status_badge(r.get("status","")),
                        "Latency": f"{r.get('latency_ms','—')} ms",
                        "Time":    fmt_dt(r.get("created_at")),
                    })
                st.dataframe(
                    pd.DataFrame(log_rows),
                    use_container_width=True,
                    hide_index=True,
                )
            else:
                st.info("No agent runs logged for this thread.")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: MEETINGS
# ══════════════════════════════════════════════════════════════════════════════

elif page == "📅 Meetings":
    st.title("Meetings")

    mf1, mf2 = st.columns([2, 1])
    with mf1:
        meet_status_opts = ["All", "proposed", "confirmed", "rescheduled",
                            "cancelled", "completed"]
        meet_status_f = st.selectbox("Filter by status", meet_status_opts)
    with mf2:
        if st.button("Refresh", use_container_width=True, key="refresh_meetings"):
            st.rerun()

    m_params: dict = {"page_size": 50}
    if meet_status_f != "All":
        m_params["status"] = meet_status_f

    meetings, meet_total = get_items("/meetings/", m_params)
    st.caption(f"{meet_total} meeting(s)")

    if not meetings:
        st.info("No meetings found.")
    else:
        # Summary metrics
        sm1, sm2, sm3, sm4 = st.columns(4)
        all_meet, _ = get_items("/meetings/", {"page_size": 200})
        statuses = [m.get("status", "") for m in all_meet]
        sm1.metric("Confirmed",   statuses.count("confirmed"))
        sm2.metric("Proposed",    statuses.count("proposed"))
        sm3.metric("Rescheduled", statuses.count("rescheduled"))
        sm4.metric("Cancelled",   statuses.count("cancelled"))

        st.divider()

        for m in meetings:
            badge = STATUS_COLORS.get(m.get("status",""), "⚪")
            with st.expander(
                f"{badge} Meeting #{m.get('id')} — "
                f"{fmt_dt(m.get('scheduled_at'))} "
                f"[{m.get('status','').upper()}]"
            ):
                ec1, ec2 = st.columns(2)
                with ec1:
                    st.markdown(f"**Thread ID:** `{m.get('thread_id')}`")
                    st.markdown(f"**Google Event ID:** `{m.get('google_event_id','N/A')}`")
                    st.markdown(f"**Scheduled At:** {fmt_dt(m.get('scheduled_at'))}")
                with ec2:
                    st.markdown(f"**Status:** {status_badge(m.get('status',''))}")
                    st.markdown(f"**Reschedule Count:** {m.get('reschedule_count', 0)}")

                st.divider()
                ac1, ac2 = st.columns(2)
                with ac1:
                    if m.get("status") not in ("cancelled", "completed"):
                        if st.button(
                            "Cancel Meeting",
                            key=f"cancel_{m['id']}",
                            type="secondary",
                            use_container_width=True,
                        ):
                            r = api_post(f"/meetings/{m['id']}/cancel")
                            if r:
                                st.success("Meeting cancelled.")
                                time.sleep(0.5)
                                st.rerun()
                with ac2:
                    if m.get("status") not in ("cancelled", "completed"):
                        if st.button(
                            "Reschedule",
                            key=f"reschedule_{m['id']}",
                            type="primary",
                            use_container_width=True,
                        ):
                            r = api_post(f"/meetings/{m['id']}/reschedule")
                            if r:
                                st.success(
                                    f"Reschedule queued! Task: `{r.get('task_id')}`"
                                )


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: AGENT CONFIG
# ══════════════════════════════════════════════════════════════════════════════

elif page == "⚙️ Agent Config":
    st.title("Agent Configuration")
    st.caption("These settings control how the AI agent writes emails, negotiates, and schedules meetings.")

    current = api_get("/config/") or {}

    with st.form("config_form"):
        st.subheader("Outreach & Tone")
        gig_description = st.text_area(
            "Gig / Role Description",
            value=current.get("gig_description", ""),
            height=120,
            help="Describe the job or project you're recruiting for. "
                 "The agent uses this to personalise cold outreach emails.",
            placeholder="e.g. We're hiring a senior backend engineer for a fintech startup...",
        )
        tone = st.selectbox(
            "Email Tone",
            ["professional", "friendly", "formal", "casual"],
            index=["professional", "friendly", "formal", "casual"].index(
                current.get("tone", "professional")
            ),
        )

        st.subheader("Negotiation")
        budget_ceiling = st.number_input(
            "Maximum Budget Ceiling (USD)",
            min_value=100.0,
            max_value=1_000_000.0,
            value=float(current.get("budget_ceiling", 5000.0)),
            step=100.0,
            help="The agent will NEVER reveal or exceed this amount. "
                 "Counter-offers are calculated against this ceiling.",
        )

        st.subheader("Working Hours")
        tz_val = current.get("timezone", "UTC")
        timezone = st.selectbox(
            "Agent Timezone",
            TIMEZONES,
            index=TIMEZONES.index(tz_val) if tz_val in TIMEZONES else 0,
            help="Calendar slots and meeting times are generated relative to this timezone.",
        )
        wh1, wh2 = st.columns(2)
        with wh1:
            working_start = st.slider(
                "Working Hours Start",
                min_value=0,
                max_value=23,
                value=int(current.get("working_hours_start", 9)),
                format="%d:00",
            )
        with wh2:
            working_end = st.slider(
                "Working Hours End",
                min_value=1,
                max_value=24,
                value=int(current.get("working_hours_end", 18)),
                format="%d:00",
            )

        st.subheader("Status")
        is_active = st.toggle(
            "Config Active",
            value=current.get("is_active", True),
            help="Only one config can be active at a time.",
        )

        save_btn = st.form_submit_button("Save Configuration", type="primary")

    if save_btn:
        if working_end <= working_start:
            st.error("Working hours end must be after start.")
        else:
            payload = {
                "gig_description":    gig_description,
                "tone":               tone,
                "budget_ceiling":     budget_ceiling,
                "timezone":           timezone,
                "working_hours_start": working_start,
                "working_hours_end":  working_end,
                "is_active":          is_active,
            }
            result = api_put("/config/", payload)
            if result:
                st.success("Configuration saved successfully.")
                st.json(result)

    if current:
        st.divider()
        st.subheader("Current Active Config")
        cc1, cc2, cc3 = st.columns(3)
        cc1.metric("Budget Ceiling", f"${current.get('budget_ceiling',0):,.0f}")
        cc2.metric("Tone",           current.get("tone","—").capitalize())
        cc3.metric("Working Hours",
                   f"{current.get('working_hours_start',9)}:00 – "
                   f"{current.get('working_hours_end',18)}:00")
        st.caption(f"Config ID: `{current.get('id')}` · "
                   f"Last updated: {fmt_dt(current.get('updated_at'))}")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: LOGS
# ══════════════════════════════════════════════════════════════════════════════

elif page == "📋 Logs":
    st.title("Agent Execution Logs")

    # Filters
    lf1, lf2, lf3, lf4 = st.columns([2, 2, 2, 1])
    with lf1:
        log_thread_id = st.number_input("Thread ID", min_value=0, value=0, step=1)
    with lf2:
        log_node = st.text_input("Node Name", placeholder="e.g. classify_intent")
    with lf3:
        log_status_opts = ["All", "success", "error", "running", "skipped"]
        log_status_f = st.selectbox("Status", log_status_opts)
    with lf4:
        if st.button("Refresh", use_container_width=True, key="refresh_logs"):
            st.rerun()

    page_size = st.select_slider(
        "Results per page", options=[20, 50, 100, 200], value=50
    )

    l_params: dict = {"page_size": page_size}
    if log_thread_id > 0:
        l_params["thread_id"] = int(log_thread_id)
    if log_node.strip():
        l_params["node_name"] = log_node.strip()
    if log_status_f != "All":
        l_params["status"] = log_status_f

    logs, log_total = get_items("/logs/", l_params)
    st.caption(f"{log_total} total log(s) matching filters")

    if not logs:
        st.info("No logs found. Run the agent to start generating logs.")
    else:
        # Summary metrics
        lm1, lm2, lm3 = st.columns(3)
        all_statuses = [r.get("status","") for r in logs]
        lm1.metric("Success", all_statuses.count("success"))
        lm2.metric("Errors",  all_statuses.count("error"))
        lm3.metric(
            "Avg Latency",
            f"{int(sum(r.get('latency_ms',0) or 0 for r in logs) / max(len(logs),1))} ms",
        )

        st.divider()

        # Flat table
        log_rows = []
        for r in logs:
            log_rows.append({
                "ID":      r.get("id"),
                "Thread":  r.get("thread_id"),
                "Node":    r.get("node_name"),
                "Status":  status_badge(r.get("status","")),
                "Latency": f"{r.get('latency_ms','—')} ms",
                "Time":    fmt_dt(r.get("created_at")),
            })
        st.dataframe(
            pd.DataFrame(log_rows),
            use_container_width=True,
            hide_index=True,
        )

        # Expandable log detail
        st.divider()
        st.subheader("Inspect Log Entry")
        log_ids = {f"#{r['id']} — {r.get('node_name')} [{r.get('status')}]": r
                   for r in logs}
        selected_log_key = st.selectbox("Select log entry", list(log_ids.keys()))
        selected_log = log_ids[selected_log_key]

        # Fetch full detail
        full_log = api_get(f"/logs/{selected_log['id']}")
        if full_log:
            dc1, dc2 = st.columns(2)
            with dc1:
                st.markdown("**Input Payload**")
                if full_log.get("input_payload"):
                    st.json(full_log["input_payload"])
                else:
                    st.caption("No input payload.")
            with dc2:
                st.markdown("**Output Payload**")
                if full_log.get("output_payload"):
                    st.json(full_log["output_payload"])
                else:
                    st.caption("No output payload.")
