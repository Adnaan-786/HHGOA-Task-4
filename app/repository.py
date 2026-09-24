from __future__ import annotations

import csv
import json
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path


@dataclass(frozen=True)
class Transaction:
    transaction_id: str
    customer_id: str
    card_id: str
    amount_usd: float
    product_code: str
    channel: str
    timestamp: datetime
    risk_score: float
    addr1: str
    addr2: str
    purchaser_email_domain: str
    recipient_email_domain: str

    @property
    def amount_cents(self) -> int:
        value = Decimal(str(self.amount_usd)) * 100
        if value != value.to_integral_value():
            raise ValueError(f"Sub-cent transaction amount: {self.transaction_id}")
        return int(value)


@dataclass(frozen=True)
class Identity:
    transaction_id: str
    device_profile: str
    device_type: str
    device_info: str
    device_newness: str
    proxy: str
    os: str
    browser: str
    screen: str
    match_status: str


@dataclass(frozen=True)
class HistoricalCase:
    case_id: str
    customer_id: str
    card_id: str
    opened_at: datetime
    closed_at: datetime
    outcome: str
    pattern: str
    txn_ids: tuple[str, ...]
    exposure_usd: float
    connected_card_ids: tuple[str, ...]
    actions_taken: tuple[str, ...]
    report_filed: bool
    analyst_notes: str


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    opened_at: datetime
    trigger_type: str
    trigger_text: str
    flagged_txn_id: str
    card_id: str
    customer_id: str
    risk_score: float | None


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


