"""Synthesis Agent: final verdict from triage + behavior evidence (LLM + calibrated fallback)."""

import json
import re
import time
from pathlib import Path
from typing import Callable

from app.llm import call_llm
from app.mcp_servers.evidence_writer import evidence_writer
from app.audit_service import append_audit
from app.cost_tracker import usage_record

SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "synthesis.txt").read_text()

_OPENING_SYNTHESIS_THOUGHT = "Compiling evidence from triage and behavioral analysis..."


def _fraud_match_ratio(behavior_result: dict | None) -> float:
    if not behavior_result:
        return 0.0
    raw = behavior_result.get("fraud_match_ratio")
    if raw is not None:
        return float(raw)
    tot = int(behavior_result.get("similar_total") or 0)
    sfc = int(behavior_result.get("similar_fraud_count") or 0)
    return (sfc / tot) if tot else 0.0


def _extract_synthesis_json(llm_result: dict) -> dict | None:
    parts = []
    for t in llm_result.get("thoughts") or []:
        if isinstance(t, str):
            parts.append(t)
    blob = "\n".join(parts)
    m = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", blob)
    if m:
        try:
            o = json.loads(m.group(1))
            if isinstance(o, dict) and "verdict" in o:
                return o
        except json.JSONDecodeError:
            pass
    start = blob.rfind("{")
    while start != -1:
        try:
            obj, _ = json.JSONDecoder().raw_decode(blob, start)
            if isinstance(obj, dict) and "verdict" in obj:
                return obj
        except json.JSONDecodeError:
            pass
        start = blob.rfind("{", 0, start)
    return None


def _normalize_verdict(raw: str) -> str:
    v = (raw or "").lower().strip()
    if v in ("fraud", "likely_fraud"):
        return "fraud"
    if v in ("legitimate", "likely_legitimate"):
        return "legitimate"
    return "suspicious"


def _normalize_recommendation(
    raw: str | None, verdict: str, confidence: float
) -> tuple[str, bool]:
    r = (raw or "").lower().strip()
    if r == "freeze":
        return "freeze", True
    if r == "escalate":
        return "escalate", True
    if r in ("review", "monitor"):
        return "monitor", False
    if r in ("close", "clear"):
        return "clear", False
    if verdict == "fraud":
        if confidence >= 0.9:
            return "freeze", True
        return "escalate", True
    if verdict == "suspicious":
        return "monitor", False
    return "clear", False


def _heuristic_synthesis(
    triage_result: dict,
    behavior_result: dict | None,
) -> tuple[str, str, float, bool, str]:
    """Middle-ground classifier when LLM output is missing or mock."""
    rules = triage_result.get("rules_fired", [])
    triv = triage_result.get("verdict", "suspicious")
    tri_conf = float(triage_result.get("confidence", 0.55))
    strong = any(r.get("severity") in ("HIGH", "CRITICAL") for r in rules)
    crit = any(r.get("severity") == "CRITICAL" for r in rules)
    anomaly = float((behavior_result or {}).get("anomaly_score") or 0.0)
    ratio = _fraud_match_ratio(behavior_result)
    flags = len((behavior_result or {}).get("behavioral_flags") or [])
    txn = triage_result.get("transaction", {}) or {}
    mcc = (txn.get("merchant_category") or "").lower()
    risky_merchant = any(x in mcc for x in ("crypto", "gambling"))
    ob = float(txn.get("old_balance_org") or 0)
    nb = float(txn.get("new_balance_orig") or 0)
    drain = nb == 0 and ob > 800
    new_dev = str(txn.get("device_fingerprint") or "").startswith("new_dev")

    pts = 0
    if crit:
        pts += 3
    elif strong:
        pts += 2
    elif rules:
        pts += 1
    if behavior_result:
        if anomaly >= 0.78:
            pts += 2
        elif anomaly >= 0.52:
            pts += 1
        if ratio >= 0.58:
            pts += 2
        elif ratio >= 0.30:
            pts += 1
        if flags >= 4:
            pts += 2
        elif flags >= 2:
            pts += 1
    if triv in ("likely_fraud",):
        pts += 1
    if risky_merchant and drain and (new_dev or flags >= 2):
        pts += 2

    reasoning = (
        f"heuristic pts={pts} triage={triv} strong_rules={strong} "
        f"anomaly={anomaly:.2f} fraud_ratio={ratio:.2f} flags={flags}"
    )

    if triv == "legitimate" and not rules and pts <= 1:
        return "legitimate", "clear", max(0.74, tri_conf), False, reasoning
    if triv in ("likely_legitimate", "legitimate") and not strong and pts <= 2:
        if pts <= 1:
            return "legitimate", "clear", max(0.78, tri_conf), False, reasoning
        return "suspicious", "monitor", min(max(0.58, tri_conf), 0.78), False, reasoning

    if pts >= 5 or (strong and pts >= 3) or crit:
        conf = min(0.82 + 0.03 * min(pts, 6), 0.95)
        rec = "freeze" if conf >= 0.9 or crit else "escalate"
        return "fraud", rec, conf, True, reasoning
    if pts >= 3:
        conf = min(0.78 + 0.025 * pts, 0.92)
        return "fraud", "escalate", conf, True, reasoning
    if pts >= 1:
        conf = min(max(0.58, tri_conf, anomaly * 0.85), 0.82)
        return "suspicious", "monitor", conf, False, reasoning
    return "legitimate", "clear", max(0.72, tri_conf), False, reasoning


