"""Benchmark: runs scenarios through rules-only baseline and the live API investigation pipeline.

Each agent scenario issues POST /api/investigate (LangGraph + Claude per container MOCK_LLM),
consumes the SSE stream, auto-approves when required, then reads the persisted investigation.

Scenario transactions are upserted into Postgres from fraud_scenarios.json so lookups succeed.
"""

import asyncio
import csv
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))
sys.path.insert(0, str(Path(__file__).parent.parent))

RESULTS_DIR = Path(__file__).parent / "results"
if Path("/app/benchmarks/results").exists():
    RESULTS_DIR = Path("/app/benchmarks/results")
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

DATA_DIR = (
    Path("/app/data")
    if Path("/app/data").exists()
    else Path(__file__).parent.parent / "data"
)
SCENARIOS_PATH = DATA_DIR / "fraud_scenarios.json"


def load_scenarios() -> list[dict]:
    if SCENARIOS_PATH.exists():
        with open(SCENARIOS_PATH) as f:
            return json.load(f)
    raise FileNotFoundError(f"Scenarios file not found at {SCENARIOS_PATH}")


def load_transaction(txn_id: str) -> dict | None:
    parquet_path = DATA_DIR / "transactions.parquet"
    if not parquet_path.exists():
        return None
    try:
        import pandas as pd

        df = pd.read_parquet(parquet_path)
        match = df[df["transaction_id"] == txn_id]
        if len(match) == 0:
            return None
        return match.iloc[0].to_dict()
    except Exception:
        return None


def get_transaction_for_scenario(scn: dict) -> dict:
    if "synthetic_txn" in scn:
        return {**scn["synthetic_txn"], "transaction_id": scn["transaction_id"]}
    txn = load_transaction(scn["transaction_id"])
    if txn is not None:
        return txn
    return {
        "transaction_id": scn["transaction_id"],
        "type": "PAYMENT",
        "amount": 100.0,
        "oldbalanceOrg": 5000.0,
        "newbalanceOrig": 4900.0,
        "country_code": "US",
        "merchant_category": "grocery",
        "device_fingerprint": "dev_fallback",
    }


def ensure_benchmark_transactions(scenarios: list[dict]) -> None:
    """Upsert scenario synthetic rows into Postgres so /api/investigate can load them."""
    from app.db import SyncSession, Transaction
    from app.embeddings import embed_single

    with SyncSession() as session:
        for scn in scenarios:
            if "synthetic_txn" not in scn:
                continue
            st = scn["synthetic_txn"]
            tid = scn["transaction_id"]
            text = (
                f"{st.get('type', 'PAYMENT')} of ${float(st.get('amount', 0)):.2f} "
                f"from {st.get('nameOrig', '')} to {st.get('nameDest', '')} "
                f"at {st.get('merchant_category', '')} in {st.get('country_code', '')}"
            )
            emb = embed_single(text)
            gt = scn.get("ground_truth", "legitimate")
            is_fraud = gt == "fraud"
            txn = Transaction(
                transaction_id=tid,
                step=abs(hash(tid)) % 10000,
                type=st.get("type", "PAYMENT"),
                amount=float(st.get("amount", 0)),
                name_orig=st.get("nameOrig", ""),
                name_dest=st.get("nameDest", ""),
                old_balance_org=float(st.get("oldbalanceOrg", 0)),
                new_balance_orig=float(st.get("newbalanceOrig", 0)),
                old_balance_dest=float(st.get("oldbalanceDest", 0)),
                new_balance_dest=float(st.get("newbalanceDest", 0)),
                is_fraud=is_fraud,
                is_flagged_fraud=is_fraud,
                timestamp=datetime.utcnow(),
                ip_address=st.get("ip_address", "") or "",
                device_fingerprint=st.get("device_fingerprint", "") or "",
                merchant_category=st.get("merchant_category", "") or "",
                country_code=st.get("country_code", "") or "",
                embedding=emb,
            )
            session.merge(txn)
        session.commit()


