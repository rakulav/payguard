"""MCP Tool Server: Customer Profile — aggregated stats via OpenSearch aggregation."""

from app.retrieval.opensearch_store import get_customer_aggregation


async def customer_profile(customer_id: str) -> dict:
    """Get aggregated customer profile stats from OpenSearch."""
    try:
        profile = await get_customer_aggregation(customer_id)
        return {"found": True, "profile": profile}
    except Exception as e:
        return {
            "found": False,
            "error": str(e),
            "profile": {
                "customer_id": customer_id,
                "total_transactions": 0,
                "avg_amount": 0,
                "top_categories": [],
                "countries": [],
                "fraud_count": 0,
            },
        }
