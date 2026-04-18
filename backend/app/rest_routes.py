"""REST API routes: /api/investigate, /api/transactions, /api/health, /api/stream, /api/mcp/*"""

import asyncio
import csv
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db, Transaction, Investigation, AsyncSessionLocal, AuditLog
from app.audit_service import append_audit
from app.agents.orchestrator import run_investigation
from app.mcp_servers.transaction_lookup import transaction_lookup
from app.mcp_servers.customer_profile import customer_profile
from app.mcp_servers.similar_fraud_search import similar_fraud_search
from app.mcp_servers.rules_engine import rules_engine
from app.mcp_servers.evidence_writer import evidence_writer

router = APIRouter()

# In-memory store for streaming events per investigation
investigation_events: dict[str, list[dict]] = {}
investigation_complete: dict[str, bool] = {}
approval_decisions: dict[str, Optional[str]] = {}


class InvestigateRequest(BaseModel):
    transaction_id: str


class ApprovalRequest(BaseModel):
    investigation_id: str
    decision: str  # "approve" or "reject"
    approved_by: str = "demo_user"


class MCPRequest(BaseModel):
    transaction_id: Optional[str] = None
    customer_id: Optional[str] = None
    investigation_id: Optional[str] = None
    transaction_embedding: Optional[list[float]] = None
    k: int = 10
    verdict: Optional[str] = None
    evidence: Optional[dict] = None
    recommendation: Optional[str] = None
    confidence: Optional[float] = None


@router.get("/health")
async def health():
    return {"status": "ok", "service": "payguard-api", "timestamp": datetime.utcnow().isoformat()}


def _benchmark_results_dir() -> Path:
    """Docker mount or repo-relative benchmarks/results (local dev)."""
    docker_path = Path("/app/benchmarks/results")
    if docker_path.is_dir():
        return docker_path
    repo_root = Path(__file__).resolve().parent.parent.parent
    return repo_root / "benchmarks" / "results"


@router.get("/benchmarks/results")
async def get_benchmark_results():
    """Load benchmark CSV + summary JSON from mounted results directory."""
    results_dir = _benchmark_results_dir()
    csv_path = results_dir / "comparison.csv"
    summary_path = results_dir / "summary.json"

    if not csv_path.is_file():
        raise HTTPException(
            status_code=404,
            detail=(
                f"comparison.csv not found in {results_dir} "
                "(run: docker compose run --rm worker python -m benchmarks.run_benchmark)"
            ),
        )

    with open(csv_path, newline="", encoding="utf-8") as f:
        scenarios = list(csv.DictReader(f))

    summary = None
    if summary_path.is_file():
        with open(summary_path, encoding="utf-8") as f:
            summary = json.load(f)

    return {"scenarios": scenarios, "summary": summary}


@router.get("/transactions")
async def list_transactions(
    limit: int = Query(default=50, le=500),
    offset: int = Query(default=0, ge=0),
    is_fraud: Optional[bool] = None,
    type: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    query = select(Transaction).order_by(Transaction.transaction_id)
    count_query = select(func.count()).select_from(Transaction)

    if is_fraud is not None:
        query = query.where(Transaction.is_fraud == is_fraud)
        count_query = count_query.where(Transaction.is_fraud == is_fraud)
    if type:
        query = query.where(Transaction.type == type)
        count_query = count_query.where(Transaction.type == type)

    total_result = await db.execute(count_query)
    total = total_result.scalar()

    result = await db.execute(query.offset(offset).limit(limit))
    transactions = result.scalars().all()

    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "transactions": [
            {
                "transaction_id": t.transaction_id,
                "step": t.step,
                "type": t.type,
                "amount": t.amount,
                "name_orig": t.name_orig,
                "name_dest": t.name_dest,
                "old_balance_org": t.old_balance_org,
                "new_balance_orig": t.new_balance_orig,
                "is_fraud": t.is_fraud,
                "is_flagged_fraud": t.is_flagged_fraud,
                "timestamp": t.timestamp.isoformat() if t.timestamp else None,
                "ip_address": t.ip_address,
                "device_fingerprint": t.device_fingerprint,
                "merchant_category": t.merchant_category,
                "country_code": t.country_code,
            }
            for t in transactions
        ],
    }


