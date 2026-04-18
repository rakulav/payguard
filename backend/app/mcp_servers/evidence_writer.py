"""MCP Tool Server: Evidence Writer — persists investigation results to Postgres."""

from datetime import datetime
from sqlalchemy import select

from app.audit_service import append_audit
from app.db import AsyncSessionLocal, Investigation


async def evidence_writer(
    investigation_id: str,
    verdict: str,
    evidence: dict,
    recommendation: str,
    confidence: float | None = None,
) -> dict:
    """Write investigation evidence and verdict to the investigations table.

    Triggers approval gate if recommendation is 'freeze' or 'escalate'.
    """
    requires_approval = recommendation in ("freeze", "escalate")
    conf = confidence if confidence is not None else float(evidence.get("triage_confidence") or 0.0)

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Investigation).where(Investigation.id == investigation_id)
        )
        inv = result.scalar_one_or_none()

        if inv:
            inv.verdict = verdict
            inv.evidence = evidence
            inv.recommendation = recommendation
            inv.confidence = conf
            inv.status = "pending_approval" if requires_approval else "completed"
            inv.updated_at = datetime.utcnow()
        else:
            inv = Investigation(
                id=investigation_id,
                transaction_id=evidence.get("transaction_id", "unknown"),
                verdict=verdict,
                confidence=conf,
                evidence=evidence,
                recommendation=recommendation,
                status="pending_approval" if requires_approval else "completed",
                created_at=datetime.utcnow(),
            )
            db.add(inv)

        await db.commit()

    await append_audit(
        investigation_id,
        "evidence_writer",
        "evidence_persisted",
        new_state={"verdict": verdict, "recommendation": recommendation, "confidence": conf},
    )

    return {
        "investigation_id": investigation_id,
        "verdict": verdict,
        "recommendation": recommendation,
        "status": "pending_approval" if requires_approval else "completed",
        "requires_approval": requires_approval,
        "message": f"Evidence written. {'Approval required before action.' if requires_approval else 'Investigation complete.'}",
    }
