"""Synthesis Agent: final verdict from triage + behavior evidence (LLM + calibrated fallback)."""

import json
import re
import time
from pathlib import Path
from typing import Callable

from sqlalchemy import text

from app.llm import call_llm
from app.mcp_servers.evidence_writer import evidence_writer
from app.audit_service import append_audit
from app.cost_tracker import usage_record
from app.db import sync_engine

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


def _profile_known(
    behavior_result: dict | None,
) -> tuple[list[str], list[str], int]:
    if not behavior_result:
        return [], [], 0
    profile = behavior_result.get("customer_profile") or {}
    top = profile.get("top_categories") or []
    countries = profile.get("countries") or []
    known_cats: list[str] = []
    for c in top:
        if isinstance(c, dict):
            known_cats.append(str(c.get("category") or ""))
        else:
            known_cats.append(str(c))
    known_cc: list[str] = []
    for c in countries:
        if isinstance(c, dict):
            known_cc.append(str(c.get("country") or ""))
        else:
            known_cc.append(str(c))
    ntx = int(profile.get("total_transactions") or 0)
    return known_cats, known_cc, ntx


def _flags_blob(behavior_result: dict | None) -> str:
    if not behavior_result:
        return ""
    parts = [str(f).lower() for f in (behavior_result.get("behavioral_flags") or [])]
    return " ".join(parts)


def _drain_fraction(txn: dict) -> float:
    ob = float(txn.get("old_balance_org") or 0)
    nb = float(txn.get("new_balance_orig") or 0)
    if ob <= 0:
        return 0.0
    return (ob - nb) / ob


def _balance_remain_ratio(txn: dict) -> float:
    """new_balance / old_balance (origin account)."""
    ob = float(txn.get("old_balance_org") or 0)
    nb = float(txn.get("new_balance_orig") or 0)
    if ob <= 0:
        return 1.0
    return nb / ob


def _any_high_or_critical(rules: list) -> bool:
    return any(r.get("severity") in ("HIGH", "CRITICAL") for r in rules)


def _pg_count_customer_country(name_orig: str, country_code: str) -> int:
    if not name_orig or not country_code:
        return 9999
    try:
        with sync_engine.connect() as conn:
            return int(
                conn.execute(
                    text(
                        "SELECT COUNT(*)::int FROM transactions "
                        "WHERE name_orig = :o AND country_code = :cc"
                    ),
                    {"o": name_orig, "cc": country_code},
                ).scalar_one()
            )
    except Exception:
        return 9999


def _pg_count_customer_device(name_orig: str, device_fp: str) -> int:
    if not name_orig or not device_fp:
        return 9999
    try:
        with sync_engine.connect() as conn:
            return int(
                conn.execute(
                    text(
                        "SELECT COUNT(*)::int FROM transactions "
                        "WHERE name_orig = :o AND device_fingerprint = :d"
                    ),
                    {"o": name_orig, "d": device_fp},
                ).scalar_one()
            )
    except Exception:
        return 9999


def _pg_count_customer_category(name_orig: str, merchant_category: str) -> int:
    if not name_orig or not merchant_category:
        return 9999
    try:
        with sync_engine.connect() as conn:
            return int(
                conn.execute(
                    text(
                        "SELECT COUNT(*)::int FROM transactions "
                        "WHERE name_orig = :o AND merchant_category = :m"
                    ),
                    {"o": name_orig, "m": merchant_category},
                ).scalar_one()
            )
    except Exception:
        return 9999


def _pg_top_two_categories(name_orig: str) -> list[str]:
    if not name_orig:
        return []
    try:
        with sync_engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT merchant_category FROM transactions WHERE name_orig = :o "
                    "GROUP BY merchant_category ORDER BY COUNT(*) DESC LIMIT 2"
                ),
                {"o": name_orig},
            ).fetchall()
        return [str(r[0]) for r in rows if r[0]]
    except Exception:
        return []


def _pg_customer_total_txns(name_orig: str) -> int:
    if not name_orig:
        return 0
    try:
        with sync_engine.connect() as conn:
            return int(
                conn.execute(
                    text("SELECT COUNT(*)::int FROM transactions WHERE name_orig = :o"),
                    {"o": name_orig},
                ).scalar_one()
            )
    except Exception:
        return 0


