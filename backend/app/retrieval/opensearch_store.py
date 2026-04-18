"""OpenSearch-backed BM25 keyword search and aggregations."""

from opensearchpy import OpenSearch
from app.config import get_settings

settings = get_settings()
INDEX_NAME = "transactions"


def get_os_client() -> OpenSearch:
    return OpenSearch(
        hosts=[{"host": settings.opensearch_host, "port": settings.opensearch_port}],
        http_compress=True,
        use_ssl=False,
        verify_certs=False,
        timeout=30,
    )


def ensure_index(client: OpenSearch | None = None):
    """Create the transactions index if it doesn't exist."""
    client = client or get_os_client()
    if not client.indices.exists(INDEX_NAME):
        client.indices.create(
            index=INDEX_NAME,
            body={
                "settings": {
                    "number_of_shards": 1,
                    "number_of_replicas": 0,
                },
                "mappings": {
                    "properties": {
                        "transaction_id": {"type": "keyword"},
                        "type": {"type": "keyword"},
                        "amount": {"type": "float"},
                        "name_orig": {"type": "keyword"},
                        "name_dest": {"type": "keyword"},
                        "is_fraud": {"type": "boolean"},
                        "is_flagged_fraud": {"type": "boolean"},
                        "merchant_category": {"type": "keyword"},
                        "country_code": {"type": "keyword"},
                        "ip_address": {"type": "keyword"},
                        "device_fingerprint": {"type": "keyword"},
                        "timestamp": {"type": "date"},
                        "text_repr": {"type": "text"},
                    }
                },
            },
        )


async def search_bm25(query_text: str, k: int = 10, filters: dict | None = None) -> list[dict]:
    """BM25 keyword search over transaction text representations."""
    client = get_os_client()

    must = [{"match": {"text_repr": {"query": query_text, "fuzziness": "AUTO"}}}]
    filter_clauses = []
    if filters:
        if "type" in filters:
            filter_clauses.append({"term": {"type": filters["type"]}})
        if "is_fraud" in filters:
            filter_clauses.append({"term": {"is_fraud": filters["is_fraud"]}})

    body = {
        "size": k,
        "query": {
            "bool": {
                "must": must,
                "filter": filter_clauses,
            }
        },
    }

    result = client.search(index=INDEX_NAME, body=body)
    hits = result.get("hits", {}).get("hits", [])

    return [
        {
            "transaction_id": hit["_source"].get("transaction_id", ""),
            "type": hit["_source"].get("type", ""),
            "amount": hit["_source"].get("amount", 0),
            "name_orig": hit["_source"].get("name_orig", ""),
            "name_dest": hit["_source"].get("name_dest", ""),
            "is_fraud": hit["_source"].get("is_fraud", False),
            "merchant_category": hit["_source"].get("merchant_category", ""),
            "country_code": hit["_source"].get("country_code", ""),
            "score": hit.get("_score", 0),
            "source": "opensearch",
        }
        for hit in hits
    ]


async def get_customer_aggregation(customer_id: str) -> dict:
    """Aggregate customer stats from OpenSearch."""
    client = get_os_client()

    body = {
        "size": 0,
        "query": {"term": {"name_orig": customer_id}},
        "aggs": {
            "avg_amount": {"avg": {"field": "amount"}},
            "total_transactions": {"value_count": {"field": "transaction_id"}},
            "top_categories": {"terms": {"field": "merchant_category", "size": 5}},
            "countries": {"terms": {"field": "country_code", "size": 10}},
            "fraud_count": {"filter": {"term": {"is_fraud": True}}},
            "amount_stats": {"stats": {"field": "amount"}},
        },
    }

    result = client.search(index=INDEX_NAME, body=body)
    aggs = result.get("aggregations", {})

    return {
        "customer_id": customer_id,
        "total_transactions": aggs.get("total_transactions", {}).get("value", 0),
        "avg_amount": round(aggs.get("avg_amount", {}).get("value", 0) or 0, 2),
        "top_categories": [
            {"category": b["key"], "count": b["doc_count"]}
            for b in aggs.get("top_categories", {}).get("buckets", [])
        ],
        "countries": [
            {"country": b["key"], "count": b["doc_count"]}
            for b in aggs.get("countries", {}).get("buckets", [])
        ],
        "fraud_count": aggs.get("fraud_count", {}).get("doc_count", 0),
        "amount_min": aggs.get("amount_stats", {}).get("min", 0),
        "amount_max": aggs.get("amount_stats", {}).get("max", 0),
    }


def bulk_index(documents: list[dict], client: OpenSearch | None = None):
    """Bulk index documents into OpenSearch."""
    client = client or get_os_client()
    actions = []
    for doc in documents:
        actions.append({"index": {"_index": INDEX_NAME, "_id": doc["transaction_id"]}})
        actions.append(doc)

    batch_size = 2000
    for i in range(0, len(actions), batch_size):
        batch = actions[i:i + batch_size]
        client.bulk(body=batch, refresh=False)

    client.indices.refresh(INDEX_NAME)