class DatasetRepository:
    """Read-only local source repository used by the pilot and benchmark runner.

    The transaction file contains many opaque Vesta columns. Runtime queries retain
    the documented investigation fields while the original files remain immutable.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self._transactions: dict[str, Transaction] | None = None
        self._by_card: dict[str, list[Transaction]] | None = None
        self._identities: dict[str, Identity] | None = None
        self._history: list[HistoricalCase] | None = None
        self._benchmarks: dict[str, BenchmarkCase] | None = None
        self._device_to_cards: dict[str, set[str]] | None = None

    def _load_transactions(self) -> None:
        if self._transactions is not None:
            return
        pairs: set[tuple[str, str]] = set()
        raw: list[dict[str, str]] = []
        with (self.root / "transactions.csv").open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                selected = {key: row.get(key, "") for key in (
                    "TransactionID", "TransactionAmt", "ProductCD", "customer_id", "ts",
                    "channel", "risk_score", "addr1", "addr2", "P_emaildomain", "R_emaildomain",
                    "card1", "card2", "card3", "card4", "card5", "card6",
                )}
                raw.append(selected)
                pairs.add((selected["customer_id"], selected["card6"]))
        card_map: dict[tuple[str, str], str] = {}
        by_customer: dict[str, list[str]] = defaultdict(list)
        for customer, card6 in sorted(pairs):
            by_customer[customer].append(card6)
        for customer, values in by_customer.items():
            for number, card6 in enumerate(values, 1):
                card_map[(customer, card6)] = f"{customer}-K{number}"
        frozen_path = self.root / "runtime" / "normalized" / "card-mapping.json"
        if frozen_path.exists():
            frozen_rows = json.loads(frozen_path.read_text())
            frozen = {(row["customer_id"], row["card6"]): row["card_id"] for row in frozen_rows}
            if frozen != card_map or len(frozen_rows) != len(frozen):
                raise ValueError("Source card keys differ from the frozen dataset mapping; canonical IDs are required for a new feed")
            card_map = frozen
        transactions: dict[str, Transaction] = {}
        by_card: dict[str, list[Transaction]] = defaultdict(list)
        for row in raw:
            tx = Transaction(
                transaction_id=row["TransactionID"], customer_id=row["customer_id"],
                card_id=card_map[(row["customer_id"], row["card6"])],
                amount_usd=float(row["TransactionAmt"] or 0), product_code=row["ProductCD"],
                channel=row["channel"], timestamp=_dt(row["ts"]),
                risk_score=float(row["risk_score"] or 0), addr1=row["addr1"], addr2=row["addr2"],
                purchaser_email_domain=row["P_emaildomain"], recipient_email_domain=row["R_emaildomain"],
            )
            transactions[tx.transaction_id] = tx
            by_card[tx.card_id].append(tx)
        for values in by_card.values():
            values.sort(key=lambda item: item.timestamp)
        self._transactions, self._by_card = transactions, dict(by_card)

    @property
    def transactions(self) -> dict[str, Transaction]:
        self._load_transactions()
        assert self._transactions is not None
        return self._transactions

    @property
    def by_card(self) -> dict[str, list[Transaction]]:
        self._load_transactions()
        assert self._by_card is not None
        return self._by_card

    @property
    def identities(self) -> dict[str, Identity]:
        if self._identities is None:
            result: dict[str, Identity] = {}
            path = self.root / "identity.csv"
            if path.exists():
                with path.open(newline="", encoding="utf-8") as fh:
                    for row in csv.DictReader(fh):
                        parts = [row.get("DeviceInfo", ""), row.get("id_30", ""), row.get("id_31", ""), row.get("id_33", "")]
                        profile = " | ".join(parts) if any(parts) else ""
                        result[row["TransactionID"]] = Identity(
                            transaction_id=row["TransactionID"], device_profile=profile,
                            device_type=row.get("DeviceType", ""), device_info=row.get("DeviceInfo", ""),
                            device_newness=row.get("id_15", ""), proxy=row.get("id_23", ""),
                            os=row.get("id_30", ""), browser=row.get("id_31", ""),
                            screen=row.get("id_33", ""), match_status=row.get("id_34", ""),
                        )
            self._identities = result
        return self._identities

    @property
    def history(self) -> list[HistoricalCase]:
        if self._history is None:
            result: list[HistoricalCase] = []
            with (self.root / "closed_cases_history.csv").open(newline="", encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    result.append(HistoricalCase(
                        case_id=row["case_id"], customer_id=row["customer_id"], card_id=row["card_id"],
                        opened_at=_dt(row["opened_at"]), closed_at=_dt(row["closed_at"]),
                        outcome=row["outcome"], pattern=row["pattern"],
                        txn_ids=tuple(x for x in row["txn_ids"].split("|") if x),
                        exposure_usd=float(row["exposure_usd"] or 0),
                        connected_card_ids=tuple(x for x in row["connected_card_ids"].split("|") if x),
                        actions_taken=tuple(x for x in row["actions_taken"].split("|") if x),
                        report_filed=row["report_filed"].lower() == "yes", analyst_notes=row["analyst_notes"],
                    ))
            self._history = result
        return self._history

    @property
    def benchmarks(self) -> dict[str, BenchmarkCase]:
        if self._benchmarks is None:
            result: dict[str, BenchmarkCase] = {}
            with (self.root / "case_pack.csv").open(newline="", encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    result[row["case_id"]] = BenchmarkCase(
                        case_id=row["case_id"], opened_at=_dt(row["opened_at"]), trigger_type=row["trigger_type"],
                        trigger_text=row["trigger_text"], flagged_txn_id=row["flagged_txn_id"],
                        card_id=row["card_id"], customer_id=row["customer_id"],
                        risk_score=float(row["risk_score"]) if row["risk_score"] else None,
                    )
            self._benchmarks = result
        return self._benchmarks

    def history_available_before(self, at: datetime) -> list[HistoricalCase]:
        return [item for item in self.history if item.closed_at <= at]

    def similar_cases(self, *, customer_id: str, pattern: str, at: datetime, limit: int = 3, exclude_case_id: str | None = None) -> list[HistoricalCase]:
        candidates = sorted(
            (item for item in self.history_available_before(at) if item.case_id != exclude_case_id),
            key=lambda item: (item.closed_at, item.case_id), reverse=True,
        )
        exact = [item for item in candidates if item.customer_id == customer_id]
        same_pattern = [item for item in candidates if item.pattern == pattern]
        cleared = [item for item in candidates if item.outcome != "confirmed_fraud"]
        result: list[HistoricalCase] = []
        # Include a cleared counterexample; this is local retrieval, not vector search.
        for item in exact[:1] + same_pattern[:1] + cleared[:1] + exact[1:] + same_pattern[1:]:
            if item.case_id not in {x.case_id for x in result}:
                result.append(item)
            if len(result) >= limit:
                break
        return result

    def device_neighbors(self, transaction_ids: Iterable[str], cutoff: datetime) -> tuple[set[str], set[str]]:
        profiles: set[str] = set()
        cards: set[str] = set()
        for transaction_id in transaction_ids:
            identity = self.identities.get(transaction_id)
            tx = self.transactions.get(transaction_id)
            if not identity or not identity.device_profile or not tx or not cutoff - timedelta(days=7) <= tx.timestamp <= cutoff:
                continue
            profiles.add(identity.device_profile)
        if not profiles:
            return profiles, cards
        for transaction_id, identity in self.identities.items():
            if identity.device_profile not in profiles:
                continue
            tx = self.transactions.get(transaction_id)
            if tx and cutoff - timedelta(days=7) <= tx.timestamp <= cutoff:
                cards.add(tx.card_id)
        return profiles, cards