def _pg_avg_amount(name_orig: str) -> float:
    if not name_orig:
        return 0.0
    try:
        with sync_engine.connect() as conn:
            v = conn.execute(
                text(
                    "SELECT COALESCE(AVG(amount), 0)::float FROM transactions "
                    "WHERE name_orig = :o"
                ),
                {"o": name_orig},
            ).scalar_one()
        return float(v or 0.0)
    except Exception:
        return 0.0


def _adversarial_patterns_matched(
    triage_result: dict,
    behavior_result: dict | None,
) -> list[str]:
    """Return human-readable pattern ids for ADVERSARIAL_OVERRIDE (pre-score)."""
    txn = triage_result.get("transaction", {}) or {}
    rules = triage_result.get("rules_fired", [])
    matched: list[str] = []
    fb = _flags_blob(behavior_result)
    known_cats, known_cc, ntx = _profile_known(behavior_result)
    cust = str(txn.get("name_orig") or "")
    cc = str(txn.get("country_code") or "")
    cat = str(txn.get("merchant_category") or "")
    fp_raw = str(txn.get("device_fingerprint") or "")
    fp = fp_raw.lower()

    # 1 — Balance drain >80% to new jurisdiction
    if _drain_fraction(txn) > 0.8:
        novel = (
            "new_country" in fb
            or "category_drift" in fb
            or (cc and _pg_count_customer_country(cust, cc) == 1)
            or (cat and _pg_count_customer_category(cust, cat) == 1)
            or (bool(cc) and known_cc and cc not in known_cc)
            or (bool(cat) and known_cats and cat not in known_cats)
        )
        if novel:
            matched.append("BALANCE_DRAIN_TO_NEW_JURISDICTION")

    # 2 — Concentrated history + risky category + 3x average
    if behavior_result:
        profile = behavior_result.get("customer_profile") or {}
        top = profile.get("top_categories") or []
        if ntx >= 5:
            counts: list[int] = []
            for b in top:
                if isinstance(b, dict):
                    counts.append(int(b.get("count") or 0))
                else:
                    counts.append(0)
            total_bucket = sum(counts) or ntx
            top2 = sum(sorted(counts, reverse=True)[:2])
            concentrated = len(top) <= 2 or (
                total_bucket > 0 and (top2 / total_bucket) >= 0.70
            )
            mcc = (txn.get("merchant_category") or "").lower()
            risky = any(x in mcc for x in ("crypto", "gambling", "gaming"))
            avg_amt = float(profile.get("avg_amount") or 0) or 100.0
            amt = float(txn.get("amount") or 0)
            if concentrated and risky and amt >= 3 * avg_amt:
                matched.append("CATEGORY_DRIFT_WITH_AMOUNT_ANOMALY")

    # 2b — Same pattern using Postgres only (behavior/profile may be skipped or stale)
    mc_raw = str(txn.get("merchant_category") or "")
    if (
        str(txn.get("type") or "").upper() == "PAYMENT"
        and mc_raw.lower() in ("crypto", "gambling", "gaming")
        and cust
    ):
        tot = _pg_customer_total_txns(cust)
        cat_cnt = _pg_count_customer_category(cust, mc_raw)
        avg_pg = _pg_avg_amount(cust) or 100.0
        if tot >= 10 and cat_cnt == 1 and float(txn.get("amount") or 0) >= 3 * avg_pg:
            matched.append("CATEGORY_DRIFT_WITH_AMOUNT_ANOMALY")

    # 3 — Rapid micro burst (rules engine marks single-txn leg of burst)
    if any(r.get("id") == "rule_rapid_micro" for r in rules):
        matched.append("RAPID_MICRO_TRANSFER_BURST")

    # 4 — Device + geography + category triple shift
    top2_pg = _pg_top_two_categories(cust)
    novel_device = (
        fp.startswith("new_dev")
        or "new_device" in fb
        or (fp_raw and _pg_count_customer_device(cust, fp_raw) == 1)
    )
    new_geo = (
        "new_country" in fb
        or (cc and _pg_count_customer_country(cust, cc) == 1)
        or (bool(cc) and known_cc and cc not in known_cc)
    )
    cat_shift = (
        "category_drift" in fb
        or (bool(cat) and known_cats and cat not in known_cats)
        or (bool(cat) and top2_pg and cat not in top2_pg)
    )
    if novel_device and new_geo and cat_shift:
        matched.append("DEVICE_GEO_CATEGORY_TRIPLE_SHIFT")

    # 5 — Drain (balance ratio) with no HIGH/CRITICAL rules
    if _balance_remain_ratio(txn) < 0.2 and float(txn.get("old_balance_org") or 0) > 0:
        if not _any_high_or_critical(rules):
            matched.append("BALANCE_DRAIN_WITH_LOW_RULES_SCORE")

    return list(dict.fromkeys(matched))


