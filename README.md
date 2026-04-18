# PayGuard

<!-- markdownlint-disable-file MD013 -->

LLM-powered payment fraud investigation assistant with multi-agent reasoning, hybrid retrieval, and human-in-the-loop approval gates.

![Python 3.11](https://img.shields.io/badge/Python-3.11-3776AB)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED)
![License](https://img.shields.io/badge/License-MIT-green)

## What it does

Manual fraud investigation is slow: analysts chase context across rules engines, customer profiles, and case history, and static rules miss novel or adversarial patterns. PayGuard automates the first pass: a LangGraph pipeline triages each transaction, optionally runs behavioral retrieval over hybrid vector + lexical stores, then synthesizes a calibrated verdict with confidence, cost, and an audit trail. High-impact recommendations (freeze / escalate) stop at an approval gate so a human explicitly accepts risk.

## Why this approach

**Multi-agent** keeps responsibilities narrow: triage stays close to rules and lookup, behavior owns retrieval and anomaly language, synthesis merges evidence. That separation makes prompts easier to tune and failures easier to localize than a single monolithic agent.

**Hybrid retrieval** combines PostgreSQL + pgvector for transactional consistency with Qdrant for filtered ANN and OpenSearch for BM25-style text; results are fused (RRF) so no single index has to be perfect.

**Human-in-the-loop** reflects asymmetric cost: missing fraud is expensive, while reviewing a false positive is comparatively cheap. The product augments analysts; it does not replace policy engines or real-time blocking.

**Out of scope by design:** real-time auto-decline, PCI-grade production hardening, and replacing the rules engine. The dataset is synthetic (PaySim-inspired); labels support benchmarking, not regulatory filing.

## Architecture

```text
                    +------------------+
                    |  Next.js /       |
                    |  Streamlit UI    |
                    +--------+---------+
                             | REST / SSE / GraphQL
                             v
+---------------------------+----------------------------+
|                    FastAPI API                         |
|  +--------+    conditional     +----------+            |
|  | Triage |--(may skip)------->| Behavior |            |
|  +---+----+                    +-----+----+            |
|      |                              |                 |
|      +------------+----------------+                 |
|                     v                                 |
|              +------+-------+                         |
|              |  Synthesis   |                         |
|              +------+-------+                         |
|                     |                                 |
|            approval gate (freeze/escalate)            |
|                     v                                 |
|         evidence + audit log (Postgres)               |
+--------------------------------------------------------+
          |              |              |
          v              v              v
    PostgreSQL       Qdrant      OpenSearch
    + pgvector
```

- **Triage:** transaction lookup + rules engine; fast path can skip behavior when the case is clearly low risk.
- **Behavior:** customer profile + similar-fraud search across fused retrieval.
- **Synthesis:** LLM JSON verdict with heuristic fallback under mocks; guardrails against obvious misclassification; writes evidence.
- **Approval gate:** benchmark and UI can POST `/api/approve` to unblock freeze/escalate paths.
- **Audit + cost:** each investigation records model usage, estimated USD cost, and an append-only audit trail (`GET /api/investigations/{id}/audit`).

## Tech stack

| Layer     | Technology                                                          |
| --------- | ------------------------------------------------------------------- |
| Backend   | FastAPI, LangGraph, Strawberry GraphQL, Uvicorn, SQLAlchemy async   |
| LLMs      | Claude Sonnet 4.5 (behavior + synthesis), Claude Haiku 4.5 (triage) |
| Storage   | PostgreSQL + pgvector, Qdrant, OpenSearch                           |
| Frontend  | Next.js 14 App Router, Tailwind CSS                                 |
| Backup UI | Streamlit                                                           |
| Infra     | Docker Compose                                                      |

## Quick start

Prerequisites: Docker Desktop, an Anthropic API key.

```bash
cp .env.example .env   # set ANTHROPIC_API_KEY and MOCK_LLM=0 for live models
make demo              # builds images, seeds data, runs benchmark, starts UI
```

`make demo` waits for Postgres, Qdrant, and OpenSearch, seeds ~250K synthetic rows, starts the API and frontends, and prints service URLs. Without a valid key, set `MOCK_LLM=1` for deterministic canned traces.

## Benchmark results

Values below are from the last committed run in `benchmarks/results/summary.json` (2026-04-18, 50 scenarios, live API, `MOCK_LLM=0`).

| Metric                    | Rules-only baseline | Multi-agent pipeline |
| ------------------------- | ------------------- | -------------------- |
| F1                        | 0.663               | 0.518                |
| Precision                 | 0.952               | 0.833                |
| Recall                    | 0.509               | 0.376                |
| Adversarial caught (of 5) | 0                   | 0                    |
| Ambiguous accuracy        | 30%                 | 40%                  |
| Median latency            | 1 ms                | 36.1 s               |

The agent traded recall for precision on this synthetic mix: fewer blanket fraud labels, more legitimate and suspicious outcomes, at the cost of missing some ground-truth frauds the rules also miss (adversarial bucket). Re-run after prompt or heuristic changes; do not treat these numbers as production KPIs.

## Reproduce the benchmark

```bash
make bench
# or: docker compose run --rm worker python -m benchmarks.run_benchmark
```

Expect roughly 15–30 minutes for 50 live investigations and noticeable Anthropic spend (order of ~$1 depending on model traffic and retries).

## Project structure

```text
payguard/
  backend/app/          # FastAPI app, agents, MCP tools, retrieval, db models
  benchmarks/           # Rules baseline + HTTP benchmark driver
  data/                 # fraud_scenarios.json, optional parquet cache (gitignored when generated)
  frontend/             # Next.js UI
  streamlit_app/        # Streamlit fallback UI
  docker-compose.yml    # local stack
```

## Agents and tools

| Agent     | Model      | Role                                                        |
| --------- | ---------- | ----------------------------------------------------------- |
| Triage    | Haiku 4.5  | Rules + transaction context, routes uncertainty to behavior |
| Behavior  | Sonnet 4.5 | Profile + similar-fraud retrieval                           |
| Synthesis | Sonnet 4.5 | Final verdict JSON + narrative, evidence writer             |

| MCP tool             | Purpose                                |
| -------------------- | -------------------------------------- |
| transaction_lookup   | Load txn row from Postgres             |
| rules_engine         | Deterministic fraud heuristics         |
| customer_profile     | Historical stats for origin customer   |
| similar_fraud_search | Hybrid retrieval + RRF                 |
| evidence_writer      | Persist investigation row + confidence |

## Development

```bash
make test    # dockerized pytest
make lint    # ruff + black --check (backend + benchmarks)
make format  # black + ruff --fix + prettier (frontend)
```

Local Python (without Docker): `cd backend && pip install ruff black pytest && ruff check . && black . && python -m pytest tests/ -v`

## Trade-offs and known limitations

- Synthetic PaySim-style data; no production fraud distribution.
- Demo throughput: sequential investigations, no horizontal worker pool.
- Approval flow is a single shared gate without RBAC or SSO.
- No Anthropic prompt caching yet (would materially cut input cost).
- Rules baseline is intentionally simple so the agent comparison is meaningful on novel patterns.

## What I would build next

Prompt caching on Anthropic, OpenTelemetry traces, shadow-mode evaluation against frozen scenario sets, a lightweight feature store feeding rules, RBAC on approvals, CI (lint + smoke + benchmark on a small shard), and optional public dataset adapters (e.g., IEEE-CIS) behind the same tool contracts.

## License

MIT — see [LICENSE](LICENSE).
