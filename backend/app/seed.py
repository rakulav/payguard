"""Seed script: loads dataset, computes embeddings, populates Postgres+pgvector, Qdrant, and OpenSearch."""

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import text

# Add project root to path for data module
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.config import get_settings
from app.db import sync_engine, Base, Transaction, SyncSession

settings = get_settings()

DATA_DIR = (
    Path("/app/data")
    if Path("/app/data").exists()
    else Path(__file__).parent.parent.parent / "data"
)
PARQUET_PATH = DATA_DIR / "transactions.parquet"
BATCH_SIZE = 1000


def wait_for_services():
    """Wait for Postgres, Qdrant, OpenSearch to be ready."""
    import time

    # Postgres
    for i in range(30):
        try:
            with sync_engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            print("  ✓ Postgres ready")
            break
        except Exception:
            time.sleep(2)
    else:
        print("  ✗ Postgres not ready after 60s")

    # Qdrant
    from qdrant_client import QdrantClient

    for i in range(30):
        try:
            client = QdrantClient(
                host=settings.qdrant_host, port=settings.qdrant_port, timeout=5
            )
            client.get_collections()
            print("  ✓ Qdrant ready")
            break
        except Exception:
            time.sleep(2)
    else:
        print("  ✗ Qdrant not ready after 60s")

    # OpenSearch
    from opensearchpy import OpenSearch

    for i in range(30):
        try:
            os_client = OpenSearch(
                hosts=[
                    {"host": settings.opensearch_host, "port": settings.opensearch_port}
                ],
                use_ssl=False,
                verify_certs=False,
                timeout=5,
            )
            os_client.cluster.health()
            print("  ✓ OpenSearch ready")
            break
        except Exception:
            time.sleep(2)
    else:
        print("  ✗ OpenSearch not ready after 60s")


def generate_data():
    """Run the dataset fetcher/generator."""
    sys.path.insert(0, str(DATA_DIR.parent))
    from data.fetch_or_generate import main as fetch_main

    fetch_main()