def _novel_risk_category_bonus(
    txn: dict,
    behavior_result: dict | None,
) -> float:
    """Step 1: +0.15 for crypto/gambling without prior category, or country not on profile."""
    if not behavior_result:
        return 0.0
    profile = behavior_result.get("customer_profile") or {}
    top_categories = profile.get("top_categories") or []
    countries = profile.get("countries") or []
    if not top_categories and not countries:
        return 0.0

    known_cats: list[str] = []
    for c in top_categories:
        if isinstance(c, dict):
            known_cats.append(str(c.get("category") or ""))
        else:
            known_cats.append(str(c))
    known_cc: list[str] = []
    for c in countries:
        if isinstance(c, dict):
            known_cc.append(str(c.get("country") or ""))
        else:
            known_cc.append(str(c))

    mcc = (txn.get("merchant_category") or "").lower()
    cat = txn.get("merchant_category") or ""
    cc = str(txn.get("country_code") or "")

    targets_crypto_gambling = any(x in mcc for x in ("crypto", "gambling", "gaming"))
    no_prior_cat = bool(cat) and cat not in known_cats
    unusual_country = bool(cc) and cc not in known_cc

    if targets_crypto_gambling and no_prior_cat:
        return 0.15
    if unusual_country:
        return 0.15
    return 0.0


def _compute_fraud_score(
    triage_result: dict,
    behavior_result: dict | None,
) -> tuple[float, str]:
    """Step 1: weighted sum; returns (capped total, audited breakdown string)."""
    rules = triage_result.get("rules_fired", [])
    txn = triage_result.get("transaction", {}) or {}

    equiv_high = sum(1 for r in rules if r.get("severity") in ("HIGH", "CRITICAL"))
    high_part = min(0.60, 0.30 * equiv_high)
    med_n = sum(1 for r in rules if r.get("severity") == "MEDIUM")
    med_part = min(0.30, 0.15 * med_n)

    anomaly = 0.0
    n_flags = 0
    if behavior_result:
        try:
            anomaly = float(behavior_result.get("anomaly_score") or 0.0)
        except (TypeError, ValueError):
            anomaly = 0.0
        n_flags = len(behavior_result.get("behavioral_flags") or [])
    beh_part = anomaly * 0.40

    extra_flags = max(0, n_flags - 1)
    flag_part = min(0.50, 0.10 * extra_flags)

    sfc = 0
    if behavior_result:
        try:
            sfc = int(behavior_result.get("similar_fraud_count") or 0)
        except (TypeError, ValueError):
            sfc = 0
    if sfc >= 3:
        sim_part = 0.30
    elif sfc == 2:
        sim_part = 0.20
    elif sfc == 1:
        sim_part = 0.10
    else:
        sim_part = 0.0

    bonus = _novel_risk_category_bonus(txn, behavior_result)

    raw = high_part + med_part + beh_part + flag_part + sim_part + bonus
    fraud_score = min(1.0, raw)

    audit = (
        f"fraud_score={fraud_score:.3f} "
        f"(HIGHequiv×{equiv_high}:+{high_part:.3f}, MED×{med_n}:+{med_part:.3f}, "
        f"anomaly×0.40:+{beh_part:.3f}, flags_beyond_first:+{flag_part:.3f}, "
        f"similar_fraud_top10(n={sfc}):+{sim_part:.3f}, novel_risk_bonus:+{bonus:.3f})"
    )
    return fraud_score, audit


def _verdict_conf_from_fraud_score(fraud_score: float) -> tuple[str, float]:
    """Step 2: map score to verdict and confidence."""
    if fraud_score >= 0.65:
        conf = min(0.95, fraud_score + 0.10)
        return "fraud", round(min(max(conf, 0.05), 0.99), 2)
    if fraud_score >= 0.35:
        return "suspicious", round(min(max(fraud_score, 0.05), 0.99), 2)
    conf = min(0.90, 1.0 - fraud_score)
    return "legitimate", round(min(max(conf, 0.05), 0.99), 2)


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


