# PayGuard final-fix session decisions

## Checkpoint

- Repository had no `.git`; initialized git and committed after implementing changes.
- Added `.gitignore` (includes `.env`) and removed `.env` from the commit so secrets are not tracked.

## Synthesis (Phase 1)

- Replaced prior purely-programmatic `_calibrated_synthesis_decision` with **LLM-first** synthesis using `synthesis.txt` (fraud / suspicious / legitimate, 2-of-n fraud bar, adversarial/crypto caveats).
- Added **JSON extraction** from model text plus **heuristic fallback** when `MOCK_LLM=1` or JSON parse fails (middle-ground scoring on rules, anomaly, fraud_match_ratio, flags, risky merchants).
- Added **guardrails** so an LLM “legitimate” is not accepted when the heuristic strongly disagrees (fraud / high-risk suspicious).
- Mapped recommendations to existing pipeline: `review`→`monitor`, `close`→`clear`, `escalate`/`freeze` preserved; `requires_approval` for freeze/escalate.

## Triage

- Extended `triage.txt` with terminal high-confidence (≥0.92) guidance.
- Wired **Haiku** via `agent_role="triage"` in `call_llm`.
- Passed `investigation_id` into `run_triage` for audit; attached `llm_usage` (tokens + model + cost).

## Behavior

- **Sonnet** via `agent_role="behavior"`; `llm_usage` + audit `agent_verdict`.

## Orchestrator

- Passes `investigation_id` into triage/behavior.
- Merges `llm_usage` from all agents into `model_breakdown`, `token_usage`, `cost_usd` on the final return dict.
- Exposes `triage`, `behavior`, `synthesis` on the top-level return for REST persistence.

## Cost tracking (`cost_tracker.py`)

- Haiku 4.5: $0.80/M input, $4/M output; Sonnet 4.5: $3/M input, $15/M output (per user spec).

## Audit (`audit_service.py`, `audit_log` table)

- Postgres table `audit_log` + indexes; `ALTER investigations` for `cost_usd`, `model_breakdown`, `token_usage`.
- Events: `investigation_started` (system), triage/behavior `agent_verdict`, synthesis `agent_verdict`, `approval_requested`, `evidence_persisted` (evidence_writer), `approval_decision` (approve endpoint).
- `GET /api/investigations/{id}/audit` returns chronological entries.

## REST / persistence

- Investigation row updated with cost + token breakdown after pipeline.
- `GET /api/investigations/{id}` includes `cost_usd`, `model_breakdown`, `token_usage`.
- MCP `evidence_writer` accepts optional `confidence`.

## UI

- Next.js investigate page: cost banner, **Live trace / Audit trail** toggle, audit list from API.
- Streamlit: cost caption, model breakdown caption, audit section.

## Benchmark

- `map_investigation_verdict` explicitly maps `suspicious` and `inconclusive` → benchmark `suspicious`.

## Verification note

- Full 50-run benchmark and live Anthropic calls were **not** executed in this environment (no guaranteed Docker/API keys here). Re-run locally: `docker compose build api && docker compose up -d api` then `docker compose run --rm worker python -m benchmarks.run_benchmark`.
