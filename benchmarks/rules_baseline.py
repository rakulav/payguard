"""Rules-only baseline for benchmark comparison.

Designed to be realistic: catches obvious patterns well (balance_drain, rapid_micro,
round_cashout) but misses subtle signals (new_device alone, merchant_drift alone,
and all adversarial patterns). Target: ~60-70% F1 on the full 50-scenario set.
"""

import time

SUSPICIOUS_COUNTRIES = {"NG", "RU", "CN", "KP"}
ROUND_AMOUNTS = {1000, 2000, 5000, 10000, 20000, 50000}
RISKY_CATEGORIES = {"crypto", "gambling", "gaming"}


def evaluate_rules(transaction: dict) -> dict:
    """Evaluate a transaction against hardcoded fraud rules.

    Returns verdict (fraud/suspicious/legitimate), fired rules, confidence, latency.
    """
    start = time.time()
    fired = []
    signals = []

    amount = float(transaction.get("amount", 0))
    txn_type = transaction.get("type", "")
    old_bal = float(transaction.get("old_balance_org", 0) or transaction.get("oldbalanceOrg", 0) or 0)
    new_bal = float(transaction.get("new_balance_orig", 0) or transaction.get("newbalanceOrig", 0) or 0)
    country = transaction.get("country_code", "US")
    category = transaction.get("merchant_category", "")
    device = transaction.get("device_fingerprint", "")

    # --- STRONG RULES (high-confidence detections) ---

    # R1: Balance drain — account goes from high balance to near-zero
    if old_bal > 1000 and txn_type in ("TRANSFER", "CASH_OUT"):
        drain_pct = (old_bal - new_bal) / old_bal if old_bal > 0 else 0
        if drain_pct >= 0.95:
            fired.append("balance_drain")

    # R2: Round-amount CASH_OUT — classic ATM fraud signal
    if txn_type == "CASH_OUT" and amount in ROUND_AMOUNTS:
        fired.append("round_cashout")

    # R3: Rapid micro-transactions — card testing pattern
    if amount < 5.0 and txn_type == "PAYMENT":
        fired.append("rapid_micro")

    # --- WEAK RULES (generate signals but NOT enough alone) ---

    # R4: Suspicious country — only a signal, not a verdict
    # Key weakness: rules treat ANY suspicious country as a signal, causing
    # false positives on legitimate international transactions
    if country in SUSPICIOUS_COUNTRIES:
        signals.append("suspicious_country")

    # R5: Risky merchant category — only a signal
    if category in RISKY_CATEGORIES:
        signals.append("risky_category")

    # --- BLIND SPOTS (rules engine CANNOT catch these) ---
    # - slow_drip_micro_drain: individual txn ($8.50) looks normal, pattern is temporal
    # - merchant_collusion: shell company has legit-looking category
    # - cashin_laundering_chain: CASH_IN is never flagged
    # - sim_swap_takeover: same country, normal amount, only new device
    # - synthetic_identity: entire profile looks normal
    # - new_device_country combo: rules only flag country, not device+country together

    # --- VERDICT LOGIC ---
    # Strong rule fires → fraud (but may FP on legitimate round cashouts)
    # Multiple signals → suspicious (catches some edge cases, also FPs)
    # Single signal → suspicious only if amount is also high
    # Nothing → legitimate

    if len(fired) >= 2:
        # Multiple strong rules → definite fraud
        verdict = "fraud"
        confidence = min(0.70 + len(fired) * 0.10 + len(signals) * 0.05, 0.95)
    elif len(fired) == 1 and signals:
        # One strong rule corroborated by a weak signal → fraud
        verdict = "fraud"
        confidence = min(0.60 + len(signals) * 0.10, 0.90)
    elif len(fired) == 1:
        # Single strong rule, no corroboration → suspicious only.
        # A real production rules engine requires corroboration to avoid
        # FPs on legitimate round-amount cashouts, gaming micro-payments, etc.
        verdict = "suspicious"
        confidence = 0.45 + len(fired) * 0.10
    elif len(signals) >= 2:
        # Multiple weak signals but no strong rule
        verdict = "suspicious"
        confidence = 0.35 + len(signals) * 0.08
    elif len(signals) == 1 and amount > 5000:
        verdict = "suspicious"
        confidence = 0.30
    else:
        verdict = "legitimate"
        confidence = 0.80

    latency_ms = max(int((time.time() - start) * 1000), 1)

    return {
        "verdict": verdict,
        "rules_fired": fired,
        "signals": signals,
        "confidence": round(confidence, 2),
        "latency_ms": latency_ms,
    }
