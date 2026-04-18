.PHONY: demo seed bench test down clean lint format

demo:
	@echo "╔══════════════════════════════════════════════╗"
	@echo "║  PayGuard: LLM-Powered Fraud Investigation  ║"
	@echo "╚══════════════════════════════════════════════╝"
	@echo ""
	@# Step 1: Create .env if missing
	@if [ ! -f .env ]; then cp .env.example .env; echo "✓ Created .env from .env.example"; fi
	@echo "→ Step 1/10: Environment ready"
	@# Step 2: Build containers
	docker compose build
	@echo "→ Step 2/10: Containers built"
	@# Step 3: Start infrastructure
	docker compose up -d postgres qdrant opensearch
	@echo "→ Step 3/10: Infrastructure starting..."
	@# Step 4: Wait for healthy
	@echo "→ Step 4/10: Waiting for infrastructure health..."
	@timeout=120; elapsed=0; \
	while ! docker compose exec -T postgres pg_isready -U payguard > /dev/null 2>&1; do \
		sleep 2; elapsed=$$((elapsed+2)); \
		if [ $$elapsed -ge $$timeout ]; then echo "✗ Postgres timeout"; exit 1; fi; \
	done; echo "  ✓ Postgres healthy"
	@timeout=120; elapsed=0; \
	while ! curl -sf http://localhost:6333/healthz > /dev/null 2>&1; do \
		sleep 2; elapsed=$$((elapsed+2)); \
		if [ $$elapsed -ge $$timeout ]; then echo "✗ Qdrant timeout"; exit 1; fi; \
	done; echo "  ✓ Qdrant healthy"
	@timeout=120; elapsed=0; \
	while ! curl -sf http://localhost:9200/_cluster/health > /dev/null 2>&1; do \
		sleep 2; elapsed=$$((elapsed+2)); \
		if [ $$elapsed -ge $$timeout ]; then echo "✗ OpenSearch timeout"; exit 1; fi; \
	done; echo "  ✓ OpenSearch healthy"
	@# Step 5: Seed data
	@echo "→ Step 5/10: Seeding database + embeddings..."
	docker compose run --rm worker python -m app.seed
	@echo "  ✓ Data seeded"
	@# Step 6: Start API + UIs
	@echo "→ Step 6/10: Starting API and UI services..."
	docker compose up -d api ui streamlit
	@# Step 7: Wait for API health
	@echo "→ Step 7/10: Waiting for API health..."
	@timeout=120; elapsed=0; \
	while ! curl -sf http://localhost:8000/api/health > /dev/null 2>&1; do \
		sleep 2; elapsed=$$((elapsed+2)); \
		if [ $$elapsed -ge $$timeout ]; then echo "✗ API timeout"; exit 1; fi; \
	done; echo "  ✓ API healthy"
	@# Step 8: Run benchmarks
	@echo "→ Step 8/10: Running benchmarks..."
	docker compose run --rm worker python -m benchmarks.run_benchmark
	@echo "  ✓ Benchmarks complete"
	@# Step 9: Run smoke test
	@echo "→ Step 9/10: Running smoke test..."
	docker compose run --rm worker python -m pytest tests/test_smoke.py -v --tb=short || echo "  ⚠ Smoke test had issues (non-blocking for demo)"
	@# Step 10: Print banner
	@echo ""
	@echo "╔══════════════════════════════════════════════════════════════╗"
	@echo "║                    PayGuard is READY!                       ║"
	@echo "╠══════════════════════════════════════════════════════════════╣"
	@echo "║  Next.js UI:    http://localhost:3000                       ║"
	@echo "║  Streamlit UI:  http://localhost:8501                       ║"
	@echo "║  API:           http://localhost:8000                       ║"
	@echo "║  API Docs:      http://localhost:8000/docs                  ║"
	@echo "║  GraphQL:       http://localhost:8000/graphql               ║"
	@echo "║  Postgres:      localhost:5432                              ║"
	@echo "║  Qdrant:        http://localhost:6333/dashboard             ║"
	@echo "║  OpenSearch:    http://localhost:9200                       ║"
	@echo "╠══════════════════════════════════════════════════════════════╣"
	@echo "║  Demo script:   cat DEMO_SCRIPT.md                         ║"
	@echo "║  Benchmarks:    cat benchmarks/results/comparison.csv       ║"
	@echo "╚══════════════════════════════════════════════════════════════╝"
	@echo ""
	@echo "Example curl commands for the interviewer:"
	@echo ""
	@echo "  1. Health check:"
	@echo "     curl http://localhost:8000/api/health"
	@echo ""
	@echo "  2. List transactions:"
	@echo "     curl http://localhost:8000/api/transactions?limit=5"
	@echo ""
	@echo "  3. Start investigation:"
	@echo "     curl -X POST http://localhost:8000/api/investigate -H 'Content-Type: application/json' -d '{\"transaction_id\": \"TXN_48213\"}'"
	@echo ""
	@echo "  4. GraphQL query:"
	@echo "     curl -X POST http://localhost:8000/graphql -H 'Content-Type: application/json' -d '{\"query\": \"{ transactions(limit: 3) { transactionId amount type isFraud } }\"}'"
	@echo ""
	@echo "  5. MCP tool (rules engine):"
	@echo "     curl -X POST http://localhost:8000/api/mcp/rules_engine -H 'Content-Type: application/json' -d '{\"transaction_id\": \"TXN_48213\"}'"
	@echo ""
	@# Try to open browser
	@(open http://localhost:3000 2>/dev/null || xdg-open http://localhost:3000 2>/dev/null || true)

seed:
	docker compose run --rm worker python -m app.seed

bench:
	docker compose run --rm worker python -m benchmarks.run_benchmark

test:
	docker compose run --rm worker python -m pytest tests/ -v

lint:
	cd backend && ruff check . && black --check .
	cd benchmarks && ruff check . && black --check .

format:
	cd backend && black . && ruff check --fix .
	cd benchmarks && black . && ruff check --fix .
	cd frontend && npx --yes prettier --write "**/*.{ts,tsx,js,jsx,json,css}" --log-level warn

down:
	docker compose down

clean:
	docker compose down -v --rmi local
	rm -f data/transactions.parquet
	rm -f benchmarks/results/*.csv
