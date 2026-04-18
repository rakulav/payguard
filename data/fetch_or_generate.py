"""
PayGuard Dataset Fetcher/Generator

Downloads PaySim from Hugging Face or generates synthetic 250K-row dataset.
Idempotent: skips if data/transactions.parquet exists with correct row count.
"""

import hashlib
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from faker import Faker

TARGET_ROWS = 250_000
FRAUD_RATE = 0.015
DATA_DIR = Path(__file__).parent
PARQUET_PATH = DATA_DIR / "transactions.parquet"
SCENARIOS_PATH = DATA_DIR / "fraud_scenarios.json"
PAYSIM_URL = "https://huggingface.co/datasets/pierre-loic/paysim/resolve/main/PS_20174392719_1491204439457_log.csv"
MAX_RETRIES = 3


def download_paysim() -> pd.DataFrame | None:
    """Attempt to download PaySim from Hugging Face with retries."""
    try:
        import requests
    except ImportError:
        print("  requests not available, skipping download")
        return None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            print(f"  Download attempt {attempt}/{MAX_RETRIES}...")
            resp = requests.get(PAYSIM_URL, stream=True, timeout=60)
            resp.raise_for_status()

            total = int(resp.headers.get("content-length", 0))
            chunks = []
            downloaded = 0
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                chunks.append(chunk)
                downloaded += len(chunk)
                if total > 0:
                    pct = downloaded * 100 / total
                    print(f"\r  Downloaded {downloaded / 1e6:.1f}MB / {total / 1e6:.1f}MB ({pct:.0f}%)", end="", flush=True)
            print()

            csv_path = DATA_DIR / "paysim_raw.csv"
            with open(csv_path, "wb") as f:
                for chunk in chunks:
                    f.write(chunk)

            df = pd.read_csv(csv_path)
            os.remove(csv_path)

            if len(df) < 100:
                print(f"  Downloaded file too small ({len(df)} rows), retrying...")
                continue

            print(f"  ✓ Downloaded PaySim: {len(df)} rows")
            return df

        except Exception as e:
            wait = 2 ** attempt
            print(f"  Attempt {attempt} failed: {e}. Retrying in {wait}s...")
            time.sleep(wait)

    print("  ✗ All download attempts failed")
    return None


def enrich_paysim(df: pd.DataFrame) -> pd.DataFrame:
    """Add extra columns to PaySim data to match our enriched schema."""
    fake = Faker()
    Faker.seed(42)
    np.random.seed(42)
    n = len(df)

    df = df.head(TARGET_ROWS).copy() if len(df) > TARGET_ROWS else df.copy()
    n = len(df)

    df["transaction_id"] = [f"TXN_{i}" for i in range(n)]
    base_ts = pd.Timestamp("2024-01-01")
    df["timestamp"] = [base_ts + pd.Timedelta(hours=int(step)) for step in df["step"]]
    df["ip_address"] = [fake.ipv4() for _ in range(n)]
    df["device_fingerprint"] = [hashlib.md5(f"dev_{i}_{np.random.randint(0, 100)}".encode()).hexdigest()[:16] for i in range(n)]

    categories = ["grocery", "electronics", "travel", "dining", "atm", "online_retail", "utilities", "gaming"]
    df["merchant_category"] = np.random.choice(categories, n)

    countries = ["US", "US", "US", "US", "GB", "DE", "FR", "NG", "CN", "RU", "BR", "IN"]
    df["country_code"] = np.random.choice(countries, n)

    return df


