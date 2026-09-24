from __future__ import annotations

from decimal import Decimal

from .policy import route_for
from .repository import DatasetRepository
from .schemas import ActionName, AnswerFile


def validate_answer(answer: AnswerFile, repository: DatasetRepository) -> None:
    """Validate the benchmark contract before an answer leaves the service."""
    benchmark = repository.benchmarks.get(answer.case_id)
    if benchmark is None:
        raise ValueError(f"answer references unknown case {answer.case_id}")
    known_transactions = repository.transactions
    if len(set(answer.case.affected_txn_ids)) != len(answer.case.affected_txn_ids):
        raise ValueError("affected transaction IDs must be unique")
    for transaction_id in answer.case.affected_txn_ids:
        if transaction_id not in known_transactions:
            raise ValueError(f"answer references unknown transaction {transaction_id}")
        if known_transactions[transaction_id].timestamp > benchmark.opened_at:
            raise ValueError("affected transactions must be available at the investigation cutoff")
    expected_exposure = Decimal(sum(abs(known_transactions[transaction_id].amount_cents) for transaction_id in answer.case.affected_txn_ids)) / 100
    if expected_exposure != Decimal(str(answer.case.exposure_usd)):
        raise ValueError(f"exposure mismatch: expected {expected_exposure}, got {answer.case.exposure_usd}")
    if answer.case.verdict == "legitimate" and (answer.case.affected_txn_ids or answer.case.exposure_usd != 0):
        raise ValueError("legitimate cases must have no affected transactions and zero exposure")
    report_action = any(item.action == ActionName.FILE_REPORT for item in answer.next_best_actions.final)
    if report_action != answer.sar.file:
        raise ValueError("sar.file must agree with FILE_REPORT in final actions")
    if answer.case.verdict == "legitimate" and answer.sar.file:
        raise ValueError("legitimate cases cannot recommend a SAR")
    if not answer.sar.file and (answer.sar.narrative or answer.sar.subjects or answer.sar.total_amount_usd or answer.sar.activity_dates):
        raise ValueError("a non-filing SAR must have empty content")
    if answer.case.pattern != "undocumented" and answer.case.pattern_description:
        raise ValueError("pattern_description is only populated for undocumented patterns")
    if answer.case.affected_txn_ids:
        if benchmark.flagged_txn_id not in answer.case.affected_txn_ids:
            raise ValueError("the candidate episode must include the flagged transaction")
        earliest = min(answer.case.affected_txn_ids, key=lambda tid: (known_transactions[tid].timestamp, tid))
        if answer.case.first_suspicious_txn_id != earliest:
            raise ValueError("first suspicious transaction must be the earliest episode transaction")
    elif answer.case.first_suspicious_txn_id:
        raise ValueError("an empty episode has no first suspicious transaction")
    for action in answer.next_best_actions.final:
        if action.route != route_for(action.action, answer.case.exposure_usd):
            raise ValueError(f"incorrect final approval route for {action.action}")
    if answer.case.written_to_graph and not answer.case.graph_case_id:
        raise ValueError("graph persistence requires graph_case_id")
    if not answer.case.written_to_graph and answer.case.graph_case_id:
        raise ValueError("unverified graph persistence cannot claim a graph case ID")
