"""Smoke test: boots FastAPI app, runs investigation, validates response."""

import os
import pytest
from httpx import AsyncClient, ASGITransport

os.environ["MOCK_LLM"] = "1"
os.environ["DATABASE_URL"] = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://payguard:payguard_secret@postgres:5432/payguard",
)
os.environ["DATABASE_URL_SYNC"] = os.environ.get(
    "DATABASE_URL_SYNC", "postgresql://payguard:payguard_secret@postgres:5432/payguard"
)

from app.main import app


@pytest.mark.asyncio
async def test_health():
    """Health endpoint returns ok."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


@pytest.mark.asyncio
async def test_investigate_returns_investigation_id():
    """POST /api/investigate returns an investigation_id and stream_url."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/investigate",
            json={"transaction_id": "TXN_48213"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "investigation_id" in data
        assert data["status"] == "started"
        assert "stream_url" in data


@pytest.mark.asyncio
async def test_transactions_endpoint():
    """GET /api/transactions returns paginated results."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/transactions?limit=5")
        assert resp.status_code == 200
        data = resp.json()
        assert "transactions" in data
        assert "total" in data


@pytest.mark.asyncio
async def test_graphql_endpoint():
    """GraphQL query works."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/graphql",
            json={"query": "{ transactions(limit: 2) { transactionId } }"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "data" in data
