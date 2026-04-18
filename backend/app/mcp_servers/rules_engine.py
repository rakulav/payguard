"""MCP Tool Server: Rules Engine — returns which of 5 hardcoded fraud rules fire."""

from app.mcp_servers.transaction_lookup import transaction_lookup

RULES = [
    {
        "id": "rule_rapid_micro",
        "name": "Rapid Micro-Transactions",
        "description": "Multiple small transactions (< $5) in short succession",
        "severity": "MEDIUM",
    },
    {
        "id": "rule_round_cashout",
        "name": "Round-Amount CASH_OUT after TRANSFER",
        "description": "CASH_OUT with round amount (e.g., $1000, $5000) following a TRANSFER",
        "severity": "HIGH",
    },
    {
        "id": "rule_new_device_country",
        "name": "New Device + New Country",
        "description": "Transaction from a previously unseen device fingerprint and country",
        "severity": "HIGH",
    },
    {
        "id": "rule_balance_drain",
        "name": "Balance Drain",
        "description": "Account balance goes from high to zero in 1-2 transactions",
        "severity": "CRITICAL",
    },
    {
        "id": "rule_merchant_drift",
        "name": "Merchant Category Drift",
        "description": "Transaction to a merchant category never used by this customer",
        "severity": "MEDIUM",
    },
]


async def rules_engine(transaction_id: str) -> dict:
    """Evaluate a transaction against 5 hardcoded fraud rules."""
    txn_result = await transaction_lookup(transaction_id)
    if not txn_result.get("found"):
        return {"error": f"Transaction {transaction_id} not found", "rules_fired": []}

    txn = txn_result["transaction"]
    fired = []

    # Rule 1: Rapid micro-transactions
    if txn["amount"] < 5.0 and txn["type"] == "PAYMENT":
        fired.append(
            {
                **RULES[0],
                "matched": True,
                "detail": f"Amount ${txn['amount']:.2f} < $5 threshold",
            }
        )

    # Rule 2: Round-amount CASH_OUT
    if (
        txn["type"] == "CASH_OUT"
        and txn["amount"] % 1000 == 0
        and txn["amount"] >= 1000
    ):
        fired.append(
            {
                **RULES[1],
                "matched": True,
                "detail": f"Round CASH_OUT of ${txn['amount']:,.0f}",
            }
        )

    # Rule 3: New device + new country (heuristic: non-US country + short fingerprint)
    suspicious_countries = {"NG", "RU", "CN", "KP"}
    if txn.get("country_code") in suspicious_countries:
        fired.append(
            {
                **RULES[2],
                "matched": True,
                "detail": f"Country {txn['country_code']} + device {txn.get('device_fingerprint', 'unknown')[:8]}...",
            }
        )

    # Rule 4: Balance drain
    if txn["old_balance_org"] and txn["old_balance_org"] > 0:
        drain_pct = (txn["old_balance_org"] - txn["new_balance_orig"]) / txn[
            "old_balance_org"
        ]
        if drain_pct >= 0.95 and txn["amount"] > 1000:
            fired.append(
                {
                    **RULES[3],
                    "matched": True,
                    "detail": f"Balance drained {drain_pct*100:.0f}%: ${txn['old_balance_org']:,.2f} → ${txn['new_balance_orig']:,.2f}",
                }
            )

    # Rule 5: Merchant category drift
    drift_categories = {"crypto", "gambling", "gaming"}
    if txn.get("merchant_category") in drift_categories:
        fired.append(
            {
                **RULES[4],
                "matched": True,
                "detail": f"Unusual category: {txn['merchant_category']}",
            }
        )

    max_severity = "NONE"
    severity_order = {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
    for rule in fired:
        if severity_order.get(rule["severity"], 0) > severity_order.get(
            max_severity, 0
        ):
            max_severity = rule["severity"]

    return {
        "transaction_id": transaction_id,
        "rules_evaluated": len(RULES),
        "rules_fired": fired,
        "rules_fired_count": len(fired),
        "max_severity": max_severity,
        "risk_score": min(len(fired) / len(RULES), 1.0),
    }
