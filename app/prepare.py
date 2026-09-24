from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import tempfile
from contextlib import ExitStack
from datetime import datetime
from decimal import Decimal
from itertools import pairwise
from pathlib import Path

from .ingest import sha256

SOURCE_FILES = ("README.md", "transactions.csv", "identity.csv", "closed_cases_history.csv", "case_pack.csv")


def entity_id(prefix: str, components: list[str]) -> str:
    return prefix + hashlib.sha256(json.dumps(components, ensure_ascii=False).encode()).hexdigest()[:24]


def cents(value: str) -> int:
    number = Decimal(value) * 100
    if not number.is_finite() or number != number.to_integral_value():
        raise ValueError(f"Amount is not a finite exact number of cents: {value}")
    return int(number)


def prepare_dataset(root: Path, output: Path) -> Path:
    """Publish a checked, versioned load package without modifying the source files."""
    sources = {name: {"sha256": sha256(root / name), "bytes": (root / name).stat().st_size} for name in SOURCE_FILES}
    version = hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest()[:24]
    destination = output / version
    if destination.exists():
        manifest = json.loads((destination / "manifest.json").read_text())
        if manifest["sources"] != sources:
            raise ValueError("Existing package has different source fingerprints")
        for name, info in manifest["outputs"].items():
            if sha256(destination / name) != info["sha256"]:
                raise ValueError(f"Existing normalized artifact changed: {name}")
        return destination

    pairs = set()
    transaction_cards = {}
    with (root / "transactions.csv").open(newline="") as source:
        reader = csv.DictReader(source)
        for row in reader:
            tid = row["TransactionID"]
            if tid in transaction_cards:
                raise ValueError(f"Duplicate transaction ID: {tid}")
            pair = (row["customer_id"], row["card6"])
            pairs.add(pair)
            transaction_cards[tid] = pair
            datetime.fromisoformat(row["ts"])
            cents(row["TransactionAmt"])
    output.mkdir(parents=True, exist_ok=True)
    mapping_path = output / "card-mapping.json"
    if mapping_path.exists():
        mapping = json.loads(mapping_path.read_text())
        card_map = {(row["customer_id"], row["card6"]): row["card_id"] for row in mapping}
        if set(card_map) != pairs:
            raise ValueError("The dataset card mapping is frozen; changed card keys require canonical IDs and an explicit new ingestion configuration")
    else:
        card_map = {}
        previous_customer, number = None, 0
        for customer, card6 in sorted(pairs):
            number = number + 1 if customer == previous_customer else 1
            card_map[customer, card6] = f"{customer}-K{number}"
            previous_customer = customer
        mapping = [{"customer_id": customer, "card6": card6, "card_id": card}
                   for (customer, card6), card in sorted(card_map.items())]

    history, benchmarks, anchor_count = [], [], 0
    known_cards = set(card_map.values())
    for filename, target in (("closed_cases_history.csv", history), ("case_pack.csv", benchmarks)):
        seen = set()
        with (root / filename).open(newline="") as source:
            for row in csv.DictReader(source):
                if row["case_id"] in seen:
                    raise ValueError(f"Duplicate case ID in {filename}: {row['case_id']}")
                seen.add(row["case_id"])
                target.append(row)
                tids = row["txn_ids"].split("|") if filename == "closed_cases_history.csv" else [row["flagged_txn_id"]]
                for tid in filter(None, tids):
                    if tid not in transaction_cards:
                        raise ValueError(f"Unresolved case transaction: {tid}")
                    pair = transaction_cards[tid]
                    if card_map[pair] != row["card_id"] or pair[0] != row["customer_id"]:
                        raise ValueError(f"Case card/customer mismatch: {row['case_id']} / {tid}")
                    anchor_count += 1
                for card in filter(None, row.get("connected_card_ids", "").split("|")):
                    if card not in known_cards:
                        raise ValueError(f"Unknown connected card: {card}")

    with tempfile.TemporaryDirectory(prefix=".pending-", dir=output) as temporary:
        staging = Path(temporary) / "package"
        staging.mkdir()
        counts: dict[str, int] = {}
        with ExitStack() as stack:
            def writer(name: str, fields: list[str], compressed: bool = False):
                if compressed:
                    binary = stack.enter_context((staging / name).open("wb"))
                    zipped = stack.enter_context(gzip.GzipFile(fileobj=binary, mode="wb", mtime=0))
                    stream = stack.enter_context(io.TextIOWrapper(zipped, encoding="utf-8", newline=""))
                else:
                    stream = stack.enter_context((staging / name).open("w", encoding="utf-8", newline=""))
                csv_writer = csv.DictWriter(stream, fieldnames=fields)
                csv_writer.writeheader()
                counts[name] = 0

                def write(row):
                    csv_writer.writerow(row)
                    counts[name] += 1
                return write

            write_profile = writer("device_profiles.csv", ["id", "profile", "device_info", "os", "browser", "screen"])
            write_device_edge = writer("from_device.csv", ["transaction_id", "device_id"])
            identity_signals, identity_ids, profile_ids = {}, set(), set()
            with (root / "identity.csv").open(newline="") as source:
                reader = csv.DictReader(source)
                write_identity = writer("identity.normalized.csv.gz", [*(reader.fieldnames or []), "_hhgoa_profile_id", "_hhgoa_source_row"], True)
                for source_row, row in enumerate(reader, 2):
                    tid = row["TransactionID"]
                    if tid in identity_ids or tid not in transaction_cards:
                        raise ValueError(f"Duplicate or orphan identity: {tid}")
                    identity_ids.add(tid)
                    parts = [row.get(name, "") for name in ("DeviceInfo", "id_30", "id_31", "id_33")]
                    profile_id = entity_id("D-", parts) if any(parts) else ""
                    write_identity({**row, "_hhgoa_profile_id": profile_id, "_hhgoa_source_row": source_row})
                    identity_signals[tid] = (row.get("id_15", ""), row.get("id_23", ""), row.get("id_34", ""))
                    if profile_id:
                        write_device_edge({"transaction_id": tid, "device_id": profile_id})
                        if profile_id not in profile_ids:
                            write_profile(dict(zip(["id", "profile", "device_info", "os", "browser", "screen"], [profile_id, " | ".join(parts), *parts], strict=True)))
                            profile_ids.add(profile_id)
            write_customer = writer("customers.csv", ["id"])
            for customer in sorted({customer for customer, _ in pairs}):
                write_customer({"id": customer})
            write_card = writer("cards.csv", ["id", "customer_id", "card6"])
            for row in mapping:
                write_card({"id": row["card_id"], "customer_id": row["customer_id"], "card6": row["card6"]})

            transaction_fields = ["id", "customer_id", "card_id", "amount_cents", "product_code", "channel", "ts", "risk_score", "addr1", "addr2", "device_newness", "proxy", "match_status", "source_row", "source_version"]
            write_transaction = writer("transactions.graph.csv", transaction_fields)
            write_domain = writer("email_domains.csv", ["id", "domain"])
            write_purchaser = writer("purchaser_email.csv", ["transaction_id", "domain_id"])
            write_recipient = writer("recipient_email.csv", ["transaction_id", "domain_id"])
            write_region = writer("billing_regions.csv", ["id", "addr1", "addr2"])
            write_billing = writer("billed_in.csv", ["transaction_id", "region_id"])
            domains, regions, by_card = set(), set(), {}
            with (root / "transactions.csv").open(newline="") as source:
                reader = csv.DictReader(source)
                fields = [*(reader.fieldnames or []), "_hhgoa_card_id", "_hhgoa_amount_cents", "_hhgoa_source_row", "_hhgoa_source_version"]
                if len(fields) != len(set(fields)):
                    raise ValueError("Source columns collide with normalized metadata")
                write_archive = writer("transactions.normalized.csv.gz", fields, True)
                for source_row, row in enumerate(reader, 2):
                    tid = row["TransactionID"]
                    card = card_map[transaction_cards[tid]]
                    amount = cents(row["TransactionAmt"])
                    signals = identity_signals.get(tid, ("", "", ""))
                    write_archive({**row, "_hhgoa_card_id": card, "_hhgoa_amount_cents": amount, "_hhgoa_source_row": source_row, "_hhgoa_source_version": version})
                    write_transaction(dict(zip(transaction_fields, [tid, row["customer_id"], card, amount, row["ProductCD"], row["channel"], row["ts"], row["risk_score"], row.get("addr1", ""), row.get("addr2", ""), *signals, source_row, version], strict=True)))
                    by_card.setdefault(card, []).append((row["ts"], tid))
                    for column, write_edge in (("P_emaildomain", write_purchaser), ("R_emaildomain", write_recipient)):
                        domain = row.get(column, "")
                        if domain:
                            if domain not in domains:
                                write_domain({"id": domain, "domain": domain})
                                domains.add(domain)
                            write_edge({"transaction_id": tid, "domain_id": domain})
                    region = (row.get("addr1", ""), row.get("addr2", ""))
                    if any(region):
                        region_id = entity_id("R-", list(region))
                        if region not in regions:
                            write_region({"id": region_id, "addr1": region[0], "addr2": region[1]})
                            regions.add(region)
                        write_billing({"transaction_id": tid, "region_id": region_id})
            write_next = writer("transaction_sequence.csv", ["from_id", "to_id", "seconds"])
            for transactions in by_card.values():
                ordered = sorted(transactions)
                for first, second in pairwise(ordered):
                    seconds = int((datetime.fromisoformat(second[0]) - datetime.fromisoformat(first[0])).total_seconds())
                    write_next({"from_id": first[1], "to_id": second[1], "seconds": seconds})
            case_fields = ["id", "customer_id", "card_id", "opened_at", "available_at", "status", "verdict", "pattern", "exposure_cents", "source", "source_version", "narrative"]
            write_case = writer("historical_cases.csv", case_fields)
            write_involves = writer("case_transactions.csv", ["case_id", "transaction_id"])
            write_connected = writer("case_connections.csv", ["case_id", "card_id"])
            for row in history:
                fraudulent = row["outcome"] == "confirmed_fraud"
                write_case(dict(zip(case_fields, [row["case_id"], row["customer_id"], row["card_id"], row["opened_at"], row["closed_at"], "closed_fraud" if fraudulent else "closed_legitimate", "fraud" if fraudulent else "legitimate", row["pattern"], cents(row["exposure_usd"]), "confirmed_historical", version, row["analyst_notes"]], strict=True)))
                for tid in filter(None, row["txn_ids"].split("|")):
                    write_involves({"case_id": row["case_id"], "transaction_id": tid})
                for card in filter(None, row["connected_card_ids"].split("|")):
                    write_connected({"case_id": row["case_id"], "card_id": card})
        (staging / "card-mapping.json").write_text(json.dumps(mapping, sort_keys=True, indent=2) + "\n")
        (staging / "source-README.md").write_bytes((root / "README.md").read_bytes())
        if {name: sha256(root / name) for name in SOURCE_FILES} != {name: info["sha256"] for name, info in sources.items()}:
            raise ValueError("A source file changed during ingestion")
        manifest = {
            "version": version, "sources": sources,
            "counts": {"transactions": len(transaction_cards), "identity": len(identity_ids), "customers": len({c for c, _ in pairs}), "cards": len(card_map), "historical_cases": len(history), "benchmark_cases": len(benchmarks), "validated_card_references": anchor_count},
            "checks": {"duplicate_transaction_ids": 0, "duplicate_identity_ids": 0, "identity_orphans": 0, "unresolved_case_references": 0, "card_mapping_mismatches": 0},
            "transformations": ["all original transaction and identity columns retained", "amounts parsed as exact signed integer cents", "timezone-unspecified timestamps preserved", "dataset-specific sorted customer/card6 mapping frozen including blanks", "device profile uses four ordered components; all-missing profiles have no shared vertex", "identity newness/proxy flags belong to transactions, not shared device vertices"],
            "outputs": {file.name: {"sha256": sha256(file), "bytes": file.stat().st_size, "rows": counts.get(file.name)} for file in sorted(staging.iterdir())},
        }
        (staging / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
        if not mapping_path.exists():
            with mapping_path.open("x") as target:
                json.dump(mapping, target, sort_keys=True, indent=2)
                target.write("\n")
        staging.rename(destination)
    return destination