def _merge_llm_parsed_and_guardrail(
    parsed: dict | None,
    triage_result: dict,
    behavior_result: dict | None,
    llm_mock: bool,
) -> tuple[str, str, float, bool, str]:
    h_v, h_r, h_c, h_req, h_reason = _heuristic_synthesis(
        triage_result, behavior_result
    )
    if parsed and not llm_mock:
        verdict = _normalize_verdict(str(parsed.get("verdict", "")))
        try:
            conf = float(parsed.get("confidence", 0.7))
        except (TypeError, ValueError):
            conf = 0.7
        conf = min(max(conf, 0.05), 0.99)
        rec, req = _normalize_recommendation(
            parsed.get("recommendation"), verdict, conf
        )
        reasoning = str(parsed.get("reasoning") or "")
        if verdict == "legitimate" and h_v == "fraud":
            verdict, rec, conf, req = h_v, h_r, max(conf, h_c), h_req
            reasoning = (reasoning + " | guardrail:" + h_reason).strip(" |")
        elif verdict == "legitimate" and h_v == "suspicious" and h_c >= 0.75:
            verdict, rec, conf, req = "suspicious", "monitor", max(conf, 0.62), False
            reasoning = (reasoning + " | guardrail:" + h_reason).strip(" |")
        return verdict, rec, conf, req, reasoning

    return h_v, h_r, h_c, h_req, h_reason


