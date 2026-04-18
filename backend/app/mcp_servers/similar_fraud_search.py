"""MCP Tool Server: Similar Fraud Search — hybrid retrieval via pgvector + Qdrant + OpenSearch with RRF."""

from app.retrieval.hybrid import hybrid_search
from app.mcp_servers.transaction_lookup import transaction_lookup


def _embed_text(text: str) -> list[float] | None:
    """Embed text using fastembed."""
    try:
        from app.embeddings import embed_single

        return embed_single(text)
    except Exception:
        return None


def _transaction_to_text(txn: dict) -> str:
    """Convert a transaction dict to a text representation for embedding."""
    return (
        f"{txn.get('type', 'UNKNOWN')} of ${txn.get('amount', 0):,.2f} "
        f"from {txn.get('name_orig', 'unknown')} to {txn.get('name_dest', 'unknown')} "
        f"at {txn.get('merchant_category', 'unknown')} merchant "
        f"in {txn.get('country_code', 'XX')}"
    )


async def similar_fraud_search(
    transaction_id: str | None = None,
    embedding: list[float] | None = None,
    k: int = 10,
) -> dict:
    """Find similar fraud cases using hybrid retrieval (pgvector + Qdrant + OpenSearch, RRF-fused)."""
    query_text = ""

    if transaction_id and not embedding:
        txn_result = await transaction_lookup(transaction_id)
        if txn_result.get("found"):
            txn = txn_result["transaction"]
            query_text = _transaction_to_text(txn)
            embedding = _embed_text(query_text)

    if embedding is None:
        embedding = [0.0] * 384

    if not query_text:
        query_text = "fraud transaction suspicious transfer"

    result = await hybrid_search(
        embedding=embedding,
        query_text=query_text,
        k=k,
        filters=None,
    )

    return {
        "similar_transactions": result["results"],
        "total_candidates": result["total_candidates"],
        "retrieval_sources": result["sources"],
    }
