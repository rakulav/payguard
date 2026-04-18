# PayGuard Demo Script (5 minutes)

## Setup (before the interview)

```bash
./run.sh   # Takes ~3 min first time, ~30s after
```

Verify: http://localhost:3000 loads, http://localhost:8000/api/health returns `{"status": "ok"}`.

---

## Walkthrough (5 minutes)

### Step 1: Dashboard Overview (60 seconds)

**Open:** http://localhost:3000

**Say:** "PayGuard is a multi-agent fraud investigation assistant. I built it to show how LLM agents can replace manual fraud analyst workflows. The dashboard shows 250,000 synthetic payment transactions from the PaySim dataset."

**Do:** Click the "Flagged Only" toggle to filter to fraudulent transactions.

**Say:** "About 1.5% are labeled fraud. The interesting part is what happens when we investigate one."

### Step 2: Start Investigation (90 seconds)

**Do:** Click "Investigate" on transaction TXN_48213.

**Say:** "This kicks off a LangGraph state machine with three specialist agents, all powered by Claude Sonnet 4.5."

**Watch the Agent Timeline populate:**

1. **Triage Agent** fires first — calls `transaction_lookup` and `rules_engine` MCP tools.
   - **Say:** "The triage agent pulls the transaction details and runs it through 5 hardcoded fraud rules. It found a balance-drain pattern — the account went from $12,000 to zero in two steps."

2. **Behavior Agent** fires next (confidence < 0.9) — calls `customer_profile` and `similar_fraud_search`.
   - **Say:** "Because triage confidence was below 90%, the behavior agent kicks in. It searches across three retrieval backends — pgvector, Qdrant, and OpenSearch — using Reciprocal Rank Fusion to find similar past frauds. The right panel shows the top matches."

3. **Synthesis Agent** runs last — produces the investigation report.
   - **Say:** "The synthesis agent combines everything into a structured report with a verdict and recommendation."

### Step 3: Approval Gate (30 seconds)

**Do:** When the approval modal appears (recommending "freeze account"), click **Approve**.

**Say:** "This is the human-in-the-loop gate. High-severity recommendations like account freezes require analyst approval before the system writes the evidence record. The agent actually pauses until you click."

### Step 4: Benchmark Results (60 seconds)

**Do:** Navigate to http://localhost:3000/benchmarks (or click "Benchmarks" tab).

**Say:** "I ran 20 labeled fraud scenarios through both a rules-only baseline and the full agent pipeline."

**Point to the chart:**
- "The agent surfaced **30%+ more adversarial patterns** than rules alone — it catches things like new-device + new-country combos that static rules miss."
- "Median investigation latency is **under 10 seconds**, compared to what would be minutes of manual analyst work."

**Do:** Show the CSV table below the chart.

### Step 5: Architecture Highlight (60 seconds)

**Say:** "Under the hood:"
- "Three specialist agents orchestrated by LangGraph, each with access to 5 MCP tool servers exposed over both REST and GraphQL."
- "Hybrid retrieval: pgvector for SQL-integrated vector search, Qdrant for filtered payload queries, OpenSearch for BM25 keyword search and aggregations. Results are fused with Reciprocal Rank Fusion."
- "Streaming over SSE so you see the agent's reasoning in real-time."
- "Everything runs in Docker — 7 containers, single `make demo` command."

---

## Likely Interviewer Questions + Prepared Answers

### Q1: "Why 3 agents instead of 1?"

**A:** "Separation of concerns. The triage agent is optimized for speed — it runs cheap rule checks first. The behavior agent only fires when triage is uncertain, saving API costs. The synthesis agent has a different system prompt focused on report writing. In production, you could scale or swap each independently. It also makes the reasoning trace more readable for auditors."

### Q2: "Why pgvector AND Qdrant?"

**A:** "Different strengths. pgvector lives inside Postgres, so I get transactional consistency and can join vectors with relational data in one query. Qdrant gives me payload-filtered search — 'find similar frauds but only CASH_OUT type' — which pgvector doesn't support natively. In production you'd pick one; here I demonstrate I can integrate both."

### Q3: "How does Reciprocal Rank Fusion work?"

**A:** "RRF scores each result as 1/(k + rank) where k=60. If a document appears in all three retrieval backends, its scores are summed. This gives a balanced ranking without needing to normalize score distributions across different backends. It's simple, effective, and avoids the problem of cosine similarity scores being incomparable with BM25 scores."

### Q4: "What does the approval gate prevent?"

**A:** "It prevents autonomous high-stakes actions. The agent can analyze and recommend, but destructive operations like freezing accounts or escalating to law enforcement require human confirmation. This is a fundamental pattern in agentic systems — you want the AI to do the investigation work but a human to approve consequential actions."

### Q5: "How would you scale this to production?"

**A:** "Three main changes: (1) Replace the single Postgres instance with a managed database (RDS + pgvector extension), (2) Put the LangGraph orchestrator behind a task queue (Celery or SQS) so investigations don't block the API, (3) Add authentication, rate limiting, and audit logging. The agent architecture itself scales horizontally since each investigation is independent."

### Q6: "Why OpenSearch instead of Elasticsearch?"

**A:** "OpenSearch is the Apache-2.0 fork — no licensing concerns. For this use case they're functionally identical. I use it for BM25 keyword search over structured fields and aggregations for the customer profile tool."

### Q7: "What happens if the Claude API is down?"

**A:** "The system has a MOCK_LLM=1 mode that returns pre-computed but realistic agent traces. The demo never breaks on stage. In production, you'd want a queue + retry system and possibly a fallback to a self-hosted model."

### Q8: "How did you generate the 250K records?"

**A:** "I use the PaySim synthetic financial fraud dataset from Hugging Face. If that's unavailable, the system falls back to generating data with Faker and numpy using the same schema. Fraud patterns are injected at ~1.5% rate: rapid micro-transactions, round-amount CASH_OUT after TRANSFER, new-device + new-country combos, balance-drain patterns, and merchant-category drift."
