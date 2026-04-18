"""PayGuard Streamlit UI — Fallback demo UI with all 3 views."""

import os
import json
import time
import requests
import streamlit as st
import pandas as pd

API_URL = os.environ.get("API_URL", "http://api:8000")

st.set_page_config(
    page_title="PayGuard - Fraud Investigation",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.sidebar.title("🛡️ PayGuard")
st.sidebar.caption("LLM-Powered Fraud Investigation")
page = st.sidebar.radio("Navigate", ["Dashboard", "Investigation", "Benchmarks"])


def api_get(path, params=None):
    try:
        r = requests.get(f"{API_URL}{path}", params=params, timeout=10)
        return r.json()
    except Exception as e:
        st.error(f"API error: {e}")
        return None


def api_post(path, data):
    try:
        r = requests.post(f"{API_URL}{path}", json=data, timeout=30)
        return r.json()
    except Exception as e:
        st.error(f"API error: {e}")
        return None


if page == "Dashboard":
    st.title("Transaction Dashboard")

    col1, col2 = st.columns([3, 1])
    with col2:
        fraud_only = st.toggle("Flagged Only", value=False)

    params = {"limit": 50, "offset": 0}
    if fraud_only:
        params["is_fraud"] = "true"

    data = api_get("/api/transactions", params)

    if data and data.get("transactions"):
        st.caption(f"Showing {len(data['transactions'])} of {data['total']} transactions")

        df = pd.DataFrame(data["transactions"])
        display_cols = ["transaction_id", "type", "amount", "name_orig", "name_dest",
                       "merchant_category", "country_code", "is_fraud"]
        available_cols = [c for c in display_cols if c in df.columns]
        st.dataframe(df[available_cols], use_container_width=True, height=500)

        st.divider()
        st.subheader("Quick Investigate")
        txn_id = st.text_input("Transaction ID", value="TXN_48213")
        if st.button("🔍 Start Investigation", type="primary"):
            result = api_post("/api/investigate", {"transaction_id": txn_id})
            if result:
                st.success(f"Investigation started: {result['investigation_id']}")
                st.session_state["current_investigation"] = result["investigation_id"]
                st.session_state["current_txn"] = txn_id
    else:
        st.info("No transactions loaded. Run `make seed` first.")

elif page == "Investigation":
    st.title("Investigation Console")

    inv_id = st.session_state.get("current_investigation", "")
    txn_id = st.session_state.get("current_txn", "TXN_48213")

    if not inv_id:
        latest = api_get("/api/investigations", {"limit": 1})
        if latest and latest.get("investigations"):
            first = latest["investigations"][0]
            inv_id = first.get("id", "")
            txn_id = first.get("transaction_id", txn_id)
            st.session_state["current_investigation"] = inv_id
            st.session_state["current_txn"] = txn_id

    if not inv_id:
        st.info("No investigations in the database yet. Start one from the Dashboard, or enter an ID below.")
        inv_id = st.text_input("Investigation ID", value="")

    if inv_id:
        col1, col2, col3 = st.columns([3, 4, 3])

        with col1:
            st.subheader("Transaction Details")
            txn_data = api_get(f"/api/transactions/{txn_id}")
            if txn_data:
                for key in ["transaction_id", "type", "amount", "name_orig", "name_dest",
                           "country_code", "merchant_category"]:
                    if key in txn_data:
                        val = txn_data[key]
                        if key == "amount":
                            val = f"${val:,.2f}"
                        st.text(f"{key}: {val}")

        with col2:
            st.subheader("Agent Timeline")
            inv_data = api_get(f"/api/investigations/{inv_id}")
            if inv_data and inv_data.get("agent_trace"):
                for event in inv_data["agent_trace"]:
                    agent = event.get("agent", "system")
                    etype = event.get("type", "")
                    content = event.get("content", "")

                    icon = {"thought": "💭", "tool_call": "🔧", "tool_result": "📊",
                           "verdict": "⚖️", "approval_required": "🔒"}.get(etype, "•")
                    color = {"triage": "orange", "behavior": "violet", "synthesis": "blue"}.get(agent, "gray")

                    with st.chat_message(name=agent, avatar=icon):
                        if isinstance(content, dict):
                            st.json(content)
                        else:
                            st.write(str(content))
            else:
                with st.spinner("Waiting for agent events..."):
                    time.sleep(2)
                    st.rerun()

            if inv_data and inv_data.get("status") == "pending_approval":
                st.warning("⚠️ Approval required!")
                c1, c2 = st.columns(2)
                with c1:
                    if st.button("✅ Approve", type="primary"):
                        api_post("/api/approve", {
                            "investigation_id": inv_id,
                            "decision": "approve",
                            "approved_by": "demo_user",
                        })
                        st.success("Approved!")
                        st.rerun()
                with c2:
                    if st.button("❌ Reject"):
                        api_post("/api/approve", {
                            "investigation_id": inv_id,
                            "decision": "reject",
                            "approved_by": "demo_user",
                        })
                        st.error("Rejected")
                        st.rerun()

        with col3:
            st.subheader("Evidence")
            if inv_data:
                if inv_data.get("verdict"):
                    st.metric("Verdict", inv_data["verdict"].upper())
                if inv_data.get("confidence") is not None:
                    st.metric("Confidence", f"{float(inv_data['confidence'])*100:.0f}%")
                if inv_data.get("recommendation"):
                    st.metric("Recommendation", inv_data["recommendation"].upper())
                cu = inv_data.get("cost_usd")
                if cu is not None:
                    st.caption(f"Investigation cost: ${float(cu):.5f}")
                mb = inv_data.get("model_breakdown") or []
                if mb:
                    st.caption("Models: " + ", ".join(f"{m.get('agent')}={m.get('model')}" for m in mb))

        audit = api_get(f"/api/investigations/{inv_id}/audit")
        if audit and audit.get("entries"):
            st.divider()
            st.subheader("Audit trail")
            for e in audit["entries"]:
                ts = e.get("timestamp", "")
                st.markdown(f"**{ts}** — `{e.get('actor')}` → **{e.get('action')}**  \n_{e.get('reason') or ''}_")

elif page == "Benchmarks":
    st.title("Benchmark Results")
    st.caption("Rules-only baseline vs. Multi-agent pipeline")

    # Try to load CSV
    csv_paths = [
        "/app/benchmarks/results/comparison.csv",
        "benchmarks/results/comparison.csv",
        "../benchmarks/results/comparison.csv",
    ]

    df = None
    for path in csv_paths:
        try:
            df = pd.read_csv(path)
            break
        except Exception:
            continue

    if df is not None and len(df) > 0:
        col1, col2, col3 = st.columns(3)

        rules_correct = (df["rules_verdict"] == df["ground_truth"]).sum()
        agent_correct = (df["agent_verdict"] == df["ground_truth"]).sum()
        improvement = ((agent_correct - rules_correct) / max(rules_correct, 1)) * 100

        with col1:
            st.metric("More Patterns Detected", f"{improvement:.0f}%+")
        with col2:
            median_latency = df["agent_latency_ms"].median()
            st.metric("Median Latency", f"{median_latency/1000:.1f}s")
        with col3:
            st.metric("Test Scenarios", len(df))

        st.divider()

        chart_data = pd.DataFrame({
            "Metric": ["Accuracy", "Accuracy"],
            "System": ["Rules", "Agent"],
            "Value": [rules_correct / len(df) * 100, agent_correct / len(df) * 100],
        })
        st.bar_chart(chart_data.pivot(index="Metric", columns="System", values="Value"))

        st.divider()
        st.subheader("Detailed Results")
        st.dataframe(df, use_container_width=True)
    else:
        st.info("No benchmark results found. Run `make bench` first.")
        st.code("make bench", language="bash")
