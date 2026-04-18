"""Claude wrapper with MOCK_LLM fallback, role-based models, and usage/cost metadata."""

import os
import time
import random
from typing import Any, Literal

from app.config import get_settings
from app.cost_tracker import usage_cost_usd

settings = get_settings()

AgentRole = Literal["triage", "behavior", "synthesis"]

MODEL_BY_ROLE: dict[AgentRole, str] = {
    "triage": "claude-haiku-4-5",
    "behavior": "claude-sonnet-4-5",
    "synthesis": "claude-sonnet-4-5",
}

MOCK_RESPONSES = {
    "triage": {
        "thoughts": [
            "Analyzing transaction details and checking against fraud rules...",
            "Transaction shows TRANSFER of full account balance ($12,000 → $0).",
            "Rules engine flagged: balance_drain pattern detected.",
            "Amount exceeds threshold and destination is a new merchant.",
        ],
        "tool_calls": [
            {
                "tool": "transaction_lookup",
                "args": {"transaction_id": "TXN_48213"},
                "result": "Found: TRANSFER $12,000.00 from C_1042 to M_8891",
            },
            {
                "tool": "rules_engine",
                "args": {"transaction_id": "TXN_48213"},
                "result": "Rules fired: balance_drain (severity=HIGH), large_transfer (severity=MEDIUM)",
            },
        ],
        "verdict": "suspicious",
        "confidence": 0.82,
        "reasoning": "Transaction drains entire account balance in a single TRANSFER to a crypto merchant. Balance-drain and large-transfer rules both fired. Confidence 0.82 — escalating to behavioral analysis.",
    },
    "behavior": {
        "thoughts": [
            "Examining customer C_1042's transaction history...",
            "Customer typically makes small PAYMENT transactions ($50-200) to grocery/dining.",
            "This TRANSFER of $12,000 to a crypto merchant is highly anomalous.",
            "Searching for similar fraud patterns in the database...",
            "Found 7 similar cases — 6 were confirmed fraud (86% match rate).",
            "New device fingerprint and country (RU) not seen in customer history.",
        ],
        "tool_calls": [
            {
                "tool": "customer_profile",
                "args": {"customer_id": "C_1042"},
                "result": "Profile: 47 transactions, avg $127.50, top categories: grocery(60%), dining(25%). Countries: US(100%). Last activity: 2 days ago.",
            },
            {
                "tool": "similar_fraud_search",
                "args": {"k": 10},
                "result": "Found 7 similar fraud cases. Top match: TXN_15330 (balance_drain, confirmed fraud, 94% similarity). Sources: pgvector(3), qdrant(2), opensearch(2).",
            },
        ],
        "anomaly_score": 0.94,
        "behavioral_flags": [
            "new_country_RU",
            "new_device",
            "category_drift_to_crypto",
            "amount_15x_average",
            "full_balance_drain",
        ],
        "reasoning": "Customer C_1042 has no history of transfers, crypto merchants, or activity from Russia. This transaction deviates across 5 behavioral dimensions. 7 similar past cases were overwhelmingly fraudulent.",
    },
    "synthesis": {
        "thoughts": [
            "Triage: balance_drain + large_transfer rules fired (confidence 0.82)",
            "Behavior: 5 anomaly flags, 94% similarity to known fraud, 0.94 anomaly score",
            "Combined evidence strongly indicates fraud. Recommending account freeze.",
            '```json\n{"verdict": "fraud", "confidence": 0.92, "recommendation": "escalate", "reasoning": "Convergent HIGH rules + behavioral anomalies."}\n```',
        ],
        "tool_calls": [
            {
                "tool": "evidence_writer",
                "args": {"verdict": "fraud", "recommendation": "freeze"},
                "result": "Evidence report written. Investigation marked for approval.",
            },
        ],
        "verdict": "fraud",
        "confidence": 0.96,
        "recommendation": "freeze",
        "summary": "**Investigation Report: TXN_48213**\n\n**Verdict: FRAUD** (Confidence: 96%)\n\n**Evidence Summary:**\n1. Full balance drain: $12,000 → $0 in single TRANSFER\n2. Destination: crypto merchant M_8891 (never used by customer)\n3. Origin country: Russia (customer history: 100% US)\n4. New device fingerprint not matching any prior sessions\n5. Amount is 15x customer's average transaction ($127.50)\n6. 7 similar historical cases, 86% confirmed fraud rate\n\n**Rules Triggered:** balance_drain (HIGH), large_transfer (MEDIUM)\n\n**Recommendation:** FREEZE ACCOUNT — Pending human approval\n\nThis transaction exhibits classic balance-drain fraud: a compromised account rapidly exfiltrated via a single large transfer to an unusual destination. The behavioral analysis confirms this is strongly inconsistent with the account holder's established patterns.",
        "requires_approval": True,
    },
}