def create_tables():
    """Create database tables with pgvector extension."""
    from sqlalchemy import text as sa_text

    with sync_engine.connect() as conn:
        conn.execute(sa_text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.commit()
    Base.metadata.create_all(sync_engine)
    print("  ✓ Database tables created")


def check_seeded() -> bool:
    """Check if data is already seeded."""
    try:
        with SyncSession() as session:
            result = session.execute(text("SELECT COUNT(*) FROM transactions"))
            count = result.scalar()
            if count and count > 100000:
                print(f"  ✓ Already seeded: {count} transactions in Postgres")
                return True
    except Exception:
        pass
    return False


def load_embeddings_model():
    """Load the fastembed model (ONNX-based, no PyTorch)."""
    print("  Loading embedding model (bge-small-en-v1.5 via fastembed)...")
    from app.embeddings import get_model

    model = get_model()
    print("  ✓ Model loaded")
    return model


def transaction_to_text(row) -> str:
    """Convert a dataframe row to text for embedding."""
    return (
        f"{row.get('type', 'UNKNOWN')} of ${row.get('amount', 0):,.2f} "
        f"from {row.get('nameOrig', 'unknown')} to {row.get('nameDest', 'unknown')} "
        f"at {row.get('merchant_category', 'unknown')} merchant "
        f"in {row.get('country_code', 'XX')}"
    )


def seed_postgres(df: pd.DataFrame, embeddings: np.ndarray):
    """Seed transactions into Postgres with pgvector embeddings."""
    print(f"  Seeding Postgres ({len(df)} rows)...")
    start = time.time()

    with SyncSession() as session:
        # Clear existing data
        session.execute(text("TRUNCATE TABLE transactions"))
        session.commit()

        for i in range(0, len(df), BATCH_SIZE):
            batch = df.iloc[i : i + BATCH_SIZE]
            batch_embeddings = embeddings[i : i + BATCH_SIZE]

            for j, (_, row) in enumerate(batch.iterrows()):
                emb = batch_embeddings[j].tolist()
                txn = Transaction(
                    transaction_id=row["transaction_id"],
                    step=int(row.get("step", 0)),
                    type=row.get("type", "UNKNOWN"),
                    amount=float(row.get("amount", 0)),
                    name_orig=row.get("nameOrig", ""),
                    old_balance_org=float(row.get("oldbalanceOrg", 0)),
                    new_balance_orig=float(row.get("newbalanceOrig", 0)),
                    name_dest=row.get("nameDest", ""),
                    old_balance_dest=float(row.get("oldbalanceDest", 0)),
                    new_balance_dest=float(row.get("newbalanceDest", 0)),
                    is_fraud=bool(row.get("isFraud", False)),
                    is_flagged_fraud=bool(row.get("isFlaggedFraud", False)),
                    timestamp=(
                        pd.Timestamp(row["timestamp"]).to_pydatetime()
                        if "timestamp" in row
                        else None
                    ),
                    ip_address=row.get("ip_address", ""),
                    device_fingerprint=row.get("device_fingerprint", ""),
                    merchant_category=row.get("merchant_category", ""),
                    country_code=row.get("country_code", ""),
                    embedding=emb,
                )
                session.add(txn)

            session.commit()
            pct = min((i + BATCH_SIZE) / len(df) * 100, 100)
            print(f"\r  Postgres: {pct:.0f}%", end="", flush=True)

    elapsed = time.time() - start
    print(f"\n  ✓ Postgres seeded in {elapsed:.1f}s")


def seed_qdrant(df: pd.DataFrame, embeddings: np.ndarray):
    """Seed transaction embeddings into Qdrant with payload filtering."""
    print(f"  Seeding Qdrant ({len(df)} rows)...")
    start = time.time()

    from qdrant_client import QdrantClient
    from qdrant_client.models import PointStruct
    from app.retrieval.qdrant_store import ensure_collection, COLLECTION_NAME

    client = QdrantClient(
        host=settings.qdrant_host, port=settings.qdrant_port, timeout=60
    )

    # Delete and recreate
    try:
        client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass
    ensure_collection(client)

    for i in range(0, len(df), BATCH_SIZE):
        batch = df.iloc[i : i + BATCH_SIZE]
        batch_embeddings = embeddings[i : i + BATCH_SIZE]

        points = []
        for j, (_, row) in enumerate(batch.iterrows()):
            points.append(
                PointStruct(
                    id=i + j,
                    vector=batch_embeddings[j].tolist(),
                    payload={
                        "transaction_id": row["transaction_id"],
                        "type": row.get("type", ""),
                        "amount": float(row.get("amount", 0)),
                        "name_orig": row.get("nameOrig", ""),
                        "name_dest": row.get("nameDest", ""),
                        "is_fraud": bool(row.get("isFraud", False)),
                        "merchant_category": row.get("merchant_category", ""),
                        "country_code": row.get("country_code", ""),
                    },
                )
            )

        client.upsert(collection_name=COLLECTION_NAME, points=points)
        pct = min((i + BATCH_SIZE) / len(df) * 100, 100)
        print(f"\r  Qdrant: {pct:.0f}%", end="", flush=True)

    elapsed = time.time() - start
    print(f"\n  ✓ Qdrant seeded in {elapsed:.1f}s")


def seed_opensearch(df: pd.DataFrame):
    """Seed structured fields into OpenSearch for BM25 search."""
    print(f"  Seeding OpenSearch ({len(df)} rows)...")
    start = time.time()

    from app.retrieval.opensearch_store import ensure_index, INDEX_NAME, get_os_client

    client = get_os_client()

    # Delete and recreate
    try:
        client.indices.delete(INDEX_NAME)
    except Exception:
        pass
    ensure_index(client)

    for i in range(0, len(df), BATCH_SIZE):
        batch = df.iloc[i : i + BATCH_SIZE]
        actions = []

        for _, row in batch.iterrows():
            text_repr = transaction_to_text(row)
            doc = {
                "transaction_id": row["transaction_id"],
                "type": row.get("type", ""),
                "amount": float(row.get("amount", 0)),
                "name_orig": row.get("nameOrig", ""),
                "name_dest": row.get("nameDest", ""),
                "is_fraud": bool(row.get("isFraud", False)),
                "is_flagged_fraud": bool(row.get("isFlaggedFraud", False)),
                "merchant_category": row.get("merchant_category", ""),
                "country_code": row.get("country_code", ""),
                "ip_address": row.get("ip_address", ""),
                "device_fingerprint": row.get("device_fingerprint", ""),
                "text_repr": text_repr,
            }
            if "timestamp" in row and pd.notna(row["timestamp"]):
                doc["timestamp"] = pd.Timestamp(row["timestamp"]).isoformat()

            actions.append(
                {"index": {"_index": INDEX_NAME, "_id": row["transaction_id"]}}
            )
            actions.append(doc)

        if actions:
            client.bulk(body=actions, refresh=False)

        pct = min((i + BATCH_SIZE) / len(df) * 100, 100)
        print(f"\r  OpenSearch: {pct:.0f}%", end="", flush=True)

    client.indices.refresh(INDEX_NAME)
    elapsed = time.time() - start
    print(f"\n  ✓ OpenSearch seeded in {elapsed:.1f}s")


def main():
    print("=" * 50)
    print("PayGuard Data Seed")
    print("=" * 50)

    print("\n→ Waiting for services...")
    wait_for_services()

    print("\n→ Creating database tables...")
    create_tables()

    if check_seeded():
        print("\n✓ Data already seeded. Skipping.")
        return

    print("\n→ Generating/loading dataset...")
    generate_data()

    if not PARQUET_PATH.exists():
        print(f"  ✗ {PARQUET_PATH} not found!")
        return

    df = pd.read_parquet(PARQUET_PATH)
    print(f"  ✓ Loaded {len(df)} rows from {PARQUET_PATH}")

    print("\n→ Computing embeddings...")
    load_embeddings_model()
    from app.embeddings import embed_texts

    texts = [transaction_to_text(row) for _, row in df.iterrows()]
    # Batch embed in chunks to avoid OOM
    all_embeddings = []
    batch_size = 2000
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        batch_emb = embed_texts(batch)
        all_embeddings.append(batch_emb)
        pct = min((i + batch_size) / len(texts) * 100, 100)
        print(f"\r  Embeddings: {pct:.0f}%", end="", flush=True)
    print()
    import numpy as np

    embeddings = np.concatenate(all_embeddings, axis=0)
    print(f"  ✓ Computed {len(embeddings)} embeddings ({embeddings.shape[1]}d)")

    print("\n→ Seeding Postgres + pgvector...")
    seed_postgres(df, embeddings)

    print("\n→ Seeding Qdrant...")
    seed_qdrant(df, embeddings)

    print("\n→ Seeding OpenSearch...")
    seed_opensearch(df)

    print("\n" + "=" * 50)
    print("✓ All stores seeded successfully!")
    print(f"  Postgres: {len(df)} transactions + embeddings")
    print(f"  Qdrant: {len(df)} vectors")
    print(f"  OpenSearch: {len(df)} documents")
    print("=" * 50)


if __name__ == "__main__":
    main()
