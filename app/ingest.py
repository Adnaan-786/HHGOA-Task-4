from __future__ import annotations

import csv
import hashlib
import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path


def sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def validate_dataset(root: str | Path) -> dict:
    root = Path(root)
    transaction_count = 0
    transaction_ids: set[str] = set()
    customer_cards: set[tuple[str, str]] = set()
    customers: set[str] = set()
    with (root / "transactions.csv").open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        transaction_columns = reader.fieldnames or []
        for row in reader:
            transaction_count += 1
            transaction_ids.add(row["TransactionID"])
            customers.add(row["customer_id"])
            customer_cards.add((row["customer_id"], row["card6"]))
    identity_ids: set[str] = set()
    identity_count = 0
    with (root / "identity.csv").open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            identity_count += 1
            identity_ids.add(row["TransactionID"])
    history_count = 0
    history_txn_ids: set[str] = set()
    expected_card_refs: dict[str, str] = {}
    history_patterns: dict[str, int] = defaultdict(int)
    with (root / "closed_cases_history.csv").open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            history_count += 1
            history_patterns[row["pattern"]] += 1
            for transaction_id in filter(None, row["txn_ids"].split("|")):
                history_txn_ids.add(transaction_id)
                expected_card_refs[transaction_id] = row["card_id"]
    benchmark_count = 0
    benchmark_txn_ids: set[str] = set()
    with (root / "case_pack.csv").open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            benchmark_count += 1
            benchmark_txn_ids.add(row["flagged_txn_id"])
            expected_card_refs[row["flagged_txn_id"]] = row["card_id"]
    by_customer: dict[str, list[str]] = defaultdict(list)
    for customer, card6 in sorted(customer_cards):
        by_customer[customer].append(card6)
    card_map = {(customer, card6): f"{customer}-K{index}"
                for customer, values in by_customer.items()
                for index, card6 in enumerate(values, 1)}
    card_mismatches: list[dict[str, str]] = []
    if expected_card_refs:
        with (root / "transactions.csv").open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                expected = expected_card_refs.get(row["TransactionID"])
                if expected and card_map[(row["customer_id"], row["card6"])] != expected:
                    card_mismatches.append({"transaction_id": row["TransactionID"], "expected": expected})
    files = {}
    for name in ("README.md", "transactions.csv", "identity.csv", "closed_cases_history.csv", "case_pack.csv"):
        path = root / name
        files[name] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    manifest = {
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "source": "HHGOA_IEEE",
        "files": files,
        "counts": {"transactions": transaction_count, "identity": len(identity_ids), "closed_cases": history_count, "benchmark_cases": benchmark_count, "customers": len(customers)},
        "columns": {"transactions": transaction_columns},
        "checks": {
            "duplicate_transaction_ids": transaction_count - len(transaction_ids),
            "duplicate_identity_ids": identity_count - len(identity_ids),
            "identity_orphans": len(identity_ids - transaction_ids),
            "missing_history_transactions": len(history_txn_ids - transaction_ids),
            "missing_benchmark_transactions": len(benchmark_txn_ids - transaction_ids),
            "card_mapping_mismatches": len(card_mismatches),
        },
        "history_patterns": dict(history_patterns),
    }
    return manifest


def write_manifest(root: str | Path, output: str | Path | None = None) -> Path:
    root = Path(root)
    destination = Path(output) if output else root / "runtime" / "manifest.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    manifest = validate_dataset(root)
    destination.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return destination
