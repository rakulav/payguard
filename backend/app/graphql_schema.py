"""Strawberry GraphQL schema for investigations, transactions, agent events, and MCP tool mutations."""

from typing import Optional
import strawberry
from strawberry.types import Info
from sqlalchemy import select, func
from datetime import datetime

from app.db import AsyncSessionLocal, Transaction, Investigation
from app.mcp_servers.transaction_lookup import transaction_lookup
from app.mcp_servers.customer_profile import customer_profile
from app.mcp_servers.similar_fraud_search import similar_fraud_search
from app.mcp_servers.rules_engine import rules_engine
from app.mcp_servers.evidence_writer import evidence_writer


@strawberry.type
class TransactionType:
    transaction_id: str
    step: Optional[int] = None
    type: Optional[str] = None
    amount: Optional[float] = None
    name_orig: Optional[str] = None
    name_dest: Optional[str] = None
    old_balance_org: Optional[float] = None
    new_balance_orig: Optional[float] = None
    is_fraud: Optional[bool] = None
    is_flagged_fraud: Optional[bool] = None
    timestamp: Optional[str] = None
    ip_address: Optional[str] = None
    device_fingerprint: Optional[str] = None
    merchant_category: Optional[str] = None
    country_code: Optional[str] = None


@strawberry.type
class InvestigationType:
    id: str
    transaction_id: str
    status: Optional[str] = None
    verdict: Optional[str] = None
    confidence: Optional[float] = None
    recommendation: Optional[str] = None
    created_at: Optional[str] = None
    approval_decision: Optional[str] = None


@strawberry.type
class MCPResult:
    success: bool
    data: Optional[str] = None
    error: Optional[str] = None


@strawberry.type
class Query:
    @strawberry.field
    async def transactions(
        self,
        limit: int = 50,
        offset: int = 0,
        is_fraud: Optional[bool] = None,
        type: Optional[str] = None,
    ) -> list[TransactionType]:
        async with AsyncSessionLocal() as db:
            query = select(Transaction).order_by(Transaction.transaction_id)
            if is_fraud is not None:
                query = query.where(Transaction.is_fraud == is_fraud)
            if type:
                query = query.where(Transaction.type == type)
            result = await db.execute(query.offset(offset).limit(limit))
            rows = result.scalars().all()
            return [
                TransactionType(
                    transaction_id=t.transaction_id,
                    step=t.step,
                    type=t.type,
                    amount=t.amount,
                    name_orig=t.name_orig,
                    name_dest=t.name_dest,
                    old_balance_org=t.old_balance_org,
                    new_balance_orig=t.new_balance_orig,
                    is_fraud=t.is_fraud,
                    is_flagged_fraud=t.is_flagged_fraud,
                    timestamp=t.timestamp.isoformat() if t.timestamp else None,
                    ip_address=t.ip_address,
                    device_fingerprint=t.device_fingerprint,
                    merchant_category=t.merchant_category,
                    country_code=t.country_code,
                )
                for t in rows
            ]

    @strawberry.field
    async def transaction(self, transaction_id: str) -> Optional[TransactionType]:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Transaction).where(Transaction.transaction_id == transaction_id)
            )
            t = result.scalar_one_or_none()
            if not t:
                return None
            return TransactionType(
                transaction_id=t.transaction_id,
                step=t.step,
                type=t.type,
                amount=t.amount,
                name_orig=t.name_orig,
                name_dest=t.name_dest,
                old_balance_org=t.old_balance_org,
                new_balance_orig=t.new_balance_orig,
                is_fraud=t.is_fraud,
                is_flagged_fraud=t.is_flagged_fraud,
                timestamp=t.timestamp.isoformat() if t.timestamp else None,
                ip_address=t.ip_address,
                device_fingerprint=t.device_fingerprint,
                merchant_category=t.merchant_category,
                country_code=t.country_code,
            )

    @strawberry.field
    async def investigations(self, limit: int = 20) -> list[InvestigationType]:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Investigation).order_by(Investigation.created_at.desc()).limit(limit)
            )
            rows = result.scalars().all()
            return [
                InvestigationType(
                    id=inv.id,
                    transaction_id=inv.transaction_id,
                    status=inv.status,
                    verdict=inv.verdict,
                    confidence=inv.confidence,
                    recommendation=inv.recommendation,
                    created_at=inv.created_at.isoformat() if inv.created_at else None,
                    approval_decision=inv.approval_decision,
                )
                for inv in rows
            ]

    @strawberry.field
    async def investigation(self, id: str) -> Optional[InvestigationType]:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Investigation).where(Investigation.id == id)
            )
            inv = result.scalar_one_or_none()
            if not inv:
                return None
            return InvestigationType(
                id=inv.id,
                transaction_id=inv.transaction_id,
                status=inv.status,
                verdict=inv.verdict,
                confidence=inv.confidence,
                recommendation=inv.recommendation,
                created_at=inv.created_at.isoformat() if inv.created_at else None,
                approval_decision=inv.approval_decision,
            )


@strawberry.type
class Mutation:
    @strawberry.mutation
    async def mcp_transaction_lookup(self, transaction_id: str) -> MCPResult:
        try:
            result = await transaction_lookup(transaction_id)
            import json
            return MCPResult(success=True, data=json.dumps(result))
        except Exception as e:
            return MCPResult(success=False, error=str(e))

    @strawberry.mutation
    async def mcp_customer_profile(self, customer_id: str) -> MCPResult:
        try:
            result = await customer_profile(customer_id)
            import json
            return MCPResult(success=True, data=json.dumps(result))
        except Exception as e:
            return MCPResult(success=False, error=str(e))

    @strawberry.mutation
    async def mcp_similar_fraud_search(
        self, transaction_id: Optional[str] = None, k: int = 10
    ) -> MCPResult:
        try:
            result = await similar_fraud_search(transaction_id=transaction_id, k=k)
            import json
            return MCPResult(success=True, data=json.dumps(result))
        except Exception as e:
            return MCPResult(success=False, error=str(e))

    @strawberry.mutation
    async def mcp_rules_engine(self, transaction_id: str) -> MCPResult:
        try:
            result = await rules_engine(transaction_id)
            import json
            return MCPResult(success=True, data=json.dumps(result))
        except Exception as e:
            return MCPResult(success=False, error=str(e))

    @strawberry.mutation
    async def mcp_evidence_writer(
        self,
        investigation_id: str,
        verdict: str,
        recommendation: str,
        evidence: Optional[str] = None,
    ) -> MCPResult:
        try:
            import json
            ev = json.loads(evidence) if evidence else {}
            result = await evidence_writer(investigation_id, verdict, ev, recommendation)
            return MCPResult(success=True, data=json.dumps(result))
        except Exception as e:
            return MCPResult(success=False, error=str(e))


schema = strawberry.Schema(query=Query, mutation=Mutation)
