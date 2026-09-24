from datetime import datetime, timedelta

from app.config import Settings
from app.graph import LocalGraphStore
from app.repository import BenchmarkCase, DatasetRepository, Transaction
from app.schemas import ApprovalRequest
from app.service import InvestigationService


def repository(tmp_path):
    repo = DatasetRepository(tmp_path)
    now = datetime(2016, 11, 12, 12)  # noqa: DTZ001 — source timestamps are timezone-unspecified.
    tx = Transaction("T1", "C1", "C1-K1", 90, "W", "in_person", now, 0.4, "1", "87", "", "")
    repo._transactions = {"T1": tx}
    repo._by_card = {"C1-K1": [tx]}
    repo._identities = {}
    repo._history = []
    repo._benchmarks = {
        "HHG-001": BenchmarkCase("HHG-001", now + timedelta(hours=1), "risk_score", "Review", "T1", "C1-K1", "C1", 0.4)
    }
    return repo


def test_interactive_run_waits_for_evidence_and_marks_request_pending(tmp_path):
    repo = repository(tmp_path)
    answer = __import__("app.investigator", fromlist=["Investigator"]).Investigator(
        repo, LocalGraphStore(tmp_path / "cases.jsonl")
    ).run("HHG-001", simulation=False)

    assert answer.case.status == "open"
    assert answer.evidence_requests
    assert answer.evidence_requests[0].simulated is False
    assert "Pending authenticated" in answer.evidence_requests[0].assumed_response
    assert "pending" in answer.stop_reason


def test_service_keeps_initial_revision_when_response_arrives(tmp_path):
    repo = repository(tmp_path)
    settings = Settings(data_dir=tmp_path, graph_cases_path=tmp_path / "cases.jsonl")
    service = InvestigationService.__new__(InvestigationService)
    service.settings = settings
    service.repository = repo
    service.graph = LocalGraphStore(tmp_path / "cases.jsonl")
    from app.investigator import Investigator
    from app.worker import InvestigationWorker
    service.investigator = Investigator(repo, service.graph)
    service.worker = InvestigationWorker(service.investigator, repo)
    import threading
    service._lock = threading.Lock()
    service._by_key = {}
    service._by_case = {}
    service._history = {}
    service._approvals = {}
    service.persistence = None

    first = service.investigate("HHG-001", idempotency_key="initial")
    second = service.investigate("HHG-001", "confirmed", idempotency_key="reply")

    assert first.revision == 1
    assert first.phase == "awaiting_evidence"
    assert second.revision == 2
    assert second.phase == "completed"
    assert len(service.history("HHG-001")) == 2
    assert second.answer.next_best_actions.initial == first.answer.next_best_actions.initial
    assert second.answer.case.verdict == "legitimate"


def test_approval_is_revision_bound_and_read_only(tmp_path):
    repo = repository(tmp_path)
    service = InvestigationService.__new__(InvestigationService)
    service.settings = Settings(data_dir=tmp_path, graph_cases_path=tmp_path / "cases.jsonl")
    service.repository = repo
    service.graph = LocalGraphStore(tmp_path / "cases.jsonl")
    from app.investigator import Investigator
    from app.worker import InvestigationWorker
    service.investigator = Investigator(repo, service.graph)
    service.worker = InvestigationWorker(service.investigator, repo)
    import threading
    service._lock = threading.Lock(); service._by_key = {}; service._by_case = {}; service._history = {}; service._approvals = {}; service.persistence = None

    current = service.investigate("HHG-001", idempotency_key="initial")
    action = current.answer.next_best_actions.final[0]
    approval = service.approve("HHG-001", ApprovalRequest(action=action.action, revision=1, role="L1", decision="approved"))
    assert approval.execution_status == "simulated_only"
    assert service.approvals("HHG-001")[0].approval_id == approval.approval_id
    service.investigate("HHG-001", "confirmed", idempotency_key="reply")
    try:
        service.approve("HHG-001", ApprovalRequest(action=action.action, revision=1, role="L1", decision="approved", idempotency_key="stale-attempt"))
    except ValueError as exc:
        assert "stale" in str(exc)
    else:
        raise AssertionError("stale approval was accepted")
