from __future__ import annotations

import hashlib
import json
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

from .config import Settings
from .graph import LocalGraphStore, TigerGraphMCPStore
from .investigator import Investigator
from .model import OpenRouterChatModel
from .persistence import DurableCaseStore
from .repository import DatasetRepository
from .schemas import ApprovalRequest, ApprovalResult, InvestigationResponse
from .worker import InvestigationWorker


class InvestigationService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.repository = DatasetRepository(settings.data_dir)
        if settings.graph_backend == "tigergraph":
            self.graph = TigerGraphMCPStore(settings.tigergraph_mcp_url, settings.tigergraph_graph_name)
        else:
            self.graph = LocalGraphStore(settings.cases_path)
        model = OpenRouterChatModel(settings.openrouter_api_key, settings.model, settings.openrouter_base_url) if settings.openrouter_api_key else None
        self.investigator = Investigator(self.repository, self.graph, model=model)
        self.worker = InvestigationWorker(self.investigator, self.repository)
        self.persistence = DurableCaseStore(settings.database_url) if settings.database_url else None
        self._lock = threading.Lock()
        self._by_key: dict[str, InvestigationResponse] = {}
        self._by_case: dict[str, InvestigationResponse] = {}
        self._history: dict[str, list[InvestigationResponse]] = {}
        self._approvals: dict[str, ApprovalResult] = {}

    def investigate(self, case_id: str, evidence_response: str | None = None, idempotency_key: str | None = None, *, simulate_timeout: bool = False) -> InvestigationResponse:
        key = idempotency_key or hashlib.sha256(f"{case_id}:{evidence_response}:{simulate_timeout}".encode()).hexdigest()
        if getattr(self, "persistence", None):
            persisted = self.persistence.by_key(key)
            if persisted:
                return persisted
        with self._lock:
            if key in self._by_key:
                return self._by_key[key]
            if getattr(self, "persistence", None):
                persisted = self.persistence.by_key(key)
                if persisted:
                    self._by_key[key] = persisted
                    self._by_case[case_id] = persisted
                    return persisted
            prior_revisions = self.history(case_id) if getattr(self, "persistence", None) else self._history.get(case_id, [])
            revision = len(prior_revisions) + 1
            answer = self.worker.run(case_id, evidence_response, simulation=simulate_timeout, revision=revision)
            response = InvestigationResponse(
                investigation_id=str(uuid.uuid4()), answer=answer, created_at=datetime.now(UTC),
                revision=revision,
                phase="awaiting_evidence" if answer.evidence_requests and not simulate_timeout and evidence_response is None else "completed",
            )
            self._by_key[key] = response
            self._by_case[case_id] = response
            self._history.setdefault(case_id, []).append(response)
            if getattr(self, "persistence", None):
                self.persistence.save(
                    key=key,
                    request_hash=hashlib.sha256(f"{case_id}:{evidence_response}:{simulate_timeout}".encode()).hexdigest(),
                    response=response,
                )
            return response

    def get(self, case_id: str) -> InvestigationResponse | None:
        persistence = getattr(self, "persistence", None)
        return self._by_case.get(case_id) or (persistence.latest(case_id) if persistence else None)

    def history(self, case_id: str) -> list[InvestigationResponse]:
        persistence = getattr(self, "persistence", None)
        if persistence:
            return persistence.history(case_id)
        return list(self._history.get(case_id, []))

    def list_cases(self) -> list[dict]:
        result = []
        for case_id, benchmark in self.repository.benchmarks.items():
            current = self.get(case_id)
            result.append({
                "case_id": case_id, "opened_at": benchmark.opened_at,
                "trigger_type": benchmark.trigger_type, "trigger_text": benchmark.trigger_text,
                "status": current.answer.case.status if current else "uninvestigated",
                "verdict": current.answer.case.verdict if current else None,
                "revision": current.revision if current else 0,
                "phase": current.phase if current else "uninvestigated",
            })
        return result

    def approve(self, case_id: str, request: ApprovalRequest) -> ApprovalResult:
        key = request.idempotency_key or hashlib.sha256(f"{case_id}:{request.action.value}:{request.revision}:{request.role}:{request.decision}".encode()).hexdigest()
        persistence = getattr(self, "persistence", None)
        if persistence:
            saved = persistence.approval_by_key(key)
            if saved:
                return saved
        with self._lock:
            if key in self._approvals:
                return self._approvals[key]
            current = self.get(case_id)
            if current is None:
                raise ValueError("Investigation has not been started")
            if request.revision != current.revision:
                raise ValueError(f"Approval revision {request.revision} is stale; current revision is {current.revision}")
            recommendation = next((item for item in current.answer.next_best_actions.final if item.action == request.action), None)
            if recommendation is None:
                raise ValueError("Action is not recommended for the current case revision")
            if request.role == "L1" and recommendation.route.value == "L2":
                raise ValueError("L1 cannot approve an L2 action")
            result = ApprovalResult(
                approval_id=str(uuid.uuid4()), case_id=case_id, action=request.action,
                revision=request.revision, role=request.role, decision=request.decision,
                created_at=datetime.now(UTC),
            )
            self._approvals[key] = result
            if persistence:
                persistence.save_approval(key=key, result=result)
            return result

    def approvals(self, case_id: str) -> list[ApprovalResult]:
        persistence = getattr(self, "persistence", None)
        if persistence:
            return persistence.approvals_for(case_id)
        return [value for value in self._approvals.values() if value.case_id == case_id]

    def export_answers(self, output_dir: str | Path) -> int:
        path = Path(output_dir)
        path.mkdir(parents=True, exist_ok=True)
        written = 0
        for case_id in self.repository.benchmarks:
            response = self._by_case.get(case_id) or self.investigate(case_id, simulate_timeout=True)
            (path / f"{case_id}.json").write_text(json.dumps(response.answer.submission_dict(), indent=2), encoding="utf-8")
            written += 1
        return written