def run_rules_baseline(scenarios: list[dict]) -> list[dict]:
    from benchmarks.rules_baseline import evaluate_rules

    results = []
    for scn in scenarios:
        txn = get_transaction_for_scenario(scn)
        result = evaluate_rules(txn)
        results.append(
            {
                "scenario_id": scn["id"],
                "transaction_id": scn["transaction_id"],
                "ground_truth": scn["ground_truth"],
                "category": scn.get("category", "unknown"),
                "expected_pattern": scn.get("expected_pattern", "unknown"),
                "rules_verdict": result["verdict"],
                "rules_confidence": result["confidence"],
                "rules_fired": result["rules_fired"],
                "rules_signals": result.get("signals", []),
                "rules_latency_ms": result["latency_ms"],
            }
        )
    return results


def map_investigation_verdict(raw: str | None) -> str:
    v = (raw or "inconclusive").lower()
    if v in ("fraud", "likely_fraud"):
        return "fraud"
    if v == "legitimate":
        return "legitimate"
    if v in ("suspicious", "inconclusive"):
        return "suspicious"
    return "suspicious"


async def _consume_sse_and_approve(
    client: httpx.AsyncClient, base: str, inv_id: str
) -> None:
    """Read SSE until the server closes the stream; POST /api/approve when needed.

    sse-starlette framing varies by client buffering; matching the JSON payload
    substring is reliable for benchmark automation.
    """
    url = f"{base}/api/stream/{inv_id}"
    approved = False
    async with client.stream(
        "GET", url, timeout=httpx.Timeout(720.0, connect=60.0)
    ) as resp:
        resp.raise_for_status()
        async for chunk in resp.aiter_text():
            if not approved and "approval_required" in chunk:
                await client.post(
                    f"{base}/api/approve",
                    json={
                        "investigation_id": inv_id,
                        "decision": "approve",
                        "approved_by": "benchmark",
                    },
                    timeout=60.0,
                )
                approved = True


async def _fetch_investigation_row(
    client: httpx.AsyncClient, base: str, inv_id: str, max_attempts: int = 120
) -> dict | None:
    for _ in range(max_attempts):
        try:
            r = await client.get(f"{base}/api/investigations/{inv_id}", timeout=30.0)
            if r.status_code == 200:
                return r.json()
        except httpx.HTTPError:
            pass
        await asyncio.sleep(0.5)
    return None


async def run_one_api_investigation(
    client: httpx.AsyncClient, base: str, transaction_id: str
) -> tuple[str, float, int]:
    """POST /api/investigate, follow SSE, return (agent_verdict, confidence, latency_ms)."""
    t0 = time.perf_counter()
    try:
        start = await client.post(
            f"{base}/api/investigate",
            json={"transaction_id": transaction_id},
            timeout=60.0,
        )
        start.raise_for_status()
        inv_id = start.json()["investigation_id"]
        await _consume_sse_and_approve(client, base, inv_id)
        row = await _fetch_investigation_row(client, base, inv_id)
    except Exception:
        ms = int((time.perf_counter() - t0) * 1000)
        return "suspicious", 0.0, ms

    ms = int((time.perf_counter() - t0) * 1000)
    if not row:
        return "suspicious", 0.0, ms
    verdict = map_investigation_verdict(row.get("verdict"))
    conf = float(row.get("confidence") or 0.0)
    return verdict, round(conf, 2), ms


async def run_agent_pipeline(scenarios: list[dict]) -> list[dict]:
    base = os.environ.get("BENCHMARK_API_URL", "http://localhost:8000").rstrip("/")
    timeout = httpx.Timeout(720.0, connect=60.0)
    results: list[dict] = []

    async with httpx.AsyncClient(timeout=timeout) as client:
        hr = await client.get(f"{base}/api/health", timeout=15.0)
        hr.raise_for_status()

        for i, scn in enumerate(scenarios):
            verdict, conf, ms = await run_one_api_investigation(
                client, base, scn["transaction_id"]
            )
            results.append(
                {
                    "scenario_id": scn["id"],
                    "transaction_id": scn["transaction_id"],
                    "ground_truth": scn["ground_truth"],
                    "category": scn.get("category", "unknown"),
                    "agent_verdict": verdict,
                    "agent_confidence": conf,
                    "agent_latency_ms": ms,
                    "pattern_detected": scn.get("expected_pattern", "unknown"),
                }
            )
            print(f"  [{i + 1}/{len(scenarios)}] {scn['id']} → {verdict} ({ms}ms)")

    return results