def _merge_llm_parsed_and_guardrail(
    parsed: dict | None,
    triage_result: dict,
    behavior_result: dict | None,
    llm_mock: bool,
    *,
    precomputed_score: tuple[float, str] | None = None,
    adversarial_override: str | None = None,
) -> tuple[str, str, float, bool, str]:
    crit = any(
        r.get("severity") == "CRITICAL" for r in triage_result.get("rules_fired", [])
    )

    if adversarial_override:
        audit = (
            f"ADVERSARIAL_OVERRIDE:{adversarial_override} "
            f"(fraud_score skipped; verdict=fraud confidence=0.75 per policy)"
        )
        verdict, conf = "fraud", 0.75
        s_rec, s_req = _normalize_recommendation(None, verdict, conf)
        if parsed and not llm_mock:
            rec, req = _normalize_recommendation(
                parsed.get("recommendation"), verdict, conf
            )
            reasoning = str(parsed.get("reasoning") or "").strip()
            reasoning = f"{audit} | {reasoning}".strip(" |")
            return verdict, rec, conf, req, reasoning
        return verdict, s_rec, conf, s_req, audit

    if precomputed_score is not None:
        fraud_score, audit = precomputed_score
    else:
        fraud_score, audit = _compute_fraud_score(triage_result, behavior_result)
    sv, sc = _verdict_conf_from_fraud_score(fraud_score)
    s_rec, s_req = _normalize_recommendation(None, sv, sc)
    if sv == "fraud" and crit:
        s_rec, s_req = "freeze", True

    if parsed and not llm_mock:
        verdict, conf = sv, sc
        rec, req = _normalize_recommendation(
            parsed.get("recommendation"), verdict, conf
        )
        if verdict == "fraud" and crit:
            rec, req = "freeze", True
        reasoning = str(parsed.get("reasoning") or "").strip()
        reasoning = f"{audit} | {reasoning}".strip(" |")

        return verdict, rec, conf, req, reasoning

    return sv, s_rec, sc, s_req, audit + " | step2_mapping"


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

    adv_patterns = _adversarial_patterns_matched(triage_result, behavior_result)
    adversarial_tag = ", ".join(adv_patterns) if adv_patterns else None
    if adversarial_tag:
        fraud_score_value = None
        step1_audit = (
            f"ADVERSARIAL_OVERRIDE matched: {adversarial_tag}. "
            f"Skip fraud_score Steps 1–3; JSON must be verdict=fraud, confidence=0.75, "
            f"and reasoning must name which pattern(s) matched."
        )
    else:
        fraud_score_value, step1_audit = _compute_fraud_score(
            triage_result, behavior_result
        )
    user_msg = (
        f"Final synthesis for investigation {investigation_id}, transaction {transaction_id}.\n\n"
        f"TRIAGE:\n  verdict={triage_verdict}, confidence={triage_confidence:.2f}\n"
        f"  rules_fired ({len(rules_fired)}):\n{rules_text}\n\n"
        f"BEHAVIOR:\n{behavior_text}\n\n"
        f"Backend directive (repeat key facts in reasoning): {step1_audit}\n\n"
        f"Follow your system instructions (adversarial override if applicable, else Steps 1–3). "
        f"End with ONLY the JSON object "
        f"(verdict, confidence, recommendation, reasoning)."
    )

    llm_result = call_llm(
        SYSTEM_PROMPT, user_msg, agent_type="synthesis", agent_role="synthesis"
    )
    parsed = _extract_synthesis_json(llm_result)

    verdict, recommendation, confidence, requires_approval, syn_reason = (
        _merge_llm_parsed_and_guardrail(
            parsed,
            triage_result,
            behavior_result,
            bool(llm_result.get("mock")),
            precomputed_score=(
                (fraud_score_value, step1_audit)
                if fraud_score_value is not None
                else None
            ),
            adversarial_override=adversarial_tag,
        )
    )
    confidence = round(float(confidence), 2)

    for thought in llm_result.get("thoughts", []):
        if isinstance(thought, str) and thought.strip() == _OPENING_SYNTHESIS_THOUGHT:
            continue
        emit({"type": "thought", "content": thought})

    summary = (
        f"**Investigation Report: {transaction_id}**\n\n"
        f"**Verdict: {verdict.upper()}** (Confidence: {confidence * 100:.0f}%)\n\n"
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
        "fraud_score": (
            None if fraud_score_value is None else round(float(fraud_score_value), 3)
        ),
        "adversarial_override": adversarial_tag,
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
