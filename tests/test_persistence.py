from datetime import UTC, datetime

from app.persistence import DurableCaseStore
from app.schemas import (
    SAR,
    AnswerFile,
    CaseRecord,
    InvestigationResponse,
    NextBestActions,
    Pattern,
)


def response(revision: int) -> InvestigationResponse:
    answer = AnswerFile(
        case_id="HHG-001",
        case=CaseRecord(status="open", verdict="uncertain", fraud_probability=.4, pattern=Pattern.none, exposure_usd=0, summary="pending"),
        next_best_actions=NextBestActions(), sar=SAR(file=False, reason="pending"), stop_reason="pending",
        tool_calls=1, tokens=0, latency_s=.1,
    )
    return InvestigationResponse(
        investigation_id=f"investigation-{revision}", answer=answer,
        created_at=datetime.now(UTC), revision=revision, phase="awaiting_evidence",
    )


def test_revisions_survive_a_new_store_instance(tmp_path):
    url = f"sqlite:///{tmp_path / 'cases.db'}"
    first_store = DurableCaseStore(url)
    first_store.save(key="initial", request_hash="a" * 64, response=response(1))
    first_store.save(key="reply", request_hash="b" * 64, response=response(2))

    restarted_store = DurableCaseStore(url)
    assert restarted_store.by_key("initial").revision == 1
    assert [item.revision for item in restarted_store.history("HHG-001")] == [1, 2]
    assert restarted_store.latest("HHG-001").revision == 2


def test_service_uses_persisted_revision_after_restart(tmp_path):
    import threading
    from datetime import timedelta

    from app.config import Settings
    from app.graph import LocalGraphStore
    from app.investigator import Investigator
    from app.repository import BenchmarkCase, DatasetRepository, Transaction
    from app.service import InvestigationService
    from app.worker import InvestigationWorker

    now = datetime(2016, 11, 12, 12)  # noqa: DTZ001 — source timestamps are timezone-unspecified.
    repo = DatasetRepository(tmp_path)
    tx = Transaction("T1", "C1", "C1-K1", 90, "W", "in_person", now, 0.4, "1", "87", "", "")
    repo._transactions = {"T1": tx}; repo._by_card = {"C1-K1": [tx]}; repo._identities = {}; repo._history = []
    repo._benchmarks = {"HHG-001": BenchmarkCase("HHG-001", now + timedelta(hours=1), "risk_score", "Review", "T1", "C1-K1", "C1", 0.4)}
    database_url = f"sqlite:///{tmp_path / 'service.db'}"

    def make_service():
        service = InvestigationService.__new__(InvestigationService)
        service.settings = Settings(data_dir=tmp_path, graph_cases_path=tmp_path / "cases.jsonl", database_url=database_url)
        service.repository = repo; service.graph = LocalGraphStore(tmp_path / "cases.jsonl")
        service.investigator = Investigator(repo, service.graph); service.worker = InvestigationWorker(service.investigator, repo)
        service.persistence = DurableCaseStore(database_url); service._lock = threading.Lock()
        service._by_key = {}; service._by_case = {}; service._history = {}; service._approvals = {}
        return service

    first = make_service().investigate("HHG-001", idempotency_key="initial")
    second = make_service().investigate("HHG-001", "confirmed", idempotency_key="reply")
    assert (first.revision, second.revision) == (1, 2)