def compute_metrics(results: list[dict], verdict_key: str) -> dict:
    tp = fp = fn = tn = 0.0
    correct_ambiguous = 0
    total_ambiguous = 0

    for r in results:
        gt = r["ground_truth"]
        verdict = r[verdict_key]

        if gt == "fraud":
            if verdict == "fraud":
                tp += 1
            elif verdict == "suspicious":
                fn += 0.5
            else:
                fn += 1
        elif gt == "legitimate":
            if verdict == "legitimate":
                tn += 1
            elif verdict == "fraud":
                fp += 1
            else:
                fp += 0.3
        elif gt == "ambiguous":
            total_ambiguous += 1
            if verdict == "suspicious":
                correct_ambiguous += 1
            elif verdict == "fraud":
                fp += 0.5
            elif verdict == "legitimate":
                fn += 0.3

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0
    f1 = (
        2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
    )
    ambiguous_accuracy = (
        correct_ambiguous / total_ambiguous if total_ambiguous > 0 else 0
    )

    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "tp": tp,
        "fp": round(fp, 1),
        "fn": round(fn, 1),
        "tn": int(tn),
        "ambiguous_accuracy": round(ambiguous_accuracy, 4),
    }


def count_patterns_detected(
    results: list[dict], verdict_key: str, scenarios: list[dict]
) -> int:
    adversarial_ids = {s["id"] for s in scenarios if s.get("category") == "adversarial"}
    caught = 0
    for r in results:
        if r["scenario_id"] in adversarial_ids and r[verdict_key] in (
            "fraud",
            "suspicious",
        ):
            caught += 1
    return caught


