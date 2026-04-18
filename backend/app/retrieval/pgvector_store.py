"""pgvector-backed vector search over transaction embeddings."""

from sqlalchemy import select, text
from app.db import AsyncSessionLocal, Transaction


async def search_similar(embedding: list[float], k: int = 10, filters: dict | None = None) -> list[dict]:
    """Search pgvector for similar transactions by embedding distance."""
    async with AsyncSessionLocal() as db:
        embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"

        where_clauses = ["embedding IS NOT NULL"]
        if filters:
            if "type" in filters:
                where_clauses.append(f"type = '{filters['type']}'")
            if "is_fraud" in filters:
                where_clauses.append(f"is_fraud = {'true' if filters['is_fraud'] else 'false'}")

        where_sql = " AND ".join(where_clauses)

        query = text(f"""
            SELECT transaction_id, type, amount, name_orig, name_dest, is_fraud,
                   merchant_category, country_code,
                   embedding <=> '{embedding_str}'::vector AS distance
            FROM transactions
            WHERE {where_sql}
            ORDER BY embedding <=> '{embedding_str}'::vector
            LIMIT :k
        """)

        result = await db.execute(query, {"k": k})
        rows = result.fetchall()

        return [
            {
                "transaction_id": row[0],
                "type": row[1],
                "amount": float(row[2]) if row[2] else 0,
                "name_orig": row[3],
                "name_dest": row[4],
                "is_fraud": bool(row[5]),
                "merchant_category": row[6],
                "country_code": row[7],
                "distance": float(row[8]) if row[8] else 999,
                "source": "pgvector",
            }
            for row in rows
        ]
