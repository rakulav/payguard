"""Qdrant-backed vector search with payload filtering."""

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    VectorParams,
    PointStruct,
    Filter,
    FieldCondition,
    MatchValue,
)
from app.config import get_settings

settings = get_settings()
COLLECTION_NAME = "transactions"


def get_qdrant_client() -> QdrantClient:
    return QdrantClient(
        host=settings.qdrant_host, port=settings.qdrant_port, timeout=30
    )


def ensure_collection(client: QdrantClient | None = None):
    """Create collection if it doesn't exist."""
    client = client or get_qdrant_client()
    collections = [c.name for c in client.get_collections().collections]
    if COLLECTION_NAME not in collections:
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(
                size=settings.embedding_dim, distance=Distance.COSINE
            ),
        )


async def search_similar(
    embedding: list[float],
    k: int = 10,
    filters: dict | None = None,
) -> list[dict]:
    """Search Qdrant for similar transactions with optional payload filtering."""
    client = get_qdrant_client()

    query_filter = None
    if filters:
        must = []
        if "type" in filters:
            must.append(
                FieldCondition(key="type", match=MatchValue(value=filters["type"]))
            )
        if "is_fraud" in filters:
            must.append(
                FieldCondition(
                    key="is_fraud", match=MatchValue(value=filters["is_fraud"])
                )
            )
        if must:
            query_filter = Filter(must=must)

    results = client.search(
        collection_name=COLLECTION_NAME,
        query_vector=embedding,
        query_filter=query_filter,
        limit=k,
        with_payload=True,
    )

    return [
        {
            "transaction_id": hit.payload.get("transaction_id", ""),
            "type": hit.payload.get("type", ""),
            "amount": hit.payload.get("amount", 0),
            "name_orig": hit.payload.get("name_orig", ""),
            "name_dest": hit.payload.get("name_dest", ""),
            "is_fraud": hit.payload.get("is_fraud", False),
            "merchant_category": hit.payload.get("merchant_category", ""),
            "country_code": hit.payload.get("country_code", ""),
            "score": hit.score,
            "source": "qdrant",
        }
        for hit in results
    ]


def upsert_points(points: list[PointStruct], client: QdrantClient | None = None):
    """Batch upsert points to Qdrant."""
    client = client or get_qdrant_client()
    batch_size = 500
    for i in range(0, len(points), batch_size):
        batch = points[i : i + batch_size]
        client.upsert(collection_name=COLLECTION_NAME, points=batch)
