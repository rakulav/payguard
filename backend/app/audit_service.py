"""Append-only audit trail for investigations."""

import uuid
from datetime import datetime
from typing import Any, Optional

from app.db import AsyncSessionLocal, AuditLog


async def append_audit(
    investigation_id: str,
    actor: str,
    action: str,
    *,
    prior_state: Optional[dict[str, Any]] = None,
    new_state: Optional[dict[str, Any]] = None,
    reason: Optional[str] = None,
) -> None:
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
