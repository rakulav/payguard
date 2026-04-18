"""Behavior Agent: reasons over customer history using customer_profile + similar_fraud_search."""

import time
from pathlib import Path
from typing import Callable

from app.llm import call_llm
from app.audit_service import append_audit
from app.cost_tracker import usage_record
from app.mcp_servers.customer_profile import customer_profile
from app.mcp_servers.similar_fraud_search import similar_fraud_search

SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "behavior.txt").read_text()


async def run_behavior(
    transaction_id: str,
    triage_result: dict,
    emit_event: Callable[[dict], None] | None = None,
    investigation_id: str | None = None,
) -> dict:
    """Run behavioral analysis when triage confidence < 0.9."""
    start = time.time()

    def emit(event: dict):
        if emit_event:
            emit_event({"agent": "behavior", **event})

    txn = triage_result.get("transaction", {})
    customer_id = txn.get("name_orig", "unknown")

    emit(
        {
            "type": "thought",
            "content": f"Triage confidence {triage_result['confidence']:.2f} < 0.9 — running behavioral analysis for customer {customer_id}...",
        }
    )

    # Tool call 1: customer_profile
    emit({"type": "tool_call", "content": f"Calling customer_profile({customer_id})"})
    profile_result = await customer_profile(customer_id)
    profile = profile_result.get("profile", {})
    emit(
        {
            "type": "tool_result",
            "content": f"Profile: {profile.get('total_transactions', 0)} transactions, avg ${profile.get('avg_amount', 0):,.2f}",
        }
    )

    # Tool call 2: similar_fraud_search
    emit(
        {
            "type": "tool_call",
            "content": f"Calling similar_fraud_search({transaction_id})",
        }
    )
    search_result = await similar_fraud_search(transaction_id=transaction_id, k=10)
    similar = search_result.get("similar_transactions", [])
    sources = search_result.get("retrieval_sources", {})
    emit(
        {
            "type": "tool_result",
            "content": f"Found {len(similar)} similar transactions. Sources: pgvector({sources.get('pgvector', 0)}), qdrant({sources.get('qdrant', 0)}), opensearch({sources.get('opensearch', 0)})",
        }
    )

    # Analyze behavioral anomalies
    behavioral_flags = []
    anomaly_score = 0.5

    avg_amount = profile.get("avg_amount", 0) or 100
    if txn.get("amount", 0) > avg_amount * 5:
        behavioral_flags.append(f"amount_{txn['amount']/avg_amount:.0f}x_average")
        anomaly_score += 0.15

    known_countries = [c["country"] for c in profile.get("countries", [])]
    if txn.get("country_code") and txn["country_code"] not in known_countries:
        behavioral_flags.append(f"new_country_{txn['country_code']}")
        anomaly_score += 0.15

    known_categories = [c["category"] for c in profile.get("top_categories", [])]
    if (
        txn.get("merchant_category")
        and txn["merchant_category"] not in known_categories
    ):
        behavioral_flags.append(f"category_drift_to_{txn['merchant_category']}")
        anomaly_score += 0.10

    if txn.get("device_fingerprint", "").startswith("new_dev"):
        behavioral_flags.append("new_device")
        anomaly_score += 0.10

    if txn.get("new_balance_orig", 0) == 0 and txn.get("old_balance_org", 0) > 0:
        behavioral_flags.append("full_balance_drain")
        anomaly_score += 0.15

    similar_fraud_count = sum(1 for s in similar if s.get("is_fraud"))
    if similar_fraud_count > len(similar) * 0.5:
        behavioral_flags.append(f"similar_to_{similar_fraud_count}_known_frauds")
        anomaly_score += 0.10

    anomaly_score = min(anomaly_score, 0.99)

    similar_total = len(similar)
    fraud_match_ratio = (similar_fraud_count / similar_total) if similar_total else 0.0

    # LLM reasoning
    user_msg = (
        f"Behavioral analysis for transaction {transaction_id} (customer {customer_id}):\n\n"
        f"Transaction: {txn.get('type', 'N/A')} ${txn.get('amount', 0):,.2f} to {txn.get('name_dest', 'N/A')}\n"
        f"Customer profile: {profile.get('total_transactions', 0)} historical txns, avg ${avg_amount:,.2f}\n"
        f"Top categories: {known_categories[:3]}\n"
        f"Known countries: {known_countries[:5]}\n\n"
        f"Anomalies detected: {behavioral_flags}\n"
        f"Similar cases: {len(similar)} found, {similar_fraud_count} were fraud\n\n"
        f"Provide your behavioral analysis."
    )

    llm_result = call_llm(
        SYSTEM_PROMPT, user_msg, agent_type="behavior", agent_role="behavior"
    )

    for thought in llm_result.get("thoughts", []):
        emit({"type": "thought", "content": thought})

    if llm_result.get("anomaly_score"):
        anomaly_score = (anomaly_score + llm_result["anomaly_score"]) / 2
    if llm_result.get("behavioral_flags"):
        behavioral_flags.extend(llm_result["behavioral_flags"])
        behavioral_flags = list(set(behavioral_flags))

    reasoning = llm_result.get("reasoning", "")
    if not reasoning:
        reasoning = (
            f"Customer {customer_id} typically makes {profile.get('total_transactions', 0)} transactions "
            f"averaging ${avg_amount:,.2f}. This {txn.get('type', 'transaction')} of ${txn.get('amount', 0):,.2f} "
            f"exhibits {len(behavioral_flags)} anomalies. {similar_fraud_count}/{len(similar)} similar cases were fraud."
        )

    result = {
        "anomaly_score": round(anomaly_score, 2),
        "behavioral_flags": behavioral_flags,
        "customer_profile": profile,
        "similar_cases": similar[:5],
        "similar_total": similar_total,
        "fraud_match_ratio": round(fraud_match_ratio, 3),
        "similar_fraud_count": similar_fraud_count,
        "reasoning": reasoning,
        "retrieval_sources": sources,
        "latency_ms": int((time.time() - start) * 1000),
    }

    usage = llm_result.get("usage") or {}
    result["llm_usage"] = usage_record(
        "behavior",
        str(llm_result.get("model") or "claude-sonnet-4-5"),
        int(usage.get("input_tokens", 0)),
        int(usage.get("output_tokens", 0)),
    )

    if investigation_id:
        await append_audit(
            investigation_id,
            "behavior_agent",
            "agent_verdict",
            new_state={
                "anomaly_score": result["anomaly_score"],
                "behavioral_flags": result["behavioral_flags"],
            },
            reason=reasoning[:400] if reasoning else "",
        )

    emit(
        {
            "type": "verdict",
            "content": {
                "anomaly_score": result["anomaly_score"],
                "behavioral_flags": result["behavioral_flags"],
                "reasoning": result["reasoning"],
            },
        }
    )

    return result