@router.get("/transactions/{transaction_id}")
async def get_transaction(transaction_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Transaction).where(Transaction.transaction_id == transaction_id))
    t = result.scalar_one_or_none()
    if not t:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return {
        "transaction_id": t.transaction_id,
        "step": t.step,
        "type": t.type,
        "amount": t.amount,
        "name_orig": t.name_orig,
        "name_dest": t.name_dest,
        "old_balance_org": t.old_balance_org,
        "new_balance_orig": t.new_balance_orig,
        "old_balance_dest": t.old_balance_dest,
        "new_balance_dest": t.new_balance_dest,
        "is_fraud": t.is_fraud,
        "is_flagged_fraud": t.is_flagged_fraud,
        "timestamp": t.timestamp.isoformat() if t.timestamp else None,
        "ip_address": t.ip_address,
        "device_fingerprint": t.device_fingerprint,
        "merchant_category": t.merchant_category,
        "country_code": t.country_code,
    }


@router.post("/investigate")
async def start_investigation(req: InvestigateRequest):
    investigation_id = f"inv_{uuid.uuid4().hex[:12]}"
    investigation_events[investigation_id] = []
    investigation_complete[investigation_id] = False
    approval_decisions[investigation_id] = None

    asyncio.create_task(_run_investigation_task(investigation_id, req.transaction_id))

    return {
        "investigation_id": investigation_id,
        "transaction_id": req.transaction_id,
        "status": "started",
        "stream_url": f"/api/stream/{investigation_id}",
    }


async def _run_investigation_task(investigation_id: str, transaction_id: str):
    """Run the full investigation pipeline and emit events."""
    try:
        await append_audit(
            investigation_id,
            "system",
            "investigation_started",
            new_state={"transaction_id": transaction_id},
        )

        def emit(event: dict):
            investigation_events.setdefault(investigation_id, []).append(event)

        result = await run_investigation(
            transaction_id=transaction_id,
            investigation_id=investigation_id,
            emit_event=emit,
            get_approval=lambda: approval_decisions.get(investigation_id),
        )

        async with AsyncSessionLocal() as db:
            res = await db.execute(select(Investigation).where(Investigation.id == investigation_id))
            inv = res.scalar_one_or_none()
            trace = investigation_events.get(investigation_id, [])
            status = "completed" if not result.get("requires_approval") else "pending_approval"
            if inv:
                inv.transaction_id = transaction_id
                inv.status = status
                inv.verdict = result.get("verdict", inv.verdict or "inconclusive")
                inv.confidence = float(result.get("confidence") or inv.confidence or 0.0)
                inv.recommendation = result.get("recommendation", inv.recommendation or "monitor")
                inv.evidence = result.get("evidence") or inv.evidence
                inv.triage_result = result.get("triage") or inv.triage_result
                inv.behavior_result = result.get("behavior") or inv.behavior_result
                inv.synthesis_result = result.get("synthesis") or inv.synthesis_result
                inv.agent_trace = trace
                inv.cost_usd = float(result.get("cost_usd") or inv.cost_usd or 0.0)
                inv.model_breakdown = result.get("model_breakdown") or inv.model_breakdown
                inv.token_usage = result.get("token_usage") or inv.token_usage
                inv.updated_at = datetime.utcnow()
            else:
                inv = Investigation(
                    id=investigation_id,
                    transaction_id=transaction_id,
                    status=status,
                    verdict=result.get("verdict", "inconclusive"),
                    confidence=float(result.get("confidence") or 0.0),
                    recommendation=result.get("recommendation", "monitor"),
                    evidence=result.get("evidence"),
                    triage_result=result.get("triage"),
                    behavior_result=result.get("behavior"),
                    synthesis_result=result.get("synthesis"),
                    agent_trace=trace,
                    cost_usd=float(result.get("cost_usd") or 0.0),
                    model_breakdown=result.get("model_breakdown"),
                    token_usage=result.get("token_usage"),
                    created_at=datetime.utcnow(),
                )
                db.add(inv)
            await db.commit()

    except Exception as e:
        investigation_events.setdefault(investigation_id, []).append(
            {"agent": "system", "type": "error", "content": str(e)}
        )
    finally:
        investigation_complete[investigation_id] = True


@router.get("/stream/{investigation_id}")
async def stream_investigation(investigation_id: str):
    """SSE stream for investigation events."""
    async def event_generator():
        sent = 0
        while True:
            events = investigation_events.get(investigation_id, [])
            while sent < len(events):
                event = events[sent]
                yield {"event": event.get("type", "message"), "data": json.dumps(event)}
                sent += 1

            if investigation_complete.get(investigation_id, False) and sent >= len(events):
                yield {"event": "done", "data": json.dumps({"status": "complete"})}
                break

            await asyncio.sleep(0.1)

    return EventSourceResponse(event_generator())