def _get_client():
    try:
        import anthropic

        return anthropic.Anthropic(api_key=settings.anthropic_api_key)
    except Exception:
        return None


def _should_mock() -> bool:
    if settings.mock_llm or os.environ.get("MOCK_LLM", "0") == "1":
        return True
    if (
        not settings.anthropic_api_key
        or settings.anthropic_api_key == "sk-ant-your-key-here"
    ):
        return True
    return False


def call_llm(
    system_prompt: str,
    user_message: str,
    tools: list[dict] | None = None,
    max_tokens: int = 4096,
    agent_type: str = "triage",
    agent_role: AgentRole | None = None,
) -> dict[str, Any]:
    """Call Claude with retry, or return mock response.

    agent_role selects model (Haiku triage, Sonnet behavior/synthesis).
    agent_type selects which mock template to use when mocked.
    """
    role: AgentRole = agent_role or (
        "triage"
        if agent_type == "triage"
        else "behavior" if agent_type == "behavior" else "synthesis"
    )
    model = MODEL_BY_ROLE.get(role, MODEL_BY_ROLE["synthesis"])

    if _should_mock():
        time.sleep(random.uniform(0.1, 0.5))
        base = {
            "mock": True,
            "model": model,
            **MOCK_RESPONSES.get(agent_type, MOCK_RESPONSES["triage"]),
        }
        base.setdefault("usage", {"input_tokens": 1200, "output_tokens": 400})
        it, ot = base["usage"]["input_tokens"], base["usage"]["output_tokens"]
        base["cost_usd"] = round(usage_cost_usd(model, it, ot), 6)
        return base

    client = _get_client()
    if client is None:
        base = {
            "mock": True,
            "model": model,
            **MOCK_RESPONSES.get(agent_type, MOCK_RESPONSES["triage"]),
        }
        base.setdefault("usage", {"input_tokens": 800, "output_tokens": 300})
        it, ot = base["usage"]["input_tokens"], base["usage"]["output_tokens"]
        base["cost_usd"] = round(usage_cost_usd(model, it, ot), 6)
        return base

    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            kwargs: dict[str, Any] = {
                "model": model,
                "max_tokens": max_tokens,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_message}],
            }
            if tools:
                kwargs["tools"] = tools

            response = client.messages.create(**kwargs)

            result: dict[str, Any] = {
                "mock": False,
                "model": model,
                "thoughts": [],
                "tool_calls": [],
            }
            for block in response.content:
                if block.type == "text":
                    result["thoughts"].append(block.text)
                elif block.type == "tool_use":
                    result["tool_calls"].append(
                        {
                            "tool": block.name,
                            "args": block.input,
                            "id": block.id,
                        }
                    )

            if response.stop_reason == "tool_use":
                result["needs_tool_response"] = True

            usage = getattr(response, "usage", None)
            if usage is not None:
                it = int(getattr(usage, "input_tokens", 0) or 0)
                ot = int(getattr(usage, "output_tokens", 0) or 0)
                result["usage"] = {"input_tokens": it, "output_tokens": ot}
                result["cost_usd"] = round(usage_cost_usd(model, it, ot), 6)
            else:
                result["usage"] = {"input_tokens": 0, "output_tokens": 0}
                result["cost_usd"] = 0.0

            return result

        except Exception as e:
            if attempt < max_retries:
                wait = 2**attempt
                time.sleep(wait)
            else:
                err = {
                    "mock": True,
                    "error": str(e),
                    "model": model,
                    **MOCK_RESPONSES.get(agent_type, MOCK_RESPONSES["triage"]),
                }
                err.setdefault("usage", {"input_tokens": 0, "output_tokens": 0})
                err["cost_usd"] = 0.0
                return err

    return {
        "mock": True,
        "model": model,
        **MOCK_RESPONSES.get(agent_type, MOCK_RESPONSES["triage"]),
    }


def get_mock_trace(agent_type: str, transaction_id: str = "TXN_48213") -> dict:
    """Get a complete mock agent trace for a specific agent type."""
    trace = MOCK_RESPONSES.get(agent_type, MOCK_RESPONSES["triage"]).copy()
    for tc in trace.get("tool_calls", []):
        if "transaction_id" in tc.get("args", {}):
            tc["args"]["transaction_id"] = transaction_id
    return trace
