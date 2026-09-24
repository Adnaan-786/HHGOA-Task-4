import csv
import gzip
import json
from pathlib import Path

import pytest

from app.prepare import cents, prepare_dataset


def write_csv(root: Path, name: str, rows: list[dict]):
    with (root / name).open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "README.md").write_text("Original specification\n")
    first = {"TransactionID": "0001", "customer_id": "C1", "card6": "", "TransactionAmt": "-10.05",
             "ProductCD": "W", "channel": "online", "ts": "2016-11-12 10:00:00", "risk_score": "0.2",
             "addr1": "", "addr2": "", "P_emaildomain": "", "R_emaildomain": "", "V999": "opaque, original"}
    second = {**first, "TransactionID": "0002", "card6": "credit", "TransactionAmt": "0.30"}
    write_csv(root, "transactions.csv", [first, second])
    identity = {"TransactionID": "0001", "DeviceInfo": "", "id_30": "", "id_31": "", "id_33": "", "id_15": "", "id_23": "", "id_34": "", "opaque": "kept"}
    write_csv(root, "identity.csv", [identity, {**identity, "TransactionID": "0002", "id_30": "Linux"}])
    write_csv(root, "closed_cases_history.csv", [{"case_id": "CC-1", "customer_id": "C1", "card_id": "C1-K1",
        "opened_at": "2016-11-12 11:00:00", "closed_at": "2016-11-14 11:00:00", "txn_ids": "0001",
        "connected_card_ids": "", "outcome": "cleared", "pattern": "none", "exposure_usd": "0.00", "analyst_notes": "Observed, then cleared"}])
    write_csv(root, "case_pack.csv", [{"case_id": "HHG-001", "customer_id": "C1", "card_id": "C1-K2", "flagged_txn_id": "0002"}])
    return root


def test_normalization_preserves_all_columns_and_exact_values(source, tmp_path):
    package = prepare_dataset(source, tmp_path / "normalized")
    with gzip.open(package / "transactions.normalized.csv.gz", "rt", newline="") as archive:
        records = list(csv.DictReader(archive))
    assert records[0]["TransactionID"] == "0001"
    assert records[0]["V999"] == "opaque, original"
    assert records[0]["_hhgoa_amount_cents"] == "-1005"
    assert records[0]["ts"] == "2016-11-12 10:00:00"
    assert records[0]["_hhgoa_card_id"] == "C1-K1"
    assert records[1]["_hhgoa_card_id"] == "C1-K2"
    manifest = json.loads((package / "manifest.json").read_text())
    assert manifest["counts"]["validated_card_references"] == 2
    assert manifest["counts"]["transactions"] == 2
    assert (source / "README.md").read_bytes() == (package / "source-README.md").read_bytes()


def test_missing_profile_has_no_shared_vertex(source, tmp_path):
    package = prepare_dataset(source, tmp_path / "normalized")
    with (package / "device_profiles.csv").open() as stream:
        profiles = list(csv.DictReader(stream))
    assert len(profiles) == 1
    assert profiles[0]["profile"] == " | Linux |  | "
    with (package / "from_device.csv").open() as stream:
        edges = list(csv.DictReader(stream))
    assert [edge["transaction_id"] for edge in edges] == ["0002"]


def test_prepare_is_idempotent_and_checks_existing_outputs(source, tmp_path):
    output = tmp_path / "normalized"
    package = prepare_dataset(source, output)
    manifest = (package / "manifest.json").read_bytes()
    assert prepare_dataset(source, output) == package
    assert (package / "manifest.json").read_bytes() == manifest
    with (package / "cards.csv").open("a") as target:
        target.write("tampered\n")
    with pytest.raises(ValueError, match="artifact changed"):
        prepare_dataset(source, output)


def test_card_mapping_cannot_be_recomputed_after_freeze(source, tmp_path):
    output = tmp_path / "normalized"
    prepare_dataset(source, output)
    with (source / "transactions.csv").open() as stream:
        transactions = list(csv.DictReader(stream))
    transactions.append({**transactions[1], "TransactionID": "0003", "card6": "a-new-card-kind"})
    write_csv(source, "transactions.csv", transactions)
    with pytest.raises(ValueError, match="mapping is frozen"):
        prepare_dataset(source, output)


def test_orphan_identity_rejects_package(source, tmp_path):
    with (source / "identity.csv").open() as stream:
        identities = list(csv.DictReader(stream))
    identities[0]["TransactionID"] = "missing"
    write_csv(source, "identity.csv", identities)
    with pytest.raises(ValueError, match="orphan identity"):
        prepare_dataset(source, tmp_path / "normalized")
    assert not list((tmp_path / "normalized").glob("*/manifest.json"))


@pytest.mark.parametrize("amount", ["0.001", "NaN", "Infinity"])
def test_subcent_and_nonfinite_amounts_fail(amount):
    with pytest.raises(ValueError):
        cents(amount)