@router.post("/approve")
async def approve_investigation(req: ApprovalRequest):
    approval_decisions[req.investigation_id] = req.decision

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Investigation).where(Investigation.id == req.investigation_id)
        )
        inv = result.scalar_one_or_none()
        if inv:
            inv.approval_decision = req.decision
            inv.approved_by = req.approved_by
            inv.status = "approved" if req.decision == "approve" else "rejected"
            inv.updated_at = datetime.utcnow()
            await db.commit()

    await append_audit(
        req.investigation_id,
        req.approved_by or "analyst",
        "approval_decision",
        new_state={"decision": req.decision},
        reason="human_review",
    )

    return {"investigation_id": req.investigation_id, "decision": req.decision, "status": "recorded"}


@router.get("/investigations")
async def list_investigations(
    limit: int = Query(default=20, le=100),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Investigation).order_by(Investigation.created_at.desc()).limit(limit)
    )
    investigations = result.scalars().all()
    return {
        "investigations": [
            {
                "id": inv.id,
                "transaction_id": inv.transaction_id,
                "status": inv.status,
                "verdict": inv.verdict,
                "confidence": inv.confidence,
                "recommendation": inv.recommendation,
                "created_at": inv.created_at.isoformat() if inv.created_at else None,
                "cost_usd": inv.cost_usd,
            }
            for inv in investigations
        ]
    }


@router.get("/investigations/{investigation_id}")
async def get_investigation(investigation_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Investigation).where(Investigation.id == investigation_id))
    inv = result.scalar_one_or_none()
    if not inv:
        raise HTTPException(status_code=404, detail="Investigation not found")
    return {
        "id": inv.id,
        "transaction_id": inv.transaction_id,
        "status": inv.status,
        "verdict": inv.verdict,
        "confidence": inv.confidence,
        "recommendation": inv.recommendation,
        "evidence": inv.evidence,
        "triage_result": inv.triage_result,
        "behavior_result": inv.behavior_result,
        "synthesis_result": inv.synthesis_result,
        "agent_trace": inv.agent_trace,
        "created_at": inv.created_at.isoformat() if inv.created_at else None,
        "approval_decision": inv.approval_decision,
        "cost_usd": inv.cost_usd,
        "model_breakdown": inv.model_breakdown,
        "token_usage": inv.token_usage,
    }


@router.get("/investigations/{investigation_id}/audit")
async def get_investigation_audit(investigation_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AuditLog)
        .where(AuditLog.investigation_id == investigation_id)
        .order_by(AuditLog.timestamp.asc())
    )
    rows = result.scalars().all()
    return {
        "investigation_id": investigation_id,
        "entries": [
            {
                "id": r.id,
                "timestamp": r.timestamp.isoformat() if r.timestamp else None,
                "actor": r.actor,
                "action": r.action,
                "prior_state": r.prior_state,
                "new_state": r.new_state,
                "reason": r.reason,
            }
            for r in rows
        ],
    }


# MCP Tool Server REST endpoints

@router.post("/mcp/transaction_lookup")
async def mcp_transaction_lookup(req: MCPRequest):
    if not req.transaction_id:
        raise HTTPException(status_code=400, detail="transaction_id required")
    return await transaction_lookup(req.transaction_id)


@router.post("/mcp/customer_profile")
async def mcp_customer_profile(req: MCPRequest):
    if not req.customer_id:
        raise HTTPException(status_code=400, detail="customer_id required")
    return await customer_profile(req.customer_id)


@router.post("/mcp/similar_fraud_search")
async def mcp_similar_fraud(req: MCPRequest):
    return await similar_fraud_search(
        transaction_id=req.transaction_id,
        embedding=req.transaction_embedding,
        k=req.k,
    )


@router.post("/mcp/rules_engine")
async def mcp_rules_engine(req: MCPRequest):
    if not req.transaction_id:
        raise HTTPException(status_code=400, detail="transaction_id required")
    return await rules_engine(req.transaction_id)


@router.post("/mcp/evidence_writer")
async def mcp_evidence_writer(req: MCPRequest):
    if not req.investigation_id:
        raise HTTPException(status_code=400, detail="investigation_id required")
    return await evidence_writer(
        investigation_id=req.investigation_id,
        verdict=req.verdict or "inconclusive",
        evidence=req.evidence or {},
        recommendation=req.recommendation or "monitor",
        confidence=req.confidence,
    )
