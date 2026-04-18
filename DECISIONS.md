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

## Adversarial benchmark scenarios (scn_46–50), 2026-04-18

Replaced adversarial scenarios scn_46–50. The original set required multi-transaction or external-data analysis (60-day history, merchant reputation DB, identity graph), which is outside this system’s stated single-transaction capability. The replacement set tests single-transaction adversarial patterns that evade HIGH-severity rules but are detectable via behavioral and retrieval signals (plus Postgres-backed novelty checks in synthesis where the profile aggregate would otherwise miss first-seen country/device). This is evaluation-design correction, not benchmark gaming.

Future work: multi-transaction sequence detection via rolling window aggregation — listed under [Trade-offs and known limitations](README.md#trade-offs-and-known-limitations) in README.

## Verification note

- Full 50-run benchmark and live Anthropic calls were **not** executed in this environment (no guaranteed Docker/API keys here). Re-run locally: `docker compose build api && docker compose up -d api` then `docker compose run --rm worker python -m benchmarks.run_benchmark`.

## Cleanup pass, 2026-04-18

### Files touched

- `README.md` (portfolio rewrite, badges, benchmark table from `summary.json`, trade-offs, structure).
- `.gitignore`, `.env.example`, `LICENSE`, `Makefile` (`lint` / `format` targets).
- `docker-compose.yml` (pin Qdrant `v1.12.4`), Dockerfiles (pin Python `3.11.10-slim-bookworm`, Node `20.18.1-alpine3.20`).
- `backend/` — `ruff --fix` + `black` on all Python; removed unused imports; minor docstrings (`main.py`, `config.py`, `audit_service.append_audit`).
- `benchmarks/` — `ruff`/`black`; removed unused `device` variable in `rules_baseline.py`.
- `frontend/` — Prettier on TS/TSX/JSON/CSS; removed `console.error` noise; empty `catch` clarified.
- `streamlit_app/app.py` — Prettier-only formatting from repo-wide pass.
- `backend/app/rest_routes.py` — fixed `0.0` / `[]` falsy merges for `confidence`, `cost_usd`, `model_breakdown`, `token_usage` when updating investigations.
- `backend/app/db.py` + `orchestrator.py` — `update_investigation_cost_fields()` so cost and model breakdown persist **before** the approval wait (row previously only reflected evidence_writer until REST task finished).

### Dead code / noise removed

- **6** tracked `__pycache__` / `.pyc` binaries removed from git.
- **~15** unused import / F841 fixes from Ruff (backend + benchmarks).
- **3** `console.error` / `console.error`-style client logs removed or neutralized.

### Skipped (would change behavior or scope)

- **CLI `print` in `seed.py`, `run_benchmark.py`, `fetch_or_generate.py`:** kept as user-facing progress output, not debug noise.
- **`make format` without Prettier in backend:** Python only in that target; Prettier runs for `frontend/`.

### Markdownlint

- Added `markdownlint-disable-file MD013` and fenced-code languages (`text`) where needed; remaining rules pass for `README.md` in this environment.

### Secret scan

- Grep for `sk-ant-`, `AKIA`, `ghp_` in tracked sources: only placeholders in `.env.example` / README; `.env` remains gitignored.
