"""MCP Tool Server: Transaction Lookup — retrieves a transaction row from Postgres."""

from sqlalchemy import select
from app.db import AsyncSessionLocal, Transaction


async def transaction_lookup(transaction_id: str) -> dict:
    """Look up a single transaction by ID from Postgres."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Transaction).where(Transaction.transaction_id == transaction_id)
        )
        t = result.scalar_one_or_none()

        if not t:
            return {"error": f"Transaction {transaction_id} not found", "found": False}

        return {
            "found": True,
            "transaction": {
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
            },
        }