def generate_synthetic() -> pd.DataFrame:
    """Generate 250K synthetic transactions with injected fraud patterns."""
    print("  Generating synthetic dataset (250K rows)...")
    fake = Faker()
    Faker.seed(42)
    np.random.seed(42)
    n = TARGET_ROWS

    types = ["PAYMENT", "TRANSFER", "CASH_OUT", "DEBIT", "CASH_IN"]
    type_weights = [0.35, 0.25, 0.20, 0.10, 0.10]
    categories = ["grocery", "electronics", "travel", "dining", "atm", "online_retail", "utilities", "gaming"]
    countries = ["US", "US", "US", "US", "GB", "DE", "FR", "NG", "CN", "RU", "BR", "IN"]

    txn_types = np.random.choice(types, n, p=type_weights)
    amounts = np.round(np.abs(np.random.lognormal(mean=5, sigma=2, size=n)), 2)
    amounts = np.clip(amounts, 0.01, 500_000)

    old_balances = np.round(np.abs(np.random.lognormal(mean=8, sigma=1.5, size=n)), 2)
    new_balances = np.maximum(old_balances - amounts, 0)
    dest_old = np.round(np.abs(np.random.lognormal(mean=7, sigma=2, size=n)), 2)
    dest_new = dest_old + amounts

    customers_orig = [f"C_{np.random.randint(1, 5000)}" for _ in range(n)]
    customers_dest = [f"M_{np.random.randint(1, 10000)}" for _ in range(n)]

    df = pd.DataFrame({
        "step": np.random.randint(1, 744, n),
        "type": txn_types,
        "amount": amounts,
        "nameOrig": customers_orig,
        "oldbalanceOrg": old_balances,
        "newbalanceOrig": new_balances,
        "nameDest": customers_dest,
        "oldbalanceDest": dest_old,
        "newbalanceDest": dest_new,
        "isFraud": 0,
        "isFlaggedFraud": 0,
        "transaction_id": [f"TXN_{i}" for i in range(n)],
        "timestamp": pd.date_range("2024-01-01", periods=n, freq="2min"),
        "ip_address": [fake.ipv4() for _ in range(n)],
        "device_fingerprint": [hashlib.md5(f"dev_{i}_{np.random.randint(0, 100)}".encode()).hexdigest()[:16] for i in range(n)],
        "merchant_category": np.random.choice(categories, n),
        "country_code": np.random.choice(countries, n),
    })

    n_fraud = int(n * FRAUD_RATE)
    fraud_indices = np.random.choice(n, n_fraud, replace=False)

    patterns = ["rapid_micro", "round_cashout", "new_device_country", "balance_drain", "merchant_drift"]
    pattern_assignments = np.random.choice(patterns, n_fraud)

    for idx, pattern in zip(fraud_indices, pattern_assignments):
        df.loc[idx, "isFraud"] = 1

        if pattern == "rapid_micro":
            df.loc[idx, "amount"] = round(np.random.uniform(0.50, 5.00), 2)
            df.loc[idx, "type"] = "PAYMENT"
        elif pattern == "round_cashout":
            df.loc[idx, "amount"] = float(np.random.choice([1000, 2000, 5000, 10000]))
            df.loc[idx, "type"] = "CASH_OUT"
        elif pattern == "new_device_country":
            df.loc[idx, "country_code"] = np.random.choice(["NG", "RU", "CN"])
            df.loc[idx, "device_fingerprint"] = hashlib.md5(f"new_dev_{idx}".encode()).hexdigest()[:16]
        elif pattern == "balance_drain":
            df.loc[idx, "amount"] = float(df.loc[idx, "oldbalanceOrg"])
            df.loc[idx, "newbalanceOrig"] = 0.0
            df.loc[idx, "type"] = "TRANSFER"
        elif pattern == "merchant_drift":
            df.loc[idx, "merchant_category"] = np.random.choice(["gaming", "crypto", "gambling"])

    flag_mask = (df["isFraud"] == 1) & (df["amount"] > 5000)
    df.loc[flag_mask, "isFlaggedFraud"] = 1

    # Ensure TXN_48213 exists and is a fraud (for demo scenario)
    if 48213 < n:
        df.loc[48213, "transaction_id"] = "TXN_48213"
        df.loc[48213, "isFraud"] = 1
        df.loc[48213, "type"] = "TRANSFER"
        df.loc[48213, "amount"] = 12000.0
        df.loc[48213, "oldbalanceOrg"] = 12000.0
        df.loc[48213, "newbalanceOrig"] = 0.0
        df.loc[48213, "country_code"] = "RU"
        df.loc[48213, "device_fingerprint"] = "new_dev_48213abc"
        df.loc[48213, "merchant_category"] = "crypto"
        df.loc[48213, "isFlaggedFraud"] = 1
        df.loc[48213, "nameOrig"] = "C_1042"
        df.loc[48213, "nameDest"] = "M_8891"

    print(f"  ✓ Generated {n} rows, {df['isFraud'].sum()} fraudulent ({df['isFraud'].mean()*100:.1f}%)")
    return df