async def run_synthesis(
    transaction_id: str,
    investigation_id: str,
    triage_result: dict,
    behavior_result: dict | None,
    emit_event: Callable[[dict], None] | None = None,
) -> dict:
    """Synthesize all evidence into a final investigation report."""
    start = time.time()

    def emit(event: dict):
        if emit_event:
            emit_event({"agent": "synthesis", **event})

    emit({"type": "thought", "content": _OPENING_SYNTHESIS_THOUGHT})

    triage_confidence = float(triage_result.get("confidence", 0.5))
    triage_verdict = triage_result.get("verdict", "suspicious")
    rules_fired = triage_result.get("rules_fired", [])

    behavioral_flags = []
    behavior_score = 0.5
    if behavior_result:
        behavior_score = float(behavior_result.get("anomaly_score", 0.5))
        behavioral_flags = behavior_result.get("behavioral_flags", [])

    txn = triage_result.get("transaction", {})
    rules_text = (
        "\n".join(
            f"  - {r['name']} ({r['severity']}): {r.get('detail', 'N/A')}"
            for r in rules_fired
        )
        or "  None"
    )

    behavior_text = "Not run (triage indicated sufficient confidence without behavior)."
    if behavior_result:
        flags_text = ", ".join(behavioral_flags) or "None"
        behavior_text = (
            f"Anomaly score: {behavior_score:.2f}\n"
            f"  Flags: {flags_text}\n"
            f"  Similar fraud cases: {behavior_result.get('similar_fraud_count', 0)} found\n"
            f"  fraud_match_ratio: {_fraud_match_ratio(behavior_result):.3f}\n"
            f"  {behavior_result.get('reasoning', '')}"
        )

    user_msg = (
        f"Final synthesis for investigation {investigation_id}, transaction {transaction_id}.\n\n"
        f"TRIAGE:\n  verdict={triage_verdict}, confidence={triage_confidence:.2f}\n"
        f"  rules_fired ({len(rules_fired)}):\n{rules_text}\n\n"
        f"BEHAVIOR:\n{behavior_text}\n\n"
        f"Apply the decision framework in your system instructions. "
        f"End with ONLY the JSON object (verdict, confidence, recommendation, reasoning)."
    )

    llm_result = call_llm(
        SYSTEM_PROMPT, user_msg, agent_type="synthesis", agent_role="synthesis"
    )
    parsed = _extract_synthesis_json(llm_result)

    verdict, recommendation, confidence, requires_approval, syn_reason = (
        _merge_llm_parsed_and_guardrail(
            parsed, triage_result, behavior_result, bool(llm_result.get("mock"))
        )
    )
    confidence = round(float(confidence), 2)

    for thought in llm_result.get("thoughts", []):
        if isinstance(thought, str) and thought.strip() == _OPENING_SYNTHESIS_THOUGHT:
            continue
        emit({"type": "thought", "content": thought})

    summary = (
        f"**Investigation Report: {transaction_id}**\n\n"
        f"**Verdict: {verdict.upper()}** (Confidence: {confidence*100:.0f}%)\n\n"
        f"**Transaction Details:**\n"
        f"  Type: {txn.get('type', 'N/A')}, Amount: ${txn.get('amount', 0):,.2f}\n"
        f"  From: {txn.get('name_orig', 'N/A')} → To: {txn.get('name_dest', 'N/A')}\n"
        f"  Balance: ${txn.get('old_balance_org', 0):,.2f} → ${txn.get('new_balance_orig', 0):,.2f}\n"
        f"  Country: {txn.get('country_code', 'N/A')}, Category: {txn.get('merchant_category', 'N/A')}\n\n"
        f"**Rules Triggered:**\n{rules_text}\n\n"
        f"**Behavioral Analysis:**\n  {behavior_text}\n\n"
        f"**Signals (synthesis):** {syn_reason}\n\n"
        f"**Recommendation: {recommendation.upper()}**"
        + (" — Pending human approval" if requires_approval else "")
    )

    if llm_result.get("summary") and not llm_result.get("mock"):
        summary = llm_result["summary"]

    usage = llm_result.get("usage") or {}
    llm_usage = usage_record(
        "synthesis",
        str(llm_result.get("model") or "claude-sonnet-4-5"),
        int(usage.get("input_tokens", 0)),
        int(usage.get("output_tokens", 0)),
    )

    await append_audit(
        investigation_id,
        "synthesis_agent",
        "agent_verdict",
        new_state={
            "verdict": verdict,
            "confidence": confidence,
            "recommendation": recommendation,
        },
        reason=syn_reason[:500],
    )

    evidence_data = {
        "transaction_id": transaction_id,
        "triage_verdict": triage_verdict,
        "triage_confidence": triage_confidence,
        "rules_fired": [r.get("name", "") for r in rules_fired],
        "behavior_score": behavior_score,
        "behavioral_flags": behavioral_flags,
        "fraud_match_ratio": (
            _fraud_match_ratio(behavior_result) if behavior_result else None
        ),
        "summary": summary,
        "synthesis_reasoning": syn_reason,
    }

    emit(
        {
            "type": "tool_call",
            "content": f"Calling evidence_writer(investigation={investigation_id}, verdict={verdict}, recommendation={recommendation})",
        }
    )
    write_result = await evidence_writer(
        investigation_id=investigation_id,
        verdict=verdict,
        evidence=evidence_data,
        recommendation=recommendation,
        confidence=confidence,
    )
    emit(
        {
            "type": "tool_result",
            "content": f"Evidence written. Status: {write_result['status']}",
        }
    )

    if requires_approval:
        await append_audit(
            investigation_id,
            "synthesis_agent",
            "approval_requested",
            new_state={"verdict": verdict, "recommendation": recommendation},
            reason=f"severity_auto confidence={confidence}",
        )
        emit(
            {
                "type": "approval_required",
                "content": {
                    "investigation_id": investigation_id,
                    "recommendation": recommendation,
                    "verdict": verdict,
                    "summary": summary,
                },
            }
        )

    result = {
        "verdict": verdict,
        "confidence": confidence,
        "recommendation": recommendation,
        "summary": summary,
        "requires_approval": requires_approval,
        "evidence": evidence_data,
        "latency_ms": int((time.time() - start) * 1000),
        "llm_usage": llm_usage,
    }

    emit(
        {
            "type": "verdict",
            "content": {
                "verdict": result["verdict"],
                "confidence": result["confidence"],
                "recommendation": result["recommendation"],
                "requires_approval": result["requires_approval"],
            },
        }
    )

    return result
