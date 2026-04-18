"""Append-only audit trail for investigations."""

import uuid
from datetime import datetime
from typing import Any

from app.db import AsyncSessionLocal, AuditLog


async def append_audit(
    investigation_id: str,
    actor: str,
    action: str,
    *,
    prior_state: dict[str, Any] | None = None,
    new_state: dict[str, Any] | None = None,
    reason: str | None = None,
) -> None:
    """Persist one audit row for an investigation (append-only)."""
    row = AuditLog(
        id=f"aud_{uuid.uuid4().hex[:16]}",
        investigation_id=investigation_id,
        timestamp=datetime.utcnow(),
        actor=actor,
        action=action,
        prior_state=prior_state,
        new_state=new_state,
        reason=reason or "",
    )
    async with AsyncSessionLocal() as session:
        session.add(row)
        await session.commit()
