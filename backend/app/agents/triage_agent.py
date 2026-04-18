"""Triage Agent: flags suspicious transactions using transaction_lookup + rules_engine."""

import time
from pathlib import Path
from typing import Callable

from app.llm import call_llm
from app.audit_service import append_audit
from app.cost_tracker import usage_record
from app.mcp_servers.transaction_lookup import transaction_lookup
from app.mcp_servers.rules_engine import rules_engine

SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "triage.txt").read_text()


async def run_triage(
    transaction_id: str,
    emit_event: Callable[[dict], None] | None = None,
    investigation_id: str | None = None,
) -> dict:
    """Run the triage agent on a transaction."""
    start = time.time()

    def emit(event: dict):
        if emit_event:
            emit_event({"agent": "triage", **event})

    emit(
        {
            "type": "thought",
            "content": f"Starting triage analysis for {transaction_id}...",
        }
    )

    # Tool call 1: transaction_lookup
    emit(
        {
            "type": "tool_call",
            "content": f"Calling transaction_lookup({transaction_id})",
        }
    )
    txn_result = await transaction_lookup(transaction_id)
    emit(
        {
            "type": "tool_result",
            "content": f"Transaction found: {txn_result.get('found', False)}",
        }
    )

    if not txn_result.get("found"):
        result = {
            "verdict": "inconclusive",
            "confidence": 0.0,
            "reasoning": f"Transaction {transaction_id} not found in database.",
            "rules_fired": [],
            "latency_ms": int((time.time() - start) * 1000),
        }
        emit({"type": "verdict", "content": result})
        return result

    # Tool call 2: rules_engine
    emit({"type": "tool_call", "content": f"Calling rules_engine({transaction_id})"})
    rules_result = await rules_engine(transaction_id)
    emit(
        {
            "type": "tool_result",
            "content": f"Rules fired: {rules_result['rules_fired_count']}/{rules_result['rules_evaluated']}, max severity: {rules_result['max_severity']}",
        }
    )

    txn = txn_result["transaction"]

    # Call LLM for reasoning (or use mock)
    user_msg = (
        f"Analyze transaction {transaction_id}:\n"
        f"Type: {txn['type']}, Amount: ${txn['amount']:,.2f}\n"
        f"From: {txn['name_orig']} (balance: ${txn['old_balance_org']:,.2f} → ${txn['new_balance_orig']:,.2f})\n"
        f"To: {txn['name_dest']}\n"
        f"Country: {txn.get('country_code', 'N/A')}, Category: {txn.get('merchant_category', 'N/A')}\n"
        f"Rules fired: {[r['name'] + ' (' + r['severity'] + ')' for r in rules_result['rules_fired']]}\n"
        f"Risk score: {rules_result['risk_score']:.2f}\n\n"
        f"Provide your triage verdict."
    )

    llm_result = call_llm(
        SYSTEM_PROMPT, user_msg, agent_type="triage", agent_role="triage"
    )

    for thought in llm_result.get("thoughts", []):
        emit({"type": "thought", "content": thought})

    # Determine verdict from rules + LLM (bias precision on weak signals)
    rules_count = rules_result["rules_fired_count"]
    max_sev = rules_result["max_severity"]

    if rules_count == 0:
        verdict = "legitimate"
        confidence = 0.92
    elif max_sev == "CRITICAL" or rules_count >= 3:
        verdict = "likely_fraud"
        confidence = min(0.70 + rules_count * 0.08, 0.95)
    elif max_sev == "HIGH" or rules_count >= 2:
        verdict = "suspicious"
        confidence = min(0.50 + rules_count * 0.10, 0.85)
    elif rules_count >= 1 and max_sev == "MEDIUM":
        verdict = "likely_legitimate"
        confidence = 0.66 + rules_count * 0.02
    elif rules_count >= 1:
        verdict = "suspicious"
        confidence = 0.40 + rules_count * 0.10
    else:
        verdict = "likely_legitimate"
        confidence = 0.85

    if llm_result.get("confidence") and verdict != "legitimate":
        confidence = (confidence + llm_result["confidence"]) / 2

    reasoning = llm_result.get("reasoning", "")
    if not reasoning:
        reasoning = (
            f"Transaction {transaction_id}: {txn['type']} of ${txn['amount']:,.2f}. "
            f"{rules_count} fraud rules fired (max severity: {max_sev}). "
            f"Risk score: {rules_result['risk_score']:.2f}."
        )

    result = {
        "verdict": verdict,
        "confidence": round(confidence, 2),
        "reasoning": reasoning,
        "rules_fired": rules_result["rules_fired"],
        "rules_summary": f"{rules_count}/{rules_result['rules_evaluated']} rules fired, max severity: {max_sev}",
        "transaction": txn,
        "risk_score": rules_result["risk_score"],
        "latency_ms": int((time.time() - start) * 1000),
    }

    usage = llm_result.get("usage") or {}
    result["llm_usage"] = usage_record(
        "triage",
        str(llm_result.get("model") or "claude-haiku-4-5"),
        int(usage.get("input_tokens", 0)),
        int(usage.get("output_tokens", 0)),
    )

    if investigation_id:
        await append_audit(
            investigation_id,
            "triage_agent",
            "agent_verdict",
            new_state={
                "verdict": result["verdict"],
                "confidence": result["confidence"],
            },
            reason=result.get("rules_summary", "")[:400],
        )

    emit(
        {
            "type": "verdict",
            "content": {
                "verdict": result["verdict"],
                "confidence": result["confidence"],
                "reasoning": result["reasoning"],
            },
        }
    )

    return result
