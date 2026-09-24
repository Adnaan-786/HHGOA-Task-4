from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from app.answer import validate_answer
from app.graph import LocalGraphStore
from app.investigator import Investigator
from app.repository import BenchmarkCase, DatasetRepository, Identity, Transaction


@pytest.fixture
def repository(tmp_path):
    repo = DatasetRepository(tmp_path)
    now = datetime(2016, 11, 12, 12)  # noqa: DTZ001 — source timestamps have no supplied timezone.
    tx = Transaction("T1", "C1", "C1-K1", 90, "W", "in_person", now, 0.4, "1", "87", "", "")
    repo._transactions = {tx.transaction_id: tx}
    repo._by_card = {tx.card_id: [tx]}
    repo._identities = {}
    repo._history = []
    repo._benchmarks = {"HHG-001": BenchmarkCase("HHG-001", now + timedelta(hours=1), "risk_score", "Review", "T1", "C1-K1", "C1", 0.4)}
    return repo


def run(repo, tmp_path, response=None, store=None):
    return Investigator(repo, store or LocalGraphStore(tmp_path / "cases.jsonl")).run("HHG-001", response)


def test_local_storage_is_not_tigergraph(repository, tmp_path):
    answer = run(repository, tmp_path)
    assert not answer.case.written_to_graph
    assert answer.case.graph_case_id == ""
    assert all(e.source != "graph" for e in answer.case.evidence)
    validate_answer(answer, repository)


def test_confirmed_simulation_preserves_initial_and_zeroes_projection(repository, tmp_path):
    store = LocalGraphStore(tmp_path / "cases.jsonl")
    answer = run(repository, tmp_path, "confirmed", store)
    initial = {a.action for a in answer.next_best_actions.initial}
    final = {a.action for a in answer.next_best_actions.final}
    assert "VERIFY_WITH_CUSTOMER" in initial
    assert "CLOSE_NO_FRAUD" not in initial
    assert "CLOSE_NO_FRAUD" in final
    assert answer.case.affected_txn_ids == []
    assert answer.case.exposure_usd == 0
    assert answer.evidence_requests[0].simulated
    assert "Simulated response" in answer.evidence_requests[0].assumed_response
    assert store.read_case("CASE-HHG-001")["exposure_usd"] == 0
    validate_answer(answer, repository)


def test_denial_updates_actions_without_rewriting_initial(repository, tmp_path):
    answer = run(repository, tmp_path, "denied")
    assert "BLOCK_CARD" not in {a.action for a in answer.next_best_actions.initial}
    assert "BLOCK_CARD" in {a.action for a in answer.next_best_actions.final}
    assert answer.case.status == "closed_fraud"
    validate_answer(answer, repository)


def test_conflicting_response_remains_escalated(repository, tmp_path):
    repository._benchmarks["HHG-001"] = replace(repository._benchmarks["HHG-001"], trigger_type="customer_report", trigger_text="I did not make this transaction")
    answer = run(repository, tmp_path, "confirmed")
    assert answer.case.status == "escalated"
    assert answer.case.verdict == "uncertain"
    assert "CLOSE_NO_FRAUD" not in {a.action for a in answer.next_best_actions.final}
    assert "ESCALATE_TO_ANALYST" in {a.action for a in answer.next_best_actions.final}
    validate_answer(answer, repository)


def test_graph_readback_mismatch_never_claims_persistence(repository, tmp_path):
    class FailedReadback(LocalGraphStore):
        is_tigergraph = True

        def read_case(self, graph_case_id):
            return {"status": "stale"}

    answer = run(repository, tmp_path, store=FailedReadback(tmp_path / "graph.jsonl"))
    assert not answer.case.written_to_graph
    assert not answer.case.graph_case_id


def test_graph_readback_verification_contract(repository, tmp_path):
    # A test double checks application behavior; it is not a live integration test.
    class VerifiedGraphDouble(LocalGraphStore):
        is_tigergraph = True

    answer = run(repository, tmp_path, store=VerifiedGraphDouble(tmp_path / "graph.jsonl"))
    assert answer.case.written_to_graph
    assert answer.case.graph_case_id == "CASE-HHG-001"


def test_submission_excludes_internal_metadata(repository, tmp_path):
    answer = run(repository, tmp_path, "denied")
    submitted = answer.submission_dict()
    assert all(set(e) == {"claim", "source", "ref", "entity_ids"} for e in submitted["case"]["evidence"])
    assert set(submitted["evidence_requests"][0]) == {"type", "asked_after_step", "assumed_response"}


def test_device_candidates_have_seven_day_cutoff(repository):
    tx = repository.transactions["T1"]
    identity = Identity("T1", "Phone | OS | Browser | screen", "mobile", "Phone", "New", "", "OS", "Browser", "screen", "")
    repository._identities = {"T1": identity}
    for tid, age in [("old", 8), ("recent", 1), ("future", -1)]:
        other = replace(tx, transaction_id=tid, card_id=tid, timestamp=tx.timestamp - timedelta(days=age))
        repository._transactions[tid] = other
        repository._identities[tid] = replace(identity, transaction_id=tid)
    _, cards = repository.device_neighbors(["T1"], tx.timestamp)
    assert cards == {"C1-K1", "recent"}


def test_duplicate_transactions_fail_validation(repository, tmp_path):
    answer = run(repository, tmp_path)
    answer.case.affected_txn_ids.append("T1")
    with pytest.raises(ValueError, match="unique"):
        validate_answer(answer, repository)


def test_unsettled_authentication_does_not_close_case(repository, tmp_path):
    repository._benchmarks["HHG-001"] = replace(repository._benchmarks["HHG-001"], risk_score=0.9)
    answer = run(repository, tmp_path, "authentication_succeeded")
    assert answer.case.status != "closed_fraud"


@pytest.mark.parametrize("within_hour", [True, False])
def test_testing_sequence_is_bounded_to_one_hour(repository, tmp_path, within_hour):
    purchase = replace(repository.transactions["T1"], channel="online", amount_usd=150)
    repository._transactions["T1"] = purchase
    sequence = [purchase]
    for index in range(3):
        small = replace(purchase, transaction_id=f"small-{index}", amount_usd=1,
                        timestamp=purchase.timestamp - timedelta(minutes=(index + 1) * (10 if within_hour else 90)))
        repository._transactions[small.transaction_id] = small
        sequence.append(small)
    repository._by_card[purchase.card_id] = sorted(sequence, key=lambda tx: tx.timestamp)
    answer = run(repository, tmp_path)
    assert (answer.case.pattern == "card_testing") is within_hour
    validate_answer(answer, repository)
