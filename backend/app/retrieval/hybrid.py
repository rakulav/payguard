"""Hybrid retrieval: pgvector + Qdrant + OpenSearch with Reciprocal Rank Fusion."""

from app.retrieval import pgvector_store, qdrant_store, opensearch_store

RRF_K = 60


def reciprocal_rank_fusion(result_lists: list[list[dict]], k: int = RRF_K) -> list[dict]:
    """Fuse multiple ranked result lists using RRF with k=60."""
    scores: dict[str, float] = {}
    doc_map: dict[str, dict] = {}
    source_map: dict[str, list[str]] = {}

    for result_list in result_lists:
        for rank, doc in enumerate(result_list):
            tid = doc["transaction_id"]
            rrf_score = 1.0 / (k + rank + 1)
            scores[tid] = scores.get(tid, 0) + rrf_score
            doc_map[tid] = doc
            source_map.setdefault(tid, []).append(doc.get("source", "unknown"))

    sorted_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)

    results = []
    for tid in sorted_ids:
        doc = doc_map[tid].copy()
        doc["rrf_score"] = round(scores[tid], 6)
        doc["sources"] = list(set(source_map.get(tid, [])))
        results.append(doc)

    return results


async def hybrid_search(
    embedding: list[float],
    query_text: str,
    k: int = 10,
    filters: dict | None = None,
) -> dict:
    """Run hybrid search across all 3 backends and fuse with RRF."""
    pgvector_results = []
    qdrant_results = []
    opensearch_results = []

    try:
        pgvector_results = await pgvector_store.search_similar(embedding, k=k * 2, filters=filters)
    except Exception as e:
        pgvector_results = []

    try:
        qdrant_results = await qdrant_store.search_similar(embedding, k=k * 2, filters=filters)
    except Exception as e:
        qdrant_results = []

    try:
        opensearch_results = await opensearch_store.search_bm25(query_text, k=k * 2, filters=filters)
    except Exception as e:
        opensearch_results = []

    fused = reciprocal_rank_fusion([pgvector_results, qdrant_results, opensearch_results])

    return {
        "results": fused[:k],
        "total_candidates": len(fused),
        "sources": {
            "pgvector": len(pgvector_results),
            "qdrant": len(qdrant_results),
            "opensearch": len(opensearch_results),
        },
    }