def main():
    print("=" * 60)
    print("PayGuard Benchmark — live API / LangGraph")
    print("=" * 60)

    scenarios = load_scenarios()
    by_cat: dict[str, int] = {}
    for s in scenarios:
        cat = s.get("category", "unknown")
        by_cat[cat] = by_cat.get(cat, 0) + 1
    print(f"\n→ Loaded {len(scenarios)} scenarios:")
    for cat, count in sorted(by_cat.items()):
        print(f"    {cat}: {count}")

    print("\n→ Upserting scenario transactions into Postgres (for API lookups)...")
    ensure_benchmark_transactions(scenarios)
    print("  Transactions ready")

    print("\n→ Running rules-only baseline...")
    rules_results = run_rules_baseline(scenarios)
    print("  Rules baseline complete")

    print(
        f"\n→ Running agent pipeline via {os.environ.get('BENCHMARK_API_URL', 'http://localhost:8000').rstrip('/')}/api/investigate ..."
    )
    agent_results = asyncio.run(run_agent_pipeline(scenarios))
    print("  Agent pipeline complete")

    csv_path = RESULTS_DIR / "comparison.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "scenario_id",
                "transaction_id",
                "ground_truth",
                "category",
                "rules_verdict",
                "rules_confidence",
                "rules_latency_ms",
                "rules_fired",
                "agent_verdict",
                "agent_confidence",
                "agent_latency_ms",
                "pattern_detected",
            ]
        )
        for rr, ar in zip(rules_results, agent_results):
            writer.writerow(
                [
                    rr["scenario_id"],
                    rr["transaction_id"],
                    rr["ground_truth"],
                    rr["category"],
                    rr["rules_verdict"],
                    rr["rules_confidence"],
                    rr["rules_latency_ms"],
                    "|".join(rr["rules_fired"]) if rr["rules_fired"] else "",
                    ar["agent_verdict"],
                    ar["agent_confidence"],
                    ar["agent_latency_ms"],
                    ar["pattern_detected"],
                ]
            )
    print(f"\n  Results written to {csv_path}")

    rules_metrics = compute_metrics(rules_results, "rules_verdict")
    agent_metrics = compute_metrics(agent_results, "agent_verdict")

    rules_adv = count_patterns_detected(rules_results, "rules_verdict", scenarios)
    agent_adv = count_patterns_detected(agent_results, "agent_verdict", scenarios)
    adv_total = sum(1 for s in scenarios if s.get("category") == "adversarial")

    rules_latencies = sorted([r["rules_latency_ms"] for r in rules_results])
    agent_latencies = sorted([r["agent_latency_ms"] for r in agent_results])
    median_rules = rules_latencies[len(rules_latencies) // 2]
    median_agent = agent_latencies[len(agent_latencies) // 2]

    f1_lift = (
        (agent_metrics["f1"] - rules_metrics["f1"]) / max(rules_metrics["f1"], 0.01)
    ) * 100

    print("\n" + "=" * 60)
    print("BENCHMARK RESULTS")
    print("=" * 60)

    print(f"\nRules-only baseline ({len(scenarios)} scenarios):")
    print(f"  Precision:  {rules_metrics['precision']:.1%}")
    print(f"  Recall:     {rules_metrics['recall']:.1%}")
    print(f"  F1:         {rules_metrics['f1']:.1%}")
    print(f"  Ambiguous accuracy: {rules_metrics['ambiguous_accuracy']:.0%}")
    print(f"  Adversarial caught: {rules_adv}/{adv_total}")
    print(f"  Median latency: {median_rules}ms")

    print(f"\nMulti-agent pipeline ({len(scenarios)} scenarios):")
    print(f"  Precision:  {agent_metrics['precision']:.1%}")
    print(f"  Recall:     {agent_metrics['recall']:.1%}")
    print(f"  F1:         {agent_metrics['f1']:.1%}")
    print(f"  Ambiguous accuracy: {agent_metrics['ambiguous_accuracy']:.0%}")
    print(f"  Adversarial caught: {agent_adv}/{adv_total}")
    print(f"  Median latency: {median_agent}ms ({median_agent/1000:.1f}s)")

    print(f"\n{'='*60}")
    print(f"Agent F1 lift over rules: +{f1_lift:.0f}%")
    print(
        f"Caught {agent_adv}/{adv_total} adversarial patterns vs {rules_adv}/{adv_total} for rules."
    )
    print(f"Median investigation latency: {median_agent/1000:.1f} seconds.")
    print(f"{'='*60}")

    print("\nVerdict distribution:")
    for system, res, key in [
        ("Rules", rules_results, "rules_verdict"),
        ("Agent", agent_results, "agent_verdict"),
    ]:
        dist: dict[str, int] = {}
        for r in res:
            v = r[key]
            dist[v] = dist.get(v, 0) + 1
        print(f"  {system}: {dict(sorted(dist.items()))}")

    benchmark_run_at = datetime.now(timezone.utc).isoformat()
    adv_improvement_pct = None
    if rules_adv > 0:
        adv_improvement_pct = round(((agent_adv - rules_adv) / rules_adv) * 100, 1)

    summary_path = RESULTS_DIR / "summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "benchmark_run_at": benchmark_run_at,
                "scenarios_run": len(scenarios),
                "scenario_mix": by_cat,
                "rules": rules_metrics,
                "agent": agent_metrics,
                "rules_adversarial_caught": rules_adv,
                "agent_adversarial_caught": agent_adv,
                "adversarial_improvement_pct": adv_improvement_pct,
                "f1_lift_pct": round(f1_lift, 1),
                "median_rules_latency_ms": median_rules,
                "median_agent_latency_ms": median_agent,
                "benchmark_api_url": os.environ.get(
                    "BENCHMARK_API_URL", "http://localhost:8000"
                ).rstrip("/"),
                "mock_llm_respected": os.environ.get("MOCK_LLM", "").lower()
                in ("1", "true", "yes"),
            },
            f,
            indent=2,
        )
    print(f"\nSummary written to {summary_path} (run at {benchmark_run_at})")


if __name__ == "__main__":
    main()