def generate_fraud_scenarios(df: pd.DataFrame) -> None:
    """Generate 20 investigation scenarios from the dataset."""
    fraud_df = df[df["isFraud"] == 1].copy()
    if len(fraud_df) < 20:
        fraud_df = df.head(20).copy()

    patterns_map = {
        "TRANSFER": "balance_drain",
        "CASH_OUT": "round_cashout",
        "PAYMENT": "rapid_micro",
    }

    scenarios = []

    # Scenario 1 is always TXN_48213
    scenarios.append({
        "id": "scn_01",
        "question": "Why was transaction TXN_48213 flagged?",
        "transaction_id": "TXN_48213",
        "expected_verdict": "fraud",
        "expected_pattern": "balance_drain",
    })

    sample = fraud_df[fraud_df["transaction_id"] != "TXN_48213"].sample(n=min(19, len(fraud_df) - 1), random_state=42)

    for i, (_, row) in enumerate(sample.iterrows(), start=2):
        txn_id = row["transaction_id"]
        pattern = patterns_map.get(row["type"], "new_device_country")
        questions = [
            f"Why was transaction {txn_id} flagged?",
            f"Investigate transaction {txn_id} for potential fraud.",
            f"Is transaction {txn_id} fraudulent? Explain the evidence.",
            f"Analyze the risk profile of transaction {txn_id}.",
        ]
        scenarios.append({
            "id": f"scn_{i:02d}",
            "question": questions[(i - 2) % len(questions)],
            "transaction_id": txn_id,
            "expected_verdict": "fraud",
            "expected_pattern": pattern,
        })

    with open(SCENARIOS_PATH, "w") as f:
        json.dump(scenarios, f, indent=2)
    print(f"  ✓ Generated {len(scenarios)} fraud scenarios → {SCENARIOS_PATH}")


def main():
    print("PayGuard Dataset Setup")
    print("=" * 40)

    if PARQUET_PATH.exists():
        existing = pd.read_parquet(PARQUET_PATH)
        if len(existing) >= TARGET_ROWS:
            print(f"✓ Dataset exists: {len(existing)} rows in {PARQUET_PATH}")
            if not SCENARIOS_PATH.exists():
                generate_fraud_scenarios(existing)
            return
        print(f"  Dataset exists but only {len(existing)} rows, regenerating...")

    # Try downloading PaySim
    print("\n→ Attempting PaySim download from Hugging Face...")
    df = download_paysim()

    if df is not None:
        print("→ Enriching PaySim with additional columns...")
        df = enrich_paysim(df)
        source = "paysim_huggingface"
    else:
        print("\n→ Falling back to synthetic generation...")
        df = generate_synthetic()
        source = "synthetic_faker_numpy"

    df.to_parquet(PARQUET_PATH, index=False)
    print(f"\n✓ Saved {len(df)} rows → {PARQUET_PATH}")
    print(f"  Source: {source}")

    generate_fraud_scenarios(df)

    decisions_path = DATA_DIR.parent / "DECISIONS.md"
    if decisions_path.exists():
        with open(decisions_path, "a") as f:
            f.write(f"\n\n## D009: Dataset source at runtime\n\n")
            f.write(f"**Date:** {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}\n")
            f.write(f"**Decision:** Used `{source}` for dataset.\n")
            f.write(f"**Rows:** {len(df)}, Fraud: {df['isFraud'].sum()}\n")


if __name__ == "__main__":
    main()
