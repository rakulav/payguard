# PayGuard: LLM-Powered Payment Fraud Investigation Assistant

![Python](https://img.shields.io/badge/Python-3.11-blue) ![FastAPI](https://img.shields.io/badge/FastAPI-0.115-green) ![Next.js](https://img.shields.io/badge/Next.js-14-black) ![Docker](https://img.shields.io/badge/Docker-Compose-blue) ![Claude](https://img.shields.io/badge/Claude-Sonnet_4.5-orange)

Multi-agent fraud investigation copilot using Claude Sonnet 4.5, orchestrating 3 specialist agents (transaction triage, behavioral reasoning, evidence synthesis) through 5 MCP tool servers exposed over REST and GraphQL. Hybrid retrieval over pgvector, Qdrant, and OpenSearch enables semantic search across 250K+ synthetic payment records.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Next.js / Streamlit UI                     │
│         Dashboard │ Investigation Console │ Benchmarks        │
└──────────────────────────┬──────────────────────────────────┘
                           │ SSE / REST / GraphQL
┌──────────────────────────▼──────────────────────────────────┐
│                     FastAPI Backend                           │
│  ┌─────────┐  ┌──────────┐  ┌───────────┐                   │
│  │ Triage  │→│ Behavior │→│ Synthesis │  LangGraph          │
│  │ Agent   │  │ Agent    │  │ Agent     │  Orchestrator      │
│  └────┬────┘  └────┬─────┘  └─────┬─────┘                   │
│       │            │              │                           │
│  ┌────▼────────────▼──────────────▼─────┐                    │
│  │         5 MCP Tool Servers            │                    │
│  │ tx_lookup │ customer │ similar_fraud  │                    │
│  │ rules_eng │ evidence_writer           │                    │
│  └────┬────────────┬──────────────┬──────┘                   │
└───────┼────────────┼──────────────┼──────────────────────────┘
        │            │              │
   ┌────▼────┐  ┌────▼────┐  ┌─────▼─────┐
   │Postgres │  │ Qdrant  │  │OpenSearch  │
   │+pgvector│  │         │  │           │
   └─────────┘  └─────────┘  └───────────┘
```

## Quick Start

```bash
# One command to start everything
./run.sh

# Or with make
make demo
```

**Prerequisites:** Docker Desktop (running), optionally `export ANTHROPIC_API_KEY=sk-ant-...`

Without an API key, the system runs with `MOCK_LLM=1` using realistic canned agent traces.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| LLM | Claude Sonnet 4.5 (Anthropic API) |
| Agent Orchestration | LangGraph (3-agent state machine) |
| API | FastAPI + Strawberry GraphQL + SSE |
| Vector Search | pgvector (Postgres) + Qdrant |
| Text Search | OpenSearch 2.11 |
| Retrieval Fusion | Reciprocal Rank Fusion (RRF, k=60) |
| Embeddings | all-MiniLM-L6-v2 (384-dim, CPU) |
| Frontend | Next.js 14 App Router + Tailwind + shadcn/ui |
| Backup UI | Streamlit |
| Data | 250K synthetic PaySim transactions |
| Infrastructure | Docker Compose (7 services) |

## Benchmark Results

After running `make demo`, results are in `benchmarks/results/comparison.csv`:

- Agent surfaces **30%+ more adversarial patterns** than rules-only baseline
- Median investigation latency: **under 10 seconds**

Re-run benchmarks: `make bench`

## Services

| Service | URL |
|---------|-----|
| Next.js UI | http://localhost:3000 |
| Streamlit UI | http://localhost:8501 |
| FastAPI | http://localhost:8000 |
| API Docs | http://localhost:8000/docs |
| GraphQL | http://localhost:8000/graphql |

## Commands

```bash
make demo    # Full setup + start + benchmark
make seed    # Re-seed data only
make bench   # Re-run benchmarks
make test    # Run tests
make down    # Stop services
make clean   # Stop + remove volumes + data
```

## Demo Script

See [DEMO_SCRIPT.md](DEMO_SCRIPT.md) for a 5-minute interview walkthrough.

## Decisions

See [DECISIONS.md](DECISIONS.md) for all architectural decisions.
